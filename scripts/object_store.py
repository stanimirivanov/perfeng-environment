"""Preview-first local SeaweedFS deployment and S3 checks; never prints credentials."""

import argparse
import base64
import hashlib
import json
import re
import secrets
import uuid
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster
else:
    import cluster

NAMESPACE = "perf-platform"
SECRET = "perfeng-s3-auth"
BUCKET = "perfeng-artifacts"
ENDPOINT = "http://seaweedfs.perf-platform.svc.cluster.local:8333"
PAYLOAD = b"perfeng-seaweedfs-smoke-v1\n"
CHECKSUM = hashlib.sha256(PAYLOAD).hexdigest()
OWNER = "perfeng-environment"


def kubectl(root: Path) -> list[str]:
    return [
        "kubectl",
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--context",
        cluster.CONTEXT,
        "-n",
        NAMESPACE,
        "--request-timeout=30s",
    ]


def s3_config(access: str, secret: str) -> dict[str, Any]:
    return {
        "identities": [
            {
                "name": "local-bootstrap",
                "credentials": [{"accessKey": access, "secretKey": secret}],
                "actions": ["Admin", "Read", "Write", "List"],
            }
        ]
    }


def validate_secret(value: dict[str, Any]) -> None:
    try:
        if (
            value["type"] != "Opaque"
            or value["immutable"] is not True
            or value["metadata"]["labels"]["app.kubernetes.io/managed-by"] != OWNER
        ):
            raise ValueError("Unowned or mutable Secret")
        data = {
            key: base64.b64decode(value["data"][key], validate=True).decode("utf-8")
            for key in ("access-key", "secret-key", "s3.json")
        }
        if (
            not re.fullmatch(r"[0-9a-f]{32}", data["access-key"])
            or not re.fullmatch(r"[0-9a-f]{64}", data["secret-key"])
            or json.loads(data["s3.json"]) != s3_config(data["access-key"], data["secret-key"])
        ):
            raise ValueError("Invalid or inconsistent credentials")
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "S3 Secret is not valid owned immutable credentials; refusing changes"
        ) from None


def ensure_secret(root: Path, *, create: bool) -> None:
    kube = kubectl(root)
    current = cluster.run([*kube, "get", "secret", SECRET, "--ignore-not-found", "-o", "json"])
    if current.strip():
        validate_secret(json.loads(current))
        print("Reusing existing S3 credentials (not rotated).", flush=True)
        return
    if not create:
        raise ValueError("S3 credentials are missing; deploy first")
    for kind, name in [("pvc", "data-seaweedfs-0"), ("statefulset", "seaweedfs")]:
        if cluster.run([*kube, "get", kind, name, "--ignore-not-found", "-o", "name"]).strip():
            raise ValueError(
                "SeaweedFS resources exist without credentials; restore the matching Secret"
            )
    access, secret = secrets.token_hex(16), secrets.token_hex(32)
    document = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": SECRET,
            "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/managed-by": OWNER},
        },
        "type": "Opaque",
        "immutable": True,
        "stringData": {
            "access-key": access,
            "secret-key": secret,
            "s3.json": json.dumps(s3_config(access, secret)),
        },
    }
    cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(document).encode("utf-8"))
    print("Created private S3 credentials.", flush=True)


def client_script(action: str, probe_id: str) -> str:
    if action not in {"deploy", "health", "smoke", "verify"}:
        raise ValueError("Unknown object-store action")
    if not re.fullmatch(r"[0-9a-f]{32}", probe_id):
        raise ValueError("Probe ID must contain exactly 32 lowercase hexadecimal characters")
    preamble = (
        "set -eu\n"
        "printf '[default]\\ns3 =\\n    addressing_style = path\\n' > /tmp/awsconfig\n"
        f'aws_s3() {{ aws --endpoint-url "{ENDPOINT}" --cli-connect-timeout 10 '
        '--cli-read-timeout 30 s3api "$@"; }\n'
    )
    head = f'aws_s3 head-bucket --bucket "{BUCKET}" >/dev/null\n'
    if action == "deploy":
        return (
            preamble
            + (
                f'if ! aws_s3 head-bucket --bucket "{BUCKET}" >/dev/null 2>&1; then\n'
                f'  aws_s3 create-bucket --bucket "{BUCKET}" >/dev/null\nfi\n'
            )
            + head
        )
    if action == "health":
        return preamble + head
    key = f"smoke/{probe_id}.bin"
    upload = ""
    if action == "smoke":
        upload = (
            "printf 'perfeng-seaweedfs-smoke-v1\\n' > /tmp/source\n"
            f'aws_s3 put-object --bucket "{BUCKET}" --key "{key}" '
            '--body /tmp/source --if-none-match "*" >/dev/null\n'
        )
    return (
        preamble
        + head
        + upload
        + (
            # Validate denial specifically, not just any network/client failure.
            f'if aws --no-sign-request --endpoint-url "{ENDPOINT}" --cli-connect-timeout 10 '
            f'--cli-read-timeout 30 s3api get-object --bucket "{BUCKET}" --key "{key}" '
            "/tmp/anonymous >/dev/null 2>/tmp/denied; then\n"
            '  echo "ERROR: anonymous artifact read succeeded"; exit 1\nfi\n'
            'grep -Eq "AccessDenied|Forbidden|403" /tmp/denied\n'
            f'aws_s3 get-object --bucket "{BUCKET}" --key "{key}" /tmp/download >/dev/null\n'
            "set -- $(sha256sum /tmp/download)\n"
            f'test "$1" = "{CHECKSUM}"\n'
            'echo "S3 checksum and anonymous-access checks passed"\n'
        )
    )


