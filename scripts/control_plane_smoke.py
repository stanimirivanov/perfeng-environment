"""Run one authenticated k6-to-analysis control-plane smoke test."""

import argparse
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster, control_plane_deployment, control_plane_runtime, k6_run, postgres
    from .control_plane_release import (
        DEFAULT_K8S_ROOT,
        DEPLOYMENT_REPOSITORY,
        REVISION,
        DeploymentLock,
        git,
        revision_reader,
    )
    from .control_plane_release import validate as validate_release
else:
    import cluster
    import control_plane_deployment
    import control_plane_runtime
    import k6_run
    import postgres
    from control_plane_release import (
        DEFAULT_K8S_ROOT,
        DEPLOYMENT_REPOSITORY,
        REVISION,
        DeploymentLock,
        git,
        revision_reader,
    )
    from control_plane_release import validate as validate_release

CLIENT_IMAGE = (
    "python:3.12.14-slim-bookworm@sha256:"
    "782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"
)
CANDIDATE_IMAGE = (
    "docker.io/library/python@sha256:"
    "782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"
)
EXPECTED_FORMATS = [
    "analysis-result/v1",
    "k6-json-points",
    "k6-summary-json",
    "normalized-result/v1",
    "raw-result/v2",
]
POD_PATTERN = re.compile(r"control-plane-smoke-[0-9a-f]{16}")
CLIENT_PROGRAM = r"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

MAX_RESPONSE = 1 << 20
TERMINAL = {
    "COMPLETED",
    "INVALID",
    "ABORTED",
    "INFRASTRUCTURE_FAILURE",
    "TEST_FAILURE",
}


def call(method, path, body=None):
    headers = {"Authorization": "Bearer " + os.environ["PERFENG_OPERATOR_TOKEN"]}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = os.environ["PERFENG_IDEMPOTENCY_KEY"]
    request = urllib.request.Request(
        os.environ["PERFENG_API"] + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content = response.read(MAX_RESPONSE + 1)
            status = response.status
            content_type = response.headers.get_content_type()
    except urllib.error.HTTPError as error:
        content = error.read(MAX_RESPONSE + 1)
        if len(content) <= MAX_RESPONSE and error.headers.get_content_type() == "application/json":
            try:
                value = json.loads(content)
            except json.JSONDecodeError:
                value = None
            if (
                isinstance(value, dict)
                and isinstance(value.get("code"), str)
                and re.fullmatch(r"[A-Z][A-Z_]{0,63}", value["code"])
                and isinstance(value.get("requestId"), str)
                and re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    value["requestId"],
                )
            ):
                raise RuntimeError(
                    f"{method} {path} returned HTTP {error.code} "
                    f"{value['code']} (request {value['requestId']})"
                ) from None
        raise RuntimeError(f"{method} {path} returned HTTP {error.code}") from None
    except (OSError, TimeoutError):
        raise RuntimeError(f"{method} {path} was unavailable") from None
    if len(content) > MAX_RESPONSE or content_type != "application/json":
        raise RuntimeError(f"{method} {path} returned an invalid response")
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        raise RuntimeError(f"{method} {path} returned invalid JSON") from None
    return status, value


def run():
    submitted = json.loads(os.environ["PERFENG_SMOKE_REQUEST"])
    status, current = call("POST", "/v1/runs", submitted)
    run_id = current.get("id") if isinstance(current, dict) else None
    if status != 201 or not isinstance(run_id, str) or re.fullmatch(
        r"perf-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}", run_id
    ) is None:
        raise RuntimeError("Run acceptance response was invalid")
    if current.get("request") != submitted or current.get("state") != "CREATED":
        raise RuntimeError("Accepted Run differs from the submitted request")
    revision = current.get("revision")
    deadline = time.monotonic() + 1100
    while current.get("state") not in TERMINAL and time.monotonic() < deadline:
        time.sleep(2)
        status, observed = call("GET", "/v1/runs/" + run_id)
        next_revision = observed.get("revision") if isinstance(observed, dict) else None
        if (
            status != 200
            or observed.get("id") != run_id
            or observed.get("request") != submitted
            or type(next_revision) is not int
            or type(revision) is not int
            or next_revision < revision
        ):
            raise RuntimeError("Run observation response was invalid")
        current = observed
        revision = next_revision
    if current.get("state") != "COMPLETED" or current.get("toolExitCode") != 0:
        raise RuntimeError("Run did not complete successfully: " + str(current.get("state")))
    status, collection = call("GET", "/v1/runs/" + run_id + "/artifacts")
    artifacts = collection.get("artifacts") if isinstance(collection, dict) else None
    if status != 200 or not isinstance(artifacts, list):
        raise RuntimeError("Artifact response was invalid")
    formats = sorted(item.get("format") for item in artifacts if isinstance(item, dict))
    expected = json.loads(os.environ["PERFENG_EXPECTED_FORMATS"])
    if formats != expected or any(item.get("runId") != run_id for item in artifacts):
        raise RuntimeError("Run artifact set was incomplete or foreign")
    print(json.dumps({
        "runId": run_id,
        "state": current["state"],
        "revision": revision,
        "toolExitCode": current["toolExitCode"],
        "artifactFormats": formats,
    }, separators=(",", ":")))


