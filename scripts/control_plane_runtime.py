"""Assemble immutable local control-plane runtime inputs."""

import argparse
import base64
import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster, control_plane_database, postgres, storage_access
    from .control_plane_release import (
        DEFAULT_K8S_ROOT,
        DEPLOYMENT_REPOSITORY,
        REVISION,
        DeploymentLock,
        revision_reader,
    )
    from .control_plane_release import (
        validate as validate_release,
    )
else:
    import cluster
    import control_plane_database
    import postgres
    import storage_access
    from control_plane_release import (
        DEFAULT_K8S_ROOT,
        DEPLOYMENT_REPOSITORY,
        REVISION,
        DeploymentLock,
        revision_reader,
    )
    from control_plane_release import (
        validate as validate_release,
    )

RUNTIME_CONFIG_PREFIX = "perfeng-control-plane-runtime"
RUNTIME_SECRET = "perfeng-control-plane-runtime"
AUTHENTICATION_SECRET = "perfeng-control-plane-authentication"
REGISTRY_CONFIG_PREFIX = "perfeng-runtime-registry"
OWNER = postgres.OWNER
PERMISSIONS = [
    "runs:create",
    "runs:read",
    "runs:cancel",
    "baselines:create",
    "baselines:read",
    "baselines:transition",
]


def runtime_configuration(release: DeploymentLock) -> dict[str, str]:
    """Return the complete non-secret local process policy supplied by envFrom."""
    return {
        "PERFENG_HTTP_READ_HEADER_TIMEOUT": "5s",
        "PERFENG_HTTP_READ_TIMEOUT": "15s",
        "PERFENG_HTTP_WRITE_TIMEOUT": "30s",
        "PERFENG_HTTP_IDLE_TIMEOUT": "60s",
        "PERFENG_HTTP_SHUTDOWN_TIMEOUT": "30s",
        "PERFENG_HTTP_MAX_HEADER_BYTES": "32768",
        "PERFENG_WORKER_CAPACITY": "2",
        "PERFENG_WORKER_LEASE_TTL": "30s",
        "PERFENG_WORKER_RENEW_EVERY": "10s",
        "PERFENG_WORKER_POLL_EVERY": "1s",
        "PERFENG_WORKER_FAILURE_DELAY": "5s",
        "PERFENG_KUBERNETES_REQUEST_TIMEOUT": "10s",
        "PERFENG_KUBERNETES_QPS": "20",
        "PERFENG_KUBERNETES_BURST": "30",
        "PERFENG_S3_REGION": "us-east-1",
        "PERFENG_S3_ENDPOINT": "http://seaweedfs.perf-platform.svc.cluster.local:8333",
        "PERFENG_S3_PATH_STYLE": "true",
        "PERFENG_S3_ALLOW_HTTP": "true",
        "PERFENG_S3_MAX_ATTEMPTS": "3",
        "PERFENG_ARTIFACT_BUCKET": "perfeng-artifacts",
        "PERFENG_RAW_MANIFEST_FORMAT": "raw-result/v2",
        "PERFENG_ARTIFACT_MAX_READ_BYTES": "67108864",
        "PERFENG_CONTRACTS_VERSION": release.contracts_version,
        "PERFENG_RECONCILIATION_VALIDATION_ATTEMPT_TIMEOUT": "8s",
        "PERFENG_RECONCILIATION_PROVISIONING_ATTEMPT_TIMEOUT": "10s",
        "PERFENG_RECONCILIATION_EXECUTION_RETRY_AFTER": "5s",
        "PERFENG_RECONCILIATION_CANCELLATION_POLL_EVERY": "1s",
        "PERFENG_RECONCILIATION_COLLECTION_ATTEMPT_TIMEOUT": "12s",
        "PERFENG_RECONCILIATION_COLLECTION_RETRY_AFTER": "6s",
        "PERFENG_RECONCILIATION_ANALYSIS_ATTEMPT_TIMEOUT": "14s",
        "PERFENG_RECONCILIATION_ANALYSIS_RETRY_AFTER": "7s",
        "PERFENG_RECONCILIATION_REPORTING_ATTEMPT_TIMEOUT": "16s",
        "PERFENG_RECONCILIATION_REPORTING_RETRY_AFTER": "8s",
    }


