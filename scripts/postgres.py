"""Preview-first PostgreSQL deployment into the verified local kind cluster."""

import argparse
import base64
import json
import secrets
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster
else:
    import cluster

NAMESPACE = "perf-platform"
RELEASE = "postgres"
SECRET = "perfeng-postgres-auth"
CLAIM = "data-postgres-0"
OWNER = "perfeng-environment"


def kubectl(root: Path) -> list[str]:
    return [
        "kubectl",
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--context",
        cluster.CONTEXT,
        "--namespace",
        NAMESPACE,
        "--request-timeout=30s",
    ]


def commands(action: str, root: Path = cluster.ROOT) -> list[list[str]]:
    kube = kubectl(root)
    health = [
        [*kube, "rollout", "status", f"statefulset/{RELEASE}", "--timeout=300s"],
        [*kube, "get", "pvc", CLAIM, "-o", "json"],
        [
            *kube,
            "exec",
            f"statefulset/{RELEASE}",
            "--",
            "sh",
            "-c",
            'export PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")" PGCONNECT_TIMEOUT=10; '
            "exec psql -X -w -h postgres.perf-platform.svc.cluster.local "
            '-U postgres -d perfeng -At -v ON_ERROR_STOP=1 -c "SELECT 1"',
        ],
    ]
    if action == "health":
        return health
    if action == "deploy":
        return [
            [
                "helm",
                "upgrade",
                "--install",
                RELEASE,
                str(root / "charts/postgres"),
                "--kubeconfig",
                str(cluster.kubeconfig(root)),
                "--kube-context",
                cluster.CONTEXT,
                "--namespace",
                NAMESPACE,
                "--wait",
                "--timeout",
                "600s",
            ],
            *health,
        ]
    raise ValueError("Unknown PostgreSQL action")


def validate_secret(value: dict[str, Any]) -> None:
    if (
        value.get("type") != "Opaque"
        or value.get("immutable") is not True
        or value.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER
    ):
        raise ValueError("Existing PostgreSQL Secret is not owned immutable local credentials")
    password = value.get("data", {}).get("password")
    if not isinstance(password, str):
        raise ValueError("Existing PostgreSQL Secret has no password")
    try:
        decoded = base64.b64decode(password, validate=True)
    except ValueError:
        raise ValueError("Existing PostgreSQL Secret has invalid password encoding") from None
    if len(decoded) < 32:
        raise ValueError("Existing PostgreSQL Secret has an invalid password length")


def ensure_secret(root: Path, *, create: bool) -> None:
    kube = kubectl(root)
    current = cluster.run([*kube, "get", "secret", SECRET, "--ignore-not-found", "-o", "json"])
    if current.strip():
        validate_secret(json.loads(current))
        print("Reusing existing PostgreSQL credentials (not rotated).", flush=True)
        return
    if not create:
        raise ValueError("PostgreSQL credentials are missing; run deploy first")
    # A retained volume may contain an initialized database. A new password would
    # not reset it. Also refuse to race a previous/foreign StatefulSet deployment.
    for kind, name in [("pvc", CLAIM), ("statefulset", RELEASE)]:
        existing = cluster.run([*kube, "get", kind, name, "--ignore-not-found", "-o", "name"])
        if existing.strip():
            raise ValueError("PostgreSQL resources exist without credentials; restore the Secret")
    value = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": SECRET,
            "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/managed-by": OWNER},
        },
        "type": "Opaque",
        "immutable": True,
        "stringData": {"password": secrets.token_urlsafe(32)},
    }
    # create (not apply) fails on concurrent creation. Nothing secret goes in
    # argv, Helm values/release history, terminal output, or a local file.
    cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(value).encode("utf-8"))
    print("Created local PostgreSQL credentials; keep this Secret with its database.", flush=True)


def execute(action: str, root: Path = cluster.ROOT) -> None:
    print("Verifying local Docker endpoint and dedicated cluster credentials...", flush=True)
    cluster.verify_local_docker()
    cluster.verify_context(root)
    kube = kubectl(root)
    # Do not create namespaces or provisioners implicitly; cluster deploy owns them.
    cluster.run([*kube, "get", "namespace", NAMESPACE, "-o", "name"])
    if action == "deploy":
        storage = json.loads(cluster.run([*kube, "get", "storageclass", "standard", "-o", "json"]))
        if (
            storage.get("provisioner") != "rancher.io/local-path"
            or storage.get("volumeBindingMode") != "WaitForFirstConsumer"
        ):
            raise ValueError(
                "Expected kind's standard local-path StorageClass with delayed binding"
            )
    ensure_secret(root, create=action == "deploy")
    planned = commands(action, root)
    for index, command in enumerate(planned):
        print(f"PostgreSQL {action}: step {index + 1}/{len(planned)}...", flush=True)
        output = cluster.run(command)
        if command[len(kube) : len(kube) + 2] == ["get", "pvc"]:
            if json.loads(output).get("status", {}).get("phase") != "Bound":
                raise ValueError("PostgreSQL persistent volume is not Bound")
        if index == len(planned) - 1 and output.strip() != "1":
            raise ValueError("PostgreSQL authenticated SQL health check did not return 1")
    print(f"Local PostgreSQL action completed: {action}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "health"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            execute(args.action)
        else:
            print("Preview: verify the local cluster and namespace; deploy checks local storage.")
            print(
                "Credentials: reuse owned Secret, or create via private stdin only on fresh deploy."
            )
            for command in commands(args.action):
                print(json.dumps(command))
            print("Preview only. Add --execute to run. No credentials generated.")
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Local PostgreSQL error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