try:
    run()
except Exception as error:
    print(json.dumps({"error": str(error)}, separators=(",", ":")))
    sys.exit(1)
""".strip()


def candidate_revision(root: Path = cluster.ROOT) -> str:
    """Bind the sample candidate to clean tracked source bytes at HEAD."""
    revision = git(root, "rev-parse", "HEAD").decode("ascii").strip()
    if REVISION.fullmatch(revision) is None:
        raise ValueError("Sample candidate revision is invalid")
    for relative in ("charts/sample-sut/values.yaml", "charts/sample-sut/files/server.py"):
        if git(root, "show", f"{revision}:{relative}") != (root / relative).read_bytes():
            raise ValueError("Sample candidate files differ from the recorded Git revision")
    return revision


def smoke_request(
    release: DeploymentLock,
    candidate_git_sha: str,
    k8s_root: Path = DEFAULT_K8S_ROOT,
) -> dict[str, Any]:
    """Assemble the exact checkout request authorized by the locked registry."""
    if REVISION.fullmatch(candidate_git_sha) is None:
        raise ValueError("Sample candidate revision is invalid")
    read = revision_reader(k8s_root, DEPLOYMENT_REPOSITORY, release.deployment_revision)
    prefix = f"{release.bundle_path}/"
    registry = json.loads(read(prefix + "registry.json"))
    matches = [entry for entry in registry["entries"] if entry.get("testId") == "checkout-api"]
    if len(matches) != 1:
        raise ValueError("Locked registry must contain one checkout-api entry")
    entry = matches[0]
    policy_bytes = read(prefix + entry["policy"])
    policy = json.loads(policy_bytes)
    candidates = entry.get("candidateImages")
    if candidates != [CANDIDATE_IMAGE] or entry.get("profile") != "smoke":
        raise ValueError("Locked checkout smoke candidate differs from the client fixture")
    return {
        "testSuite": entry["testId"],
        "catalogue": entry["catalogue"],
        "profile": entry["profile"],
        "candidate": {"gitSha": candidate_git_sha, "image": candidates[0]},
        "environment": {key: entry["environment"][key] for key in ("id", "version", "sha256")},
        "policy": {
            "id": policy["metadata"]["name"],
            "version": policy["metadata"]["version"],
            "sha256": hashlib.sha256(policy_bytes).hexdigest(),
        },
    }


def client_pod(name: str, request: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    """Build a restricted one-use API client with Secret-referenced authentication."""
    if (
        POD_PATTERN.fullmatch(name) is None
        or re.fullmatch(r"smoke-[0-9a-f]{32}", idempotency_key) is None
    ):
        raise ValueError("Invalid smoke client identity")
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": postgres.NAMESPACE,
            "labels": {
                "app.kubernetes.io/managed-by": postgres.OWNER,
                "perfeng.io/purpose": "control-plane-smoke",
            },
        },
        "spec": {
            "activeDeadlineSeconds": 1200,
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "nodeSelector": {"workload": "control-plane"},
            "tolerations": [
                {
                    "key": "node-role.kubernetes.io/control-plane",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                }
            ],
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "runAsGroup": 65532,
                "fsGroup": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "api-client",
                    "image": CLIENT_IMAGE,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["python", "-c", CLIENT_PROGRAM],
                    "env": [
                        {
                            "name": "PERFENG_OPERATOR_TOKEN",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": control_plane_runtime.AUTHENTICATION_SECRET,
                                    "key": "operator-token",
                                }
                            },
                        },
                        {
                            "name": "PERFENG_API",
                            "value": "http://control-plane.perf-platform.svc.cluster.local:8080",
                        },
                        {"name": "PERFENG_IDEMPOTENCY_KEY", "value": idempotency_key},
                        {
                            "name": "PERFENG_SMOKE_REQUEST",
                            "value": json.dumps(request, separators=(",", ":")),
                        },
                        {
                            "name": "PERFENG_EXPECTED_FORMATS",
                            "value": json.dumps(EXPECTED_FORMATS, separators=(",", ":")),
                        },
                        {"name": "HOME", "value": "/tmp"},
                        {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                        {"name": "PYTHONUNBUFFERED", "value": "1"},
                    ],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "25m", "memory": "32Mi"},
                        "limits": {"cpu": "100m", "memory": "64Mi"},
                    },
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                }
            ],
            "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "16Mi"}}],
        },
    }


def validate_client_pod(value: dict[str, Any], expected: dict[str, Any]) -> None:
    """Require the retained client Pod to be the successful submitted identity."""
    spec = value.get("spec", {})
    expected_spec = expected["spec"]
    expected_container = expected["spec"]["containers"][0]
    containers = spec.get("containers", [])
    statuses = value.get("status", {}).get("containerStatuses", [])
    exact_spec_fields = (
        "activeDeadlineSeconds",
        "restartPolicy",
        "automountServiceAccountToken",
        "enableServiceLinks",
        "nodeSelector",
        "securityContext",
        "volumes",
    )
    exact_container_fields = (
        "image",
        "imagePullPolicy",
        "command",
        "env",
        "securityContext",
        "resources",
        "volumeMounts",
    )
    if (
        value.get("metadata", {}).get("uid") in {None, ""}
        or value.get("metadata", {}).get("name") != expected["metadata"]["name"]
        or value.get("status", {}).get("phase") != "Succeeded"
        or any(spec.get(field) != expected_spec[field] for field in exact_spec_fields)
        or spec.get("tolerations")
        != [
            *expected_spec["tolerations"],
            {
                "key": "node.kubernetes.io/not-ready",
                "operator": "Exists",
                "effect": "NoExecute",
                "tolerationSeconds": 300,
            },
            {
                "key": "node.kubernetes.io/unreachable",
                "operator": "Exists",
                "effect": "NoExecute",
                "tolerationSeconds": 300,
            },
        ]
        or len(containers) != 1
        or any(
            containers[0].get(field) != expected_container[field]
            for field in exact_container_fields
        )
        or len(statuses) != 1
        or statuses[0].get("state", {}).get("terminated", {}).get("exitCode") != 0
    ):
        raise ValueError("Control-plane smoke client Pod did not succeed with reviewed inputs")


def validate_result(text: str) -> dict[str, Any]:
    """Validate the client's bounded, credential-free completion receipt."""
    value = json.loads(text)
    if (
        not isinstance(value, dict)
        or set(value) != {"runId", "state", "revision", "toolExitCode", "artifactFormats"}
        or not isinstance(value.get("runId"), str)
        or re.fullmatch(k6_run.RUN_PATTERN, value["runId"]) is None
        or value.get("state") != "COMPLETED"
        or type(value.get("revision")) is not int
        or value["revision"] < 1
        or value.get("toolExitCode") != 0
        or value.get("artifactFormats") != EXPECTED_FORMATS
    ):
        raise ValueError("Control-plane smoke receipt is invalid")
    return value


