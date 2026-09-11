"""Run and verify pinned control-plane PostgreSQL migrations locally."""

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster, control_plane_database, control_plane_registry, postgres
    from .control_plane_release import DeploymentLock
    from .control_plane_release import validate as validate_release
else:
    import cluster
    import control_plane_database
    import control_plane_registry
    import postgres
    from control_plane_release import DeploymentLock
    from control_plane_release import validate as validate_release

EXPECTED_RUNTIME_PRIVILEGES = (
    True,
    False,
    True,
    True,
    True,
    False,
    True,
    True,
    True,
    False,
    True,
    True,
    False,
    False,
    True,
    True,
    False,
    False,
    True,
    True,
    True,
    False,
    True,
    True,
    True,
    False,
)


def job_name(release: DeploymentLock) -> str:
    """Return the stable migration identity for one control-plane source revision."""
    return f"control-plane-migrate-{release.source_revision[:12]}"


def job_document(release: DeploymentLock) -> dict[str, Any]:
    """Build the restricted, immutable-image migration Job."""
    name = job_name(release)
    labels = {
        "app.kubernetes.io/name": "perfeng-control-plane-migration",
        "app.kubernetes.io/managed-by": postgres.OWNER,
        "perfeng.io/source-revision": release.source_revision,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": postgres.NAMESPACE,
            "labels": labels,
            "annotations": {"perfeng.io/image": release.image},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 120,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "imagePullSecrets": [{"name": control_plane_registry.SECRET}],
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
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "migrate",
                            "image": release.image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/usr/local/bin/migrate"],
                            "env": [
                                {
                                    "name": "PERFENG_DATABASE_URL",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": control_plane_database.SECRET,
                                            "key": "migration-url",
                                        }
                                    },
                                }
                            ],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "25m", "memory": "32Mi"},
                                "limits": {"cpu": "250m", "memory": "128Mi"},
                            },
                        }
                    ],
                },
            },
        },
    }


def validate_job(value: dict[str, Any], release: DeploymentLock) -> None:
    """Reject a retained Job whose security-sensitive inputs differ."""
    expected = job_document(release)
    try:
        spec = value["spec"]
        expected_spec = expected["spec"]
        pod = spec["template"]["spec"]
        expected_pod = expected_spec["template"]["spec"]
        expected_labels = expected_spec["template"]["metadata"]["labels"]
        containers = pod["containers"]
        expected_containers = expected_pod["containers"]
        if len(containers) != 1 or len(expected_containers) != 1:
            raise ValueError()
        container = containers[0]
        expected_container = expected_containers[0]
        defaulted_container_fields = {
            "terminationMessagePath": "/dev/termination-log",
            "terminationMessagePolicy": "File",
        }
        if (
            value["metadata"]["name"] != expected["metadata"]["name"]
            or value["metadata"]["namespace"] != expected["metadata"]["namespace"]
            or value["metadata"]["labels"] != expected["metadata"]["labels"]
            or value["metadata"]["annotations"].get("perfeng.io/image") != release.image
            or spec["backoffLimit"] != expected_spec["backoffLimit"]
            or spec["activeDeadlineSeconds"] != expected_spec["activeDeadlineSeconds"]
            or any(
                spec["template"]["metadata"]["labels"].get(key) != expected_value
                for key, expected_value in expected_labels.items()
            )
            or any(
                pod.get(key) != expected_value
                for key, expected_value in expected_pod.items()
                if key != "containers"
            )
            or any(
                container.get(key) != expected_value
                for key, expected_value in expected_container.items()
            )
            or not set(container) - set(expected_container) <= set(defaulted_container_fields)
            or any(
                container.get(key) != expected_value
                for key, expected_value in defaulted_container_fields.items()
                if key in container
            )
        ):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("Existing control-plane migration Job has unexpected identity") from None