def content_addressed_name(prefix: str, data: dict[str, str]) -> str:
    """Return the deterministic Kubernetes identity for non-secret data."""
    payload = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:12]}"


def validate_config_map_selection(
    release: DeploymentLock,
    runtime_data: dict[str, str],
    registry_data: dict[str, str],
) -> None:
    """Require the lock to select the identities derived from reviewed bytes."""
    expected = {
        "runtime": content_addressed_name(RUNTIME_CONFIG_PREFIX, runtime_data),
        "registry": content_addressed_name(REGISTRY_CONFIG_PREFIX, registry_data),
    }
    selected = {
        "runtime": release.runtime_config_map,
        "registry": release.registry_config_map,
    }
    for purpose in expected:
        if selected[purpose] != expected[purpose]:
            raise ValueError(f"Locked {purpose} ConfigMap identity differs from reviewed bytes")


def selected_registry_paths(manifest_bytes: bytes) -> list[str]:
    """Return every mounted path selected by one registry manifest."""
    manifest = json.loads(manifest_bytes)
    paths = {"registry.json", "jobs/analysis.yaml", "jobs/reporting.yaml"}
    for entry in manifest["entries"]:
        paths.add(entry["policy"])
        if entry.get("executionTemplate"):
            paths.add(entry["executionTemplate"])
    return sorted(paths)


def registry_paths(release: DeploymentLock, k8s_root: Path = DEFAULT_K8S_ROOT) -> list[str]:
    """Return every mounted runtime path selected by the locked registry manifest."""
    read = revision_reader(k8s_root, DEPLOYMENT_REPOSITORY, release.deployment_revision)
    return selected_registry_paths(read(f"{release.bundle_path}/registry.json"))


def registry_files(release: DeploymentLock, k8s_root: Path = DEFAULT_K8S_ROOT) -> dict[str, str]:
    """Read every runtime file from the exact locked deployment commit."""
    read = revision_reader(k8s_root, DEPLOYMENT_REPOSITORY, release.deployment_revision)
    prefix = f"{release.bundle_path}/"
    paths = selected_registry_paths(read(prefix + "registry.json"))
    return {path.replace("/", "--"): read(prefix + path).decode("utf-8") for path in paths}


def object_document(
    kind: str, name: str, data: dict[str, str], release: DeploymentLock
) -> dict[str, Any]:
    """Build one immutable ConfigMap or Secret with locked provenance."""
    document: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": kind,
        "metadata": {
            "name": name,
            "namespace": postgres.NAMESPACE,
            "labels": {"app.kubernetes.io/managed-by": OWNER},
            "annotations": {"perfeng.io/deployment-revision": release.deployment_revision},
        },
        "immutable": True,
    }
    document["data" if kind == "ConfigMap" else "stringData"] = data
    if kind == "Secret":
        document["type"] = "Opaque"
    return document


def runtime_credentials(database: dict[str, Any], uploader: dict[str, Any]) -> dict[str, str]:
    """Assemble the service credentials from existing owned Secrets."""
    migration_password, runtime_password = control_plane_database.decode_secret(database)
    del migration_password
    storage = storage_access.decode(
        uploader,
        storage_access.UPLOADER_SECRET,
        {"access-key", "secret-key"},
    )
    return {
        "PERFENG_DATABASE_URL": control_plane_database.database_url(
            control_plane_database.RUNTIME_ROLE, runtime_password
        ),
        "AWS_ACCESS_KEY_ID": storage["access-key"],
        "AWS_SECRET_ACCESS_KEY": storage["secret-key"],
    }


def authentication(token: str) -> dict[str, str]:
    """Build the mounted digest document and separately retained client token."""
    if len(token) < 40 or any(character.isspace() for character in token):
        raise ValueError("Invalid local operator credential")
    document = {
        "schemaVersion": 1,
        "credentials": [
            {
                "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
                "principal": "local-operator",
                "permissions": PERMISSIONS,
            }
        ],
    }
    return {
        "credentials.json": json.dumps(document, separators=(",", ":")),
        "operator-token": token,
    }