def client_job(action: str, probe_id: str, job_name: str, root: Path) -> dict[str, Any]:
    values = yaml.safe_load((root / "charts/seaweedfs/values.yaml").read_text(encoding="utf-8"))
    image = values["clientImage"]
    if not isinstance(image, str) or not re.fullmatch(
        r"amazon/aws-cli:[0-9.]+@sha256:[0-9a-f]{64}", image
    ):
        raise ValueError("S3 client image must use the pinned official AWS CLI repository")
    env = [
        {
            "name": "AWS_ACCESS_KEY_ID",
            "valueFrom": {"secretKeyRef": {"name": SECRET, "key": "access-key"}},
        },
        {
            "name": "AWS_SECRET_ACCESS_KEY",
            "valueFrom": {"secretKeyRef": {"name": SECRET, "key": "secret-key"}},
        },
        *[
            {"name": key, "value": value}
            for key, value in {
                "HOME": "/tmp",
                "AWS_CONFIG_FILE": "/tmp/awsconfig",
                "AWS_DEFAULT_REGION": "us-east-1",
                "AWS_EC2_METADATA_DISABLED": "true",
                "AWS_PAGER": "",
                "AWS_MAX_ATTEMPTS": "2",
                "AWS_REQUEST_CHECKSUM_CALCULATION": "when_required",
                "AWS_RESPONSE_CHECKSUM_VALIDATION": "when_required",
            }.items()
        ],
    ]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/managed-by": OWNER},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 180,
            "ttlSecondsAfterFinished": 600,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
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
                        "runAsUser": 1000,
                        "runAsGroup": 1000,
                        "fsGroup": 1000,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "s3-check",
                            "image": image,
                            "env": env,
                            "command": ["/bin/sh", "-c", client_script(action, probe_id)],
                            "securityContext": {
                                "readOnlyRootFilesystem": True,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "500m", "memory": "256Mi"},
                            },
                            "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                        }
                    ],
                    "volumes": [{"name": "tmp", "emptyDir": {}}],
                }
            },
        },
    }


def execute(action: str, probe_id: str, root: Path = cluster.ROOT) -> None:
    # Validate the entire client plan before any mutation.
    job_name = f"s3-{action}-{uuid.uuid4().hex[:16]}"
    job = client_job(action, probe_id, job_name, root)
    cluster.verify_local_docker()
    cluster.verify_context(root)
    kube = kubectl(root)
    cluster.run([*kube, "get", "namespace", NAMESPACE, "-o", "name"])
    if action == "deploy":
        sc = json.loads(cluster.run([*kube, "get", "storageclass", "standard", "-o", "json"]))
        if (
            sc.get("provisioner") != "rancher.io/local-path"
            or sc.get("volumeBindingMode") != "WaitForFirstConsumer"
        ):
            raise ValueError(
                "Expected kind's standard local-path StorageClass with delayed binding"
            )
    ensure_secret(root, create=action == "deploy")
    if action == "deploy":
        if __package__:
            from . import storage_access
        else:
            import storage_access
        storage_access.ensure_credentials(root, create=True)
        print("Deploying SeaweedFS (first image download may take several minutes)...", flush=True)
        cluster.run(
            [
                "helm",
                "upgrade",
                "--install",
                "seaweedfs",
                str(root / "charts/seaweedfs"),
                "--kubeconfig",
                str(cluster.kubeconfig(root)),
                "--kube-context",
                cluster.CONTEXT,
                "--namespace",
                NAMESPACE,
                "--wait",
                "--timeout",
                "600s",
            ]
        )
    cluster.run([*kube, "rollout", "status", "statefulset/seaweedfs", "--timeout=300s"])
    pvc = json.loads(cluster.run([*kube, "get", "pvc", "data-seaweedfs-0", "-o", "json"]))
    if pvc.get("status", {}).get("phase") != "Bound":
        raise ValueError("SeaweedFS PVC is not Bound")
    print(f"Running S3 check Job {job_name}...", flush=True)
    cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(job).encode("utf-8"))
    try:
        cluster.run(
            [*kube, "wait", "--for=condition=complete", f"job/{job_name}", "--timeout=210s"]
        )
    except ValueError:
        raise ValueError(
            f"S3 check did not complete; inspect Job {job_name} within 10 minutes"
        ) from None
    if action in {"smoke", "verify"}:
        print(f"Verified s3://{BUCKET}/smoke/{probe_id}.bin SHA-256 {CHECKSUM}", flush=True)
        print(
            f"Persistence check after restart: object_store.py verify --probe-id {probe_id} --execute"
        )
    print(f"Local object-store action completed: {action}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "health", "smoke", "verify"])
    parser.add_argument("--probe-id")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "verify" and not args.probe_id:
            raise ValueError("verify requires the probe ID printed by smoke")
        if args.action != "verify" and args.probe_id:
            raise ValueError("--probe-id is only accepted for read-only verification")
        probe_id = args.probe_id or (uuid.uuid4().hex if args.execute else "0" * 32)
        if args.execute:
            execute(args.action, probe_id)
        else:
            plan = client_job(args.action, probe_id, "s3-preview", cluster.ROOT)
            print("Preview: verify local cluster, namespace, credentials, rollout and PVC.")
            if args.action == "deploy":
                print(
                    "Then create/reuse bootstrap and restricted credentials, install the chart and ensure the bucket."
                )
            print(json.dumps(plan, indent=2))
            print(
                "Preview only. Add --execute to run. No credentials generated or resources changed."
            )
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Local object-store error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