def grant_runtime(root: Path) -> None:
    """Apply the reviewed runtime table privileges as one transaction."""
    control_plane_database.psql(
        root,
        b"BEGIN;\n"
        b"REVOKE ALL ON SCHEMA perfeng_control FROM PUBLIC, perfeng_runtime;\n"
        b"GRANT USAGE ON SCHEMA perfeng_control TO perfeng_runtime;\n"
        b"REVOKE ALL ON ALL TABLES IN SCHEMA perfeng_control FROM PUBLIC, perfeng_runtime;\n"
        b"GRANT SELECT, INSERT, UPDATE ON perfeng_control.runs, "
        b"perfeng_control.create_bindings, perfeng_control.baselines TO perfeng_runtime;\n"
        b"GRANT SELECT, INSERT ON perfeng_control.artifacts, "
        b"perfeng_control.kubernetes_executions TO perfeng_runtime;\n"
        b"GRANT SELECT, INSERT, UPDATE ON perfeng_control.reconciliation_leases "
        b"TO perfeng_runtime;\nCOMMIT;\n",
    )


def verify_runtime(root: Path) -> None:
    """Verify every required grant and selected forbidden privileges."""
    checks = [
        "has_schema_privilege('perfeng_runtime','perfeng_control','USAGE')",
        "has_schema_privilege('perfeng_runtime','perfeng_control','CREATE')",
    ]
    for table, privileges in [
        ("runs", ("SELECT", "INSERT", "UPDATE", "DELETE")),
        ("create_bindings", ("SELECT", "INSERT", "UPDATE", "DELETE")),
        ("artifacts", ("SELECT", "INSERT", "UPDATE", "DELETE")),
        ("kubernetes_executions", ("SELECT", "INSERT", "UPDATE", "DELETE")),
        ("reconciliation_leases", ("SELECT", "INSERT", "UPDATE", "DELETE")),
        ("baselines", ("SELECT", "INSERT", "UPDATE", "DELETE")),
    ]:
        checks.extend(
            f"has_table_privilege('perfeng_runtime','perfeng_control.{table}','{privilege}')"
            for privilege in privileges
        )
    output = control_plane_database.psql(root, ("SELECT " + ", ".join(checks) + ";\n").encode())
    fields = output.strip().split("|")
    if any(field not in {"t", "f"} for field in fields):
        raise ValueError("Control-plane runtime database privileges are invalid")
    if tuple(field == "t" for field in fields) != EXPECTED_RUNTIME_PRIVILEGES:
        raise ValueError("Control-plane runtime database privileges are invalid")


def execute(action: str, root: Path = cluster.ROOT) -> None:
    """Create or verify the migration Job and exact runtime privileges."""
    release = validate_release()
    cluster.verify_local_docker()
    cluster.verify_context(root)
    control_plane_database.ensure_identities(root, create=False)
    control_plane_registry.ensure_secret(root, release.image, create=False)
    kube = postgres.kubectl(root)
    name = job_name(release)
    current = cluster.run([*kube, "get", "job", name, "--ignore-not-found", "-o", "json"])
    if current.strip():
        validate_job(json.loads(current), release)
    elif action == "health":
        raise ValueError("Control-plane migration Job is missing; run deploy first")
    else:
        cluster.run(
            [*kube, "create", "-f", "-"],
            input_data=json.dumps(job_document(release)).encode(),
        )
    cluster.run([*kube, "wait", "--for=condition=Complete", f"job/{name}", "--timeout=180s"])
    completed = json.loads(cluster.run([*kube, "get", "job", name, "-o", "json"]))
    validate_job(completed, release)
    if completed.get("status", {}).get("succeeded") != 1:
        raise ValueError("Control-plane migration Job did not complete exactly once")
    if action == "deploy":
        grant_runtime(root)
    verify_runtime(root)
    print(f"Local control-plane migration action completed: {action}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "health"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            execute(args.action)
        else:
            print("Preview: run the pinned migration image and establish runtime table grants.")
            print("Preview only. Add --execute to access GHCR, PostgreSQL or Kubernetes.")
    except (OSError, ValueError, TypeError, yaml.YAMLError, json.JSONDecodeError) as error:
        print(f"Control-plane migration error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
