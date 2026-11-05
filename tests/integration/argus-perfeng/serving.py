"""Start and verify the disposable serving tier for the joint checkout flow."""

import argparse
import hashlib
import importlib
import json
import os
import secrets
import shlex
import shutil
import signal
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

from scripts import control_plane_release as release
from scripts import control_plane_runtime as runtime
from scripts import storage_access

fixture = importlib.import_module("tests.integration.argus-perfeng.fixture")

ROOT = Path(__file__).resolve().parents[3]
PLATFORM = "perf-platform"
GENERATORS = "perf-generators"
SUT = "perf-sut"
ISSUER = "https://perfeng.fixture.invalid"
ARGUS_ISSUER = "https://argus.fixture.invalid"
BUCKET = "perfeng-artifacts"
STORE_HOST = "seaweedfs.perf-platform.svc.cluster.local"
PYTHON_IMAGE = "python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"


def prove_readiness(evidence: dict, probe: Callable[[str], None]) -> None:
    for service in ("sample-sut", "seaweedfs", "argus", "perfeng", "product-http"):
        probe(service)
    evidence["servingReady"] = True


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_(api, kind, name, namespace=None, **fields):
    metadata = {"name": name}
    if namespace:
        metadata["namespace"] = namespace
    return {"apiVersion": api, "kind": kind, "metadata": metadata, **fields}