def stored_data(value: dict[str, Any], kind: str) -> dict[str, str]:
    """Return stored data without exposing it to output."""
    data = value.get("data")
    if not isinstance(data, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError("Runtime resource data is invalid")
    if kind == "ConfigMap":
        return data
    try:
        return {key: base64.b64decode(item, validate=True).decode() for key, item in data.items()}
    except (ValueError, UnicodeError):
        raise ValueError("Runtime Secret data is invalid") from None


def validate_resource(value: dict[str, Any], expected: dict[str, Any]) -> None:
    """Require an immutable resource with owned bytes and recorded Git provenance."""
    kind = expected["kind"]
    source = "data" if kind == "ConfigMap" else "stringData"
    provenance = (
        value.get("metadata", {}).get("annotations", {}).get("perfeng.io/deployment-revision")
    )
    if (
        value.get("kind") != kind
        or value.get("immutable") is not True
        or value.get("metadata", {}).get("name") != expected["metadata"]["name"]
        or value.get("metadata", {}).get("namespace") != postgres.NAMESPACE
        or value.get("metadata", {}).get("labels") != expected["metadata"]["labels"]
        or not isinstance(provenance, str)
        or REVISION.fullmatch(provenance) is None
        or stored_data(value, kind) != expected[source]
    ):
        raise ValueError(
            f"Existing {kind} {expected['metadata']['name']} differs from reviewed input"
        )


def execute(action: str, root: Path = cluster.ROOT) -> None:
    """Create or verify the complete external runtime input set."""
    release = validate_release()
    runtime_data = runtime_configuration(release)
    registry_data = registry_files(release)
    validate_config_map_selection(release, runtime_data, registry_data)
    cluster.verify_local_docker()
    cluster.verify_context(root)
    kube = postgres.kubectl(root)
    database = json.loads(
        cluster.run([*kube, "get", "secret", control_plane_database.SECRET, "-o", "json"])
    )
    uploader = json.loads(
        cluster.run([*kube, "get", "secret", storage_access.UPLOADER_SECRET, "-o", "json"])
    )
    credentials = runtime_credentials(database, uploader)
    resources = [
        object_document("ConfigMap", release.runtime_config_map, runtime_data, release),
        object_document("Secret", RUNTIME_SECRET, credentials, release),
        object_document("ConfigMap", release.registry_config_map, registry_data, release),
    ]
    current: dict[tuple[str, str], dict[str, Any] | None] = {}
    for resource in resources:
        kind = resource["kind"].lower()
        name = resource["metadata"]["name"]
        text = cluster.run([*kube, "get", kind, name, "--ignore-not-found", "-o", "json"])
        current[(resource["kind"], name)] = json.loads(text) if text.strip() else None
    auth_text = cluster.run(
        [*kube, "get", "secret", AUTHENTICATION_SECRET, "--ignore-not-found", "-o", "json"]
    )
    auth_current = json.loads(auth_text) if auth_text.strip() else None
    if auth_current is not None:
        auth_data = stored_data(auth_current, "Secret")
        auth = object_document(
            "Secret",
            AUTHENTICATION_SECRET,
            authentication(auth_data.get("operator-token", "")),
            release,
        )
        validate_resource(auth_current, auth)
    elif action == "health":
        raise ValueError("Control-plane authentication Secret is missing; run deploy first")
    else:
        auth = object_document(
            "Secret", AUTHENTICATION_SECRET, authentication(secrets.token_urlsafe(32)), release
        )
    planned = [*resources, auth]
    for resource in planned:
        name = resource["metadata"]["name"]
        key = (resource["kind"], name)
        existing = auth_current if name == AUTHENTICATION_SECRET else current[key]
        if existing is not None:
            validate_resource(existing, resource)
        elif action == "health":
            raise ValueError(f"Control-plane runtime resource {name} is missing; run deploy first")
    if action == "deploy":
        for resource in planned:
            name = resource["metadata"]["name"]
            key = (resource["kind"], name)
            existing = auth_current if name == AUTHENTICATION_SECRET else current[key]
            if existing is not None:
                continue
            cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(resource).encode())
    print(f"Local control-plane runtime inputs validated: {action}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate", "deploy", "health"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "validate":
            release = validate_release()
            validate_config_map_selection(
                release,
                runtime_configuration(release),
                registry_files(release),
            )
            print("Validated content-addressed control-plane runtime selection.")
        elif args.execute:
            execute(args.action)
        else:
            print("Preview: assemble immutable control-plane configuration and credentials.")
            print("Preview only. Add --execute to access Kubernetes or create resources.")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        yaml.YAMLError,
        json.JSONDecodeError,
    ) as error:
        print(f"Control-plane runtime error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