def execute(root: Path = cluster.ROOT) -> None:
    """Submit and verify one retained end-to-end checkout smoke Run."""
    release = validate_release()
    revision = candidate_revision(root)
    request = smoke_request(release, revision)
    suffix = uuid.uuid4().hex
    name = f"control-plane-smoke-{suffix[:16]}"
    document = client_pod(name, request, f"smoke-{suffix}")
    control_plane_deployment.execute("health", root)
    kube = postgres.kubectl(root)
    sut_kube = [
        "kubectl",
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--context",
        cluster.CONTEXT,
        "--namespace",
        "perf-sut",
        "--request-timeout=30s",
    ]
    cluster.run([*sut_kube, "rollout", "status", "deployment/sample-sut", "--timeout=60s"])
    k6_run.validate_sample(
        cluster.run([*sut_kube, "get", "deployment", "sample-sut", "-o", "json"]), root
    )
    print(f"Creating authenticated smoke client Pod {name}...", flush=True)
    cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(document).encode())
    try:
        cluster.run([*kube, "wait", "--for=condition=Ready", f"pod/{name}", "--timeout=300s"])
        output = cluster.run([*kube, "logs", "--follow", f"pod/{name}", "-c", "api-client"])
        pod = json.loads(cluster.run([*kube, "get", "pod", name, "-o", "json"]))
        validate_client_pod(pod, document)
        result = validate_result(output)
    except ValueError:
        raise ValueError(f"Control-plane smoke failed; inspect retained Pod {name}") from None
    print(
        f"Completed Run {result['runId']} with {len(result['artifactFormats'])} verified artifacts."
    )
    print(f"Smoke client retained for inspection: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            execute()
        else:
            print("Preview: submit one locked checkout-api Run through a restricted client Pod.")
            print(
                "Preview only. Add --execute to create the Run and retained Kubernetes resources."
            )
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as error:
        print(f"Control-plane smoke error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