def pod(name, namespace, image, command, *, secret=None, paths=(), account=None, probe=None):
    mounts: list[dict[str, str | bool]] = [{"name": "scratch", "mountPath": "/tmp"}]
    container: dict[str, Any] = {
        "name": name,
        "image": image,
        "command": command,
        "workingDir": "/tmp",
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "resources": {
            "requests": {"cpu": "100m"},
            "limits": {"cpu": "1", "memory": "512Mi"},
        },
        "volumeMounts": mounts,
    }
    volumes = [{"name": "scratch", "emptyDir": {"sizeLimit": "256Mi"}}]
    for index, path in enumerate(paths):
        volume = f"fixture-{index}"
        volumes.append({"name": volume, "hostPath": {"path": f"/fixture/{path}"}})
        mounts.append({"name": volume, "mountPath": f"/fixture/{path}", "readOnly": True})
    if secret:
        container["envFrom"] = [{"secretRef": {"name": secret}}]
    if probe:
        container["readinessProbe"] = {**probe, "periodSeconds": 2, "timeoutSeconds": 2}
    spec = {
        "containers": [container],
        "volumes": volumes,
        "nodeSelector": {"workload": "control-plane"},
        "tolerations": [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists"}],
        "restartPolicy": "Never",
        "automountServiceAccountToken": bool(account),
        "securityContext": {
            "seccompProfile": {"type": "RuntimeDefault"},
            "runAsUser": os.getuid(),
            "runAsGroup": os.getgid(),
            "fsGroup": os.getgid(),
        },
    }
    if account:
        spec["serviceAccountName"] = account
    result = object_("v1", "Pod", name, namespace, spec=spec)
    result["metadata"]["labels"] = {"fixture-service": name}
    return result


class Serving:
    def __init__(self, prepared):
        self.fixture = prepared
        self.root = prepared.root
        self.name = prepared.run_id
        self.network = self.name + "-kind"
        self.kubeconfig = self.root / "serving.kubeconfig"
        self.kind_env = {**os.environ, "KIND_EXPERIMENTAL_DOCKER_NETWORK": self.network}
        self.network_attempted = False
        self.network_id = ""
        self.uid = ""

    def k(self, *args, **kwargs):
        return self.k_command(*args, **kwargs).stdout

    def k_command(self, *args, **kwargs):
        return self.fixture.command(
            [
                "kubectl",
                "--kubeconfig",
                str(self.kubeconfig),
                "--context",
                f"kind-{self.name}",
                "--request-timeout=15s",
                *args,
            ],
            **kwargs,
        )

    def apply(self, *objects):
        self.k(
            "create",
            "-f",
            "-",
            data=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}),
        )

    def private(self, path, value):
        file = self.root / path
        file.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
        with file.open("x", encoding="utf-8") as stream:
            file.chmod(0o600)
            stream.write(value)

    def secret(self, name, namespace, data):
        self.apply(object_("v1", "Secret", name, namespace, immutable=True, stringData=data))

    def service(self, name, namespace, *ports):
        self.apply(
            object_(
                "v1",
                "Service",
                name,
                namespace,
                spec={
                    "selector": {"fixture-service": name},
                    "ports": [{"name": f"tcp-{port}", "port": port} for port in ports],
                },
            )
        )

    def cluster(self):
        image = json.loads((ROOT / "deployments/local-infrastructure.json").read_text())[
            "compatibility"
        ]["nodeImage"]
        existing = self.fixture.command(["kind", "get", "clusters"])
        if self.name in existing.stdout.splitlines():
            raise ValueError("serving cluster already exists")
        self.network_attempted = True
        self.network_id = self.fixture.docker(
            "network", "create", "--label", f"{fixture.LABEL}={self.name}", self.network
        ).stdout.strip()
        config = {
            "kind": "Cluster",
            "apiVersion": "kind.x-k8s.io/v1alpha4",
            "networking": {"apiServerAddress": "127.0.0.1"},
            "nodes": [
                {
                    "role": "control-plane",
                    "extraMounts": [
                        {
                            "hostPath": str(self.root),
                            "containerPath": "/fixture",
                            "readOnly": True,
                        }
                    ],
                },
                {"role": "worker"},
            ],
        }
        self.fixture.command(
            [
                "kind",
                "create",
                "cluster",
                "--name",
                self.name,
                "--kubeconfig",
                str(self.kubeconfig),
                "--image",
                image,
                "--wait",
                "120s",
                "--config",
                "-",
            ],
            env=self.kind_env,
            data=json.dumps(config),
            timeout=240,
        )
        self.uid = self.k(
            "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"
        ).strip()
        if not self.uid:
            raise ValueError("missing fixture cluster identity")
        self.k("label", "node", f"{self.name}-control-plane", "workload=control-plane")
        self.k("label", "node", f"{self.name}-worker", "workload=performance-generator")
        self.apply(*(object_("v1", "Namespace", ns) for ns in (PLATFORM, GENERATORS, SUT)))

    def database_url(self, database):
        name = f"{self.name}-{database}"
        owned = self.fixture.inspect("container", name)
        self.fixture.docker("network", "connect", self.network_id, owned["Id"])
        host = self.fixture.inspect("container", name)["NetworkSettings"]["Networks"][self.network][
            "IPAddress"
        ]
        url = urlsplit(
            json.loads((self.root / f"{database}-credentials.json").read_text())["runtime"]
        )
        return urlunsplit(url._replace(netloc=f"{url.username}:{url.password}@{host}:5432"))

    def cleanup(self):
        if not self.network_attempted:
            return
        details = self.fixture.inspect("network", self.network, cleanup=True)
        if self.network_id and details["Id"] != self.network_id:
            raise ValueError("serving network identity changed")
        self.network_id = details["Id"]
        nodes = self.fixture.docker(
            "ps", "-aq", "--filter", f"label=io.x-k8s.kind.cluster={self.name}", cleanup=True
        ).stdout.split()
        for node in nodes:
            record = json.loads(self.fixture.docker("inspect", node, cleanup=True).stdout)[0]
            if (
                record["Name"] not in {f"/{self.name}-control-plane", f"/{self.name}-worker"}
                or record["NetworkSettings"]["Networks"].get(self.network, {}).get("NetworkID")
                != self.network_id
            ):
                raise ValueError("refusing cleanup of foreign kind node")
        if nodes:
            self.fixture.command(
                [
                    "kind",
                    "delete",
                    "cluster",
                    "--name",
                    self.name,
                    "--kubeconfig",
                    str(self.kubeconfig),
                ],
                env=self.kind_env,
                cleanup=True,
                timeout=60,
            )
        for database in ("argus", "perfeng"):
            info = self.fixture.inspect("container", f"{self.name}-{database}", cleanup=True)
            if self.network in info["NetworkSettings"]["Networks"]:
                self.fixture.docker(
                    "network", "disconnect", self.network_id, info["Id"], cleanup=True
                )
        self.fixture.docker("network", "rm", self.network_id, cleanup=True)
        if (
            self.fixture.docker(
                "network", "ls", "--filter", f"id={self.network_id}", "--quiet", cleanup=True
            ).stdout.strip()
            or self.fixture.docker(
                "ps", "-aq", "--filter", f"label=io.x-k8s.kind.cluster={self.name}", cleanup=True
            ).stdout.strip()
        ):
            raise ValueError("serving resources still present")

    def candidate_revision(self, source, target):
        return source

    def registry_entries(self, entry, files):
        return [entry]

    def checkout(self, lock):
        source = self.fixture.command(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
        target = self.fixture.command(
            ["git", "-C", str(ROOT), "show", f"{source}:charts/sample-sut/files/server.py"]
        ).stdout
        source = self.candidate_revision(source, target)
        self.private("sample.py", target)
        files = runtime.registry_files(lock, ROOT.parent / "perfeng-k8s")
        registry = json.loads(files.pop("registry.json"))
        [entry] = [
            item
            for item in registry["entries"]
            if item["testId"] == "checkout-api" and item["profile"] == "smoke"
        ]
        env_pin = digest(json.dumps({"clusterUID": self.uid, "source": source}).encode())
        entry["environment"] = {
            "id": "joint-fixture",
            "version": "1.0.0",
            "sha256": env_pin,
            "fingerprint": env_pin,
        }
        entry["principals"] = ["fixture-argus"]

        def reference(name):
            return {
                "issuer": ISSUER,
                "id": name,
                "revision": "1.0.0",
                "sha256": digest(name.encode()),
            }

        entry["integration"] = {
            "requestIssuer": ARGUS_ISSUER,
            "service": reference("checkout-service"),
            "capability": reference("checkout-capability"),
            "deployment": reference("checkout-fixture"),
            "candidate": {"gitSha": source, "image": entry["candidateImages"][0]},
            "evidence": [reference("checkout-evidence")],
        }
        entry["catalogOwnerRoute"] = {
            "reference": reference("checkout-route"),
            "owner": {"issuer": ISSUER, "id": "fixture-owner"},
            "route": {"issuer": ISSUER, "id": "fixture-route"},
        }
        registry["performanceCatalog"] = {
            "issuer": ISSUER,
            "principalIssuer": ARGUS_ISSUER,
            "producerVersion": "1.0.0",
        }
        registry["entries"] = self.registry_entries(entry, files)
        self.private("registry/registry.json", json.dumps(registry))
        for path in (
            entry["policy"],
            entry["executionTemplate"],
            "jobs/analysis.yaml",
            "jobs/reporting.yaml",
        ):
            self.private(f"registry/{path}", files[path.replace("/", "--")])
        self.secret("sample-env", SUT, {"FIXTURE_HOST": "0.0.0.0"})
        self.apply(
            pod(
                "sample-sut",
                SUT,
                PYTHON_IMAGE,
                ["python", "-B", "-u", "/fixture/sample.py"],
                secret="sample-env",
                paths=("sample.py",),
                probe={"httpGet": {"path": "/healthz", "port": 8080}},
            )
        )
        self.service("sample-sut", SUT, 8080)
        return json.loads(files["jobs--checkout.json"])["spec"]["template"]["spec"]["containers"][
            0
        ]["image"]

    def storage(self, aws_image):
        entries = json.loads((self.root / "registry/registry.json").read_text())["entries"]
        policy_paths = tuple(f"registry/{entry['policy']}" for entry in entries)
        uploads = []
        for path in policy_paths:
            policy = json.loads((self.root / path).read_text())["metadata"]
            key = f"policies/{policy['name']}/{policy['version']}/performance-policy.json"
            uploads.append(
                f"aws_s3 put-object --bucket {BUCKET} --key {shlex.quote(key)}"
                f" --body {shlex.quote('/fixture/' + path)}"
            )
        bootstrap, uploader, reader = (
            {"access-key": secrets.token_hex(16), "secret-key": secrets.token_hex(32)}
            for _ in range(3)
        )
        self.report_reader = reader
        config = storage_access.server_config(bootstrap, uploader)
        config["identities"].append(
            {
                "name": "fixture-report-reader",
                "credentials": [
                    {
                        "accessKey": reader["access-key"],
                        "secretKey": reader["secret-key"],
                    }
                ],
                "policyNames": ["fixture-report-reader"],
            }
        )
        read_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{BUCKET}/runs/*"],
                }
            ],
        }
        config["policies"].append(
            {"name": "fixture-report-reader", "content": json.dumps(read_policy)}
        )
        self.private("s3.json", json.dumps(config))
        self.fixture.command(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                f"/CN={STORE_HOST}",
                "-addext",
                f"subjectAltName=DNS:{STORE_HOST}",
                "-keyout",
                str(self.root / "store.key"),
                "-out",
                str(self.root / "store.crt"),
            ]
        )
        (self.root / "store.crt").chmod(0o644)
        for ns in (PLATFORM, GENERATORS):
            self.secret("perfeng-s3-uploader-auth", ns, uploader)
        self.secret("report-reader", PLATFORM, reader)
        image = yaml.safe_load((ROOT / "charts/seaweedfs/values.yaml").read_text())["image"]
        command = [
            "/bin/sh",
            "-ec",
            "mkdir -p /tmp/master /tmp/volumes; exec /usr/bin/weed server "
            "-ip=127.0.0.1 -ip.bind=127.0.0.1 -dir=/tmp/volumes -master.dir=/tmp/master "
            "-master.volumeSizeLimitMB=64 -volume.max=16 -master.telemetry=false -filer=true -s3=true "
            "-s3.ip.bind=0.0.0.0 -s3.port=8333 -s3.port.https=8443 -s3.cert.file=/fixture/store.crt "
            "-s3.key.file=/fixture/store.key -s3.config=/fixture/s3.json -s3.iam=false "
            "-s3.port.iceberg=0 -s3.port.lance=0",
        ]
        storage = pod(
            "seaweedfs",
            PLATFORM,
            image,
            command,
            paths=("s3.json", "store.key", "store.crt"),
            probe={"tcpSocket": {"port": 8443}},
        )
        # The pinned filer config writes /data/filerldb2. An explicit scratch
        # mount gives the caller's UID/group access to this image-owned path.
        storage["spec"]["containers"][0]["volumeMounts"].append(
            {"name": "scratch", "mountPath": "/data"}
        )
        self.apply(storage)
        self.service("seaweedfs", PLATFORM, 8333, 8443)
        self.wait("seaweedfs", PLATFORM)
        self.secret(
            "store-bootstrap",
            PLATFORM,
            {
                "AWS_ACCESS_KEY_ID": bootstrap["access-key"],
                "AWS_SECRET_ACCESS_KEY": bootstrap["secret-key"],
                "AWS_DEFAULT_REGION": "us-east-1",
                "HOME": "/tmp",
                "FIXTURE_UPLOADER_ACCESS_KEY": uploader["access-key"],
                "FIXTURE_UPLOADER_SECRET_KEY": uploader["secret-key"],
            },
        )
        self.apply(
            pod(
                "store-init",
                PLATFORM,
                aws_image,
                [
                    "/bin/sh",
                    "-ec",
                    f"""
aws_s3() {{
  AWS_MAX_ATTEMPTS=1 aws --endpoint-url https://{STORE_HOST}:8443 \
    --ca-bundle /fixture/store.crt --cli-connect-timeout 4 --cli-read-timeout 8 s3api "$@"
}}
for attempt in 1 2 3 4 5; do
  if aws_s3 create-bucket --bucket {BUCKET} || aws_s3 head-bucket --bucket {BUCKET}; then
    break
  fi
  test "$attempt" != 5
  sleep 2
done
{chr(10).join(uploads)}
export AWS_ACCESS_KEY_ID="$FIXTURE_UPLOADER_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$FIXTURE_UPLOADER_SECRET_KEY"
printf '%s' fixture-storage-ready > /tmp/probe
aws_s3 put-object --bucket {BUCKET} --key runs/fixture-probe/ready --body /tmp/probe
aws_s3 get-object --bucket {BUCKET} --key runs/fixture-probe/ready /tmp/probe-read
test "$(cat /tmp/probe-read)" = fixture-storage-ready
""",
                ],
                secret="store-bootstrap",
                paths=("store.crt", *policy_paths),
            )
        )
        self.k(
            "-n",
            PLATFORM,
            "wait",
            "--for=jsonpath={.status.phase}=Succeeded",
            "pod/store-init",
            "--timeout=150s",
            timeout=160,
        )
        return uploader

    def wait(self, name, namespace):
        try:
            self.k(
                "-n",
                namespace,
                "wait",
                "--for=condition=Ready",
                f"pod/{name}",
                "--timeout=90s",
                timeout=100,
            )
        except ValueError:
            reason = "Unavailable"
            try:
                result = self.k_command(
                    "-n",
                    namespace,
                    "get",
                    f"pod/{name}",
                    "-o",
                    "json",
                    optional=True,
                    cleanup=True,
                    timeout=5,
                )
                status = json.loads(result.stdout)["status"]
                allowed = {
                    "ContainerCreating",
                    "ImagePullBackOff",
                    "ErrImagePull",
                    "CreateContainerConfigError",
                    "CreateContainerError",
                    "CrashLoopBackOff",
                    "OOMKilled",
                    "Error",
                    "Completed",
                }
                for container in status.get("containerStatuses", []):
                    state = container.get("state", {})
                    candidate = state.get("waiting", state.get("terminated", {})).get("reason")
                    if candidate in allowed:
                        reason = candidate
                        break
                if any(
                    condition.get("type") == "PodScheduled"
                    and condition.get("status") == "False"
                    and condition.get("reason") == "Unschedulable"
                    for condition in status.get("conditions", [])
                ):
                    reason = "Unschedulable"
                if reason == "Unavailable" and status.get("phase") == "Running":
                    reason = "RunningNotReady"
            except (ValueError, TypeError, KeyError, OSError, AttributeError):
                pass
            service = (
                name
                if name
                in {
                    "seaweedfs",
                    "sample-sut",
                    "argus",
                    "perfeng",
                    "baseline-reader",
                    "fixture-github",
                }
                else "service"
            )
            print(f"Fixture pod {service} readiness failed: {reason}", flush=True)
            raise ValueError("fixture pod readiness failed") from None

    def argus_configuration(self):
        return {}, ()

    def argus_pod(self, paths):
        return pod(
            "argus",
            PLATFORM,
            PYTHON_IMAGE,
            ["/fixture/argus-control-plane"],
            secret="argus-env",
            paths=("argus-control-plane", *paths),
            probe={"tcpSocket": {"port": 8080}},
        )

    def provider_permissions(self):
        return ["catalog:read", "runs:create", "runs:read"]

    def perfeng_configuration(self):
        return {}

    def products(self, lock, uploader):
        token = secrets.token_urlsafe(48)
        auth = runtime.authentication(token)
        document = json.loads(auth["credentials.json"])
        document["credentials"][0].update(
            principal="fixture-argus", permissions=self.provider_permissions()
        )
        self.private("perfeng-auth.json", json.dumps(document))
        self.private("fixture-provider-token", token)
        self.apply(object_("v1", "ServiceAccount", "perfeng", PLATFORM))
        role = {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "perfeng"}
        self.apply(
            object_(
                "rbac.authorization.k8s.io/v1",
                "Role",
                "perfeng",
                GENERATORS,
                rules=[
                    {
                        "apiGroups": ["batch"],
                        "resources": ["jobs"],
                        "verbs": ["create", "get", "delete"],
                    },
                    {"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]},
                ],
            ),
            object_(
                "rbac.authorization.k8s.io/v1",
                "RoleBinding",
                "perfeng",
                GENERATORS,
                roleRef=role,
                subjects=[{"kind": "ServiceAccount", "name": "perfeng", "namespace": PLATFORM}],
            ),
        )
        env = runtime.runtime_configuration(lock) | {
            "PERFENG_DATABASE_URL": self.database_url("perfeng"),
            "PERFENG_HTTP_ADDRESS": ":8080",
            "PERFENG_WORKER_ID": self.name,
            "PERFENG_KUBERNETES_NAMESPACE": GENERATORS,
            "PERFENG_REGISTRY_ROOT": "/fixture/registry",
            "PERFENG_REGISTRY_MANIFEST": "registry.json",
            "PERFENG_AUTHENTICATION_FILE": "/fixture/perfeng-auth.json",
            "PERFENG_ANALYSIS_JOB_FILE": "/fixture/registry/jobs/analysis.yaml",
            "PERFENG_REPORT_JOB_FILE": "/fixture/registry/jobs/reporting.yaml",
            "PERFENG_INTEGRATION_ANALYSIS_PUBLICATION_ENABLED": "true",
            "AWS_ACCESS_KEY_ID": uploader["access-key"],
            "AWS_SECRET_ACCESS_KEY": uploader["secret-key"],
        }
        self.secret("perfeng-env", PLATFORM, env | self.perfeng_configuration())
        argus_env, argus_paths = self.argus_configuration()
        self.secret(
            "argus-env",
            PLATFORM,
            {
                "ARGUS_DATABASE_URL": self.database_url("argus"),
                "ARGUS_HTTP_ADDRESS": ":8080",
                "ARGUS_GITHUB_TOKEN": secrets.token_urlsafe(48),
                "ARGUS_GITHUB_WEBHOOK_SECRET": secrets.token_urlsafe(48),
            }
            | argus_env,
        )
        self.apply(
            pod(
                "perfeng",
                PLATFORM,
                PYTHON_IMAGE,
                ["/fixture/perfeng-control-plane-control-plane"],
                secret="perfeng-env",
                paths=("perfeng-control-plane-control-plane", "registry", "perfeng-auth.json"),
                account="perfeng",
                probe={"httpGet": {"path": "/readyz", "port": 8080}},
            ),
            self.argus_pod(argus_paths),
        )
        for name in ("perfeng", "argus"):
            self.service(name, PLATFORM, 8080)

    def probe(self, name):
        if name != "product-http":
            self.wait(name, SUT if name == "sample-sut" else PLATFORM)
            return
        result = self.k_command(
            "--request-timeout=45s",
            "-n",
            SUT,
            "exec",
            "-i",
            "sample-sut",
            "--",
            "python",
            "-c",
            """
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

opener = build_opener(ProxyHandler({}), NoRedirect())
deadline = time.monotonic() + 25

def connect(request):
    while True:
        try:
            return opener.open(request, timeout=5)
        except HTTPError:
            raise
        except (URLError, TimeoutError):
            if time.monotonic() >= deadline:
                print("Product service connection unavailable", flush=True)
                sys.exit(1)
            time.sleep(1)

def expect_status(name, url, expected):
    try:
        with connect(url) as response:
            actual = response.status
    except HTTPError as error:
        actual = error.code
    if actual != expected:
        print(f"{name} returned HTTP {actual}", flush=True)
        sys.exit(1)

catalog = "http://perfeng.perf-platform.svc.cluster.local:8080/v1/performance-catalog"
expect_status("Argus webhook", "http://argus.perf-platform.svc.cluster.local:8080/webhooks/github", 405)
expect_status("Anonymous catalog", catalog, 401)
request = Request(catalog, headers={"Authorization": "Bearer " + sys.stdin.read().strip()})
try:
    with connect(request) as response:
        page = json.load(response)
except HTTPError as error:
    print(f"Authenticated catalog returned HTTP {error.code}", flush=True)
    sys.exit(1)
if page["kind"] != "PerformanceCatalogPage" or page["snapshot"]["principal"]["id"] != "fixture-argus":
    print("Unexpected catalog identity", flush=True)
    sys.exit(1)
""",
            data=(self.root / "fixture-provider-token").read_text(),
            optional=True,
            timeout=40,
        )
        if result.returncode:
            status = result.stdout.strip()
            prefixes = (
                "Argus webhook returned HTTP ",
                "Anonymous catalog returned HTTP ",
                "Authenticated catalog returned HTTP ",
            )
            if status in {
                "Unexpected catalog identity",
                "Product service connection unavailable",
            } or any(
                status.startswith(prefix) and status[len(prefix) :].isdigit() for prefix in prefixes
            ):
                print(status, flush=True)
            raise ValueError("fixture HTTP probe failed")

    def after_ready(self):
        pass

    def run(self):
        try:
            print("Starting disposable kind cluster", flush=True)
            self.cluster()
            lock = release.load_lock(ROOT / "deployments/local-control-plane.json")
            aws_image = self.checkout(lock)
            print("Preparing disposable HTTPS object storage", flush=True)
            uploader = self.storage(aws_image)
            print("Starting and probing pinned product services", flush=True)
            self.products(lock, uploader)
            prove_readiness(self.fixture.evidence, self.probe)
            self.fixture.evidence["servingClusterUID"] = self.uid
            self.after_ready()
        finally:
            try:
                self.cleanup()
            except (ValueError, KeyError, OSError):
                self.fixture.evidence["servingCleanup"] = "unresolved"
                raise
            self.fixture.evidence["servingCleanup"] = "passed"


def main(driver=Serving, *, description=__doc__):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--execute", action="store_true")
    if not parser.parse_args().execute:
        print("Serving fixture requires --execute to create disposable resources.")
        return
    missing = [
        tool
        for tool in ("docker", "git", "go", "kind", "kubectl", "openssl")
        if not shutil.which(tool)
    ]
    if missing:
        raise SystemExit("Missing serving fixture tools: " + ", ".join(missing))
    root = Path(tempfile.mkdtemp(prefix="argus-perfeng-serving-"))
    print(f"Private serving fixture directory: {root}", flush=True)
    signal.signal(signal.SIGTERM, fixture.interrupt)
    prepared = fixture.Fixture(fixture.inputs(), root)
    try:
        with prepared.prepared():
            driver(prepared).run()
    except (ValueError, KeyError, OSError, KeyboardInterrupt):
        raise SystemExit("Joint fixture failed. Inspect the private fixture evidence.") from None
    print("Disposable joint fixture and cleanup passed.")


if __name__ == "__main__":
    main()
