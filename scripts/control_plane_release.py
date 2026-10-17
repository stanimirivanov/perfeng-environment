"""Validate the immutable local control-plane deployment selection."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

if __package__:
    from .workspace import canonical_origin, unique_object
else:
    from workspace import canonical_origin, unique_object

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "deployments" / "local-control-plane.json"
DEFAULT_K8S_ROOT = ROOT.parent / "perfeng-k8s"
DEFAULT_CONTROL_PLANE_ROOT = ROOT.parent / "perfeng-control-plane"
CONTROL_PLANE_REPOSITORY = "https://github.com/stanimirivanov/perfeng-control-plane.git"
DEPLOYMENT_REPOSITORY = "https://github.com/stanimirivanov/perfeng-k8s.git"
CONTROL_PLANE_IMAGE = re.compile(
    r"ghcr\.io/stanimirivanov/perfeng-control-plane@sha256:[0-9a-f]{64}"
)
REVISION = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
REPOSITORY_PATH = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
RUNTIME_CONFIG_MAP = re.compile(r"perfeng-control-plane-runtime-[0-9a-f]{12}")
REGISTRY_CONFIG_MAP = re.compile(r"perfeng-runtime-registry-[0-9a-f]{12}")
CONTROL_PLANE_HTTP_PORT = 8080


@dataclass(frozen=True)
class DeploymentLock:
    """Immutable identities selected for one local control-plane deployment."""

    image: str
    source_revision: str
    deployment_revision: str
    chart_path: str
    chart_version: str
    bundle_path: str
    bundle_version: str
    contracts_version: str
    runtime_config_map: str
    registry_config_map: str
    entry_contracts_versions: tuple[str, ...] = ()
    package_path: str = ""
    package_provenance_revision: str = ""


def mapping(value: Any, fields: set[str], location: str) -> Mapping[str, Any]:
    """Return an exact string-keyed object or reject it."""
    if (
        not isinstance(value, Mapping)
        or any(not isinstance(key, str) for key in value)
        or set(value) != fields
    ):
        raise ValueError(f"Invalid {location} fields")
    return value


def string(value: Any, pattern: re.Pattern[str], location: str) -> str:
    """Return a string matching the complete required syntax."""
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"Invalid {location}")
    return value


def portable_path(value: Any, location: str) -> str:
    """Return a normalized, non-traversing repository-relative path."""
    if not isinstance(value, str):
        raise ValueError(f"Invalid {location}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or REPOSITORY_PATH.fullmatch(value) is None
    ):
        raise ValueError(f"Invalid {location}")
    return value


def load_lock(path: Path = DEFAULT_LOCK) -> DeploymentLock:
    """Load and strictly validate the deployment-lock document."""
    content = path.resolve(strict=True).read_text(encoding="utf-8")
    value = json.loads(content, object_pairs_hook=unique_object)
    root = mapping(value, {"schemaVersion", "controlPlane", "deployment"}, "lock")
    if type(root["schemaVersion"]) is not int or root["schemaVersion"] != 1:
        raise ValueError("Unsupported deployment lock version")

    control_plane = mapping(
        root["controlPlane"],
        {"sourceRepository", "sourceRevision", "image"},
        "control-plane",
    )
    if control_plane["sourceRepository"] != CONTROL_PLANE_REPOSITORY:
        raise ValueError("Invalid control-plane source repository")
    source_revision = string(control_plane["sourceRevision"], REVISION, "source revision")
    image = string(control_plane["image"], CONTROL_PLANE_IMAGE, "control-plane image")

    deployment = mapping(
        root["deployment"],
        {"repository", "revision", "chart", "runtimeBundle", "runtimePackage", "runtimeResources"},
        "deployment",
    )
    if deployment["repository"] != DEPLOYMENT_REPOSITORY:
        raise ValueError("Invalid deployment repository")
    deployment_revision = string(deployment["revision"], REVISION, "deployment revision")
    chart = mapping(deployment["chart"], {"path", "version"}, "chart")
    bundle_fields = {"path", "version", "contractsVersion"}
    if (
        isinstance(deployment["runtimeBundle"], Mapping)
        and "entryContractsVersions" in deployment["runtimeBundle"]
    ):
        bundle_fields.add("entryContractsVersions")
    bundle = mapping(
        deployment["runtimeBundle"],
        bundle_fields,
        "runtime bundle",
    )
    chart_path = portable_path(chart["path"], "chart path")
    chart_version = string(chart["version"], VERSION, "chart version")
    bundle_path = portable_path(bundle["path"], "runtime-bundle path")
    bundle_version = string(bundle["version"], VERSION, "runtime-bundle version")
    contracts_version = bundle["contractsVersion"]
    versions: tuple[str, ...] = ()
    if contracts_version == "registry":
        selected = bundle.get("entryContractsVersions")
        if not isinstance(selected, list) or not selected:
            raise ValueError("Registry mode requires explicit entry contracts versions")
        versions = tuple(string(item, VERSION, "entry contracts version") for item in selected)
        if versions != tuple(sorted(set(versions))):
            raise ValueError("Entry contracts versions must be sorted and unique")
    else:
        contracts_version = string(contracts_version, VERSION, "contracts version")
        if "entryContractsVersions" in bundle:
            raise ValueError("Uniform mode cannot select entry contracts versions")
    if PurePosixPath(bundle_path).name != bundle_version:
        raise ValueError("Runtime-bundle path and version differ")
    package = mapping(
        deployment["runtimePackage"],
        {"path", "version", "provenanceRevision"},
        "runtime package",
    )
    package_path = portable_path(package["path"], "runtime-package path")
    package_version = string(package["version"], VERSION, "runtime-package version")
    package_provenance_revision = string(
        package["provenanceRevision"], REVISION, "runtime-package provenance revision"
    )
    if package_version != bundle_version or package_path != (
        f"packages/runtime-inputs/{PurePosixPath(bundle_path).parent.name}-{package_version}"
    ):
        raise ValueError("Runtime package and bundle versions differ")
    resources = mapping(
        deployment["runtimeResources"],
        {"runtimeConfigMap", "registryConfigMap"},
        "runtime resources",
    )
    runtime_config_map = string(
        resources["runtimeConfigMap"], RUNTIME_CONFIG_MAP, "runtime ConfigMap"
    )
    registry_config_map = string(
        resources["registryConfigMap"], REGISTRY_CONFIG_MAP, "registry ConfigMap"
    )

    return DeploymentLock(
        image=image,
        source_revision=source_revision,
        deployment_revision=deployment_revision,
        chart_path=chart_path,
        chart_version=chart_version,
        bundle_path=bundle_path,
        bundle_version=bundle_version,
        contracts_version=contracts_version,
        runtime_config_map=runtime_config_map,
        registry_config_map=registry_config_map,
        entry_contracts_versions=versions,
        package_path=package_path,
        package_provenance_revision=package_provenance_revision,
    )


def git(root: Path, *arguments: str) -> bytes:
    """Run a bounded, read-only Git query without exposing command output on failure."""
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *arguments],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Git could not inspect the deployment source") from error
    if completed.returncode:
        raise ValueError("Git could not inspect the pinned deployment revision")
    return completed.stdout


def revision_reader(root: Path, repository: str, revision: str) -> Callable[[str], bytes]:
    """Return a reader for tracked files at one verified repository revision."""
    if root.is_symlink() or root.is_junction():
        raise ValueError("Deployment source must be an ordinary repository directory")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Deployment source must be an ordinary repository directory")
    origin = git(root, "remote", "get-url", "origin").decode("utf-8").strip()
    if canonical_origin(origin) != canonical_origin(repository):
        raise ValueError("Deployment source has the wrong Git origin")
    if git(root, "cat-file", "-t", revision).decode("ascii").strip() != "commit":
        raise ValueError("Deployment revision is not a commit")

    def read(path: str) -> bytes:
        portable_path(path, "deployment source path")
        return git(root, "show", f"{revision}:{path}")

    return read


def validate_deployment_source(lock: DeploymentLock, read: Callable[[str], bytes]) -> None:
    """Validate chart and runtime-bundle identities at the pinned Git commit."""
    chart = yaml.safe_load(read(f"{lock.chart_path}/Chart.yaml"))
    if not isinstance(chart, Mapping) or chart.get("name") != "perfeng-control-plane":
        raise ValueError("Pinned chart has the wrong identity")
    if chart.get("version") != lock.chart_version:
        raise ValueError("Pinned chart version differs from the deployment lock")
    values = yaml.safe_load(read(f"{lock.chart_path}/values.yaml"))
    if not isinstance(values, Mapping) or values.get("httpPort") != CONTROL_PLANE_HTTP_PORT:
        raise ValueError("Pinned chart HTTP port differs from the environment contract")

    manifest_path = f"{lock.bundle_path}/registry.json"
    manifest = json.loads(read(manifest_path), object_pairs_hook=unique_object)
    if not isinstance(manifest, Mapping) or manifest.get("schemaVersion") != 1:
        raise ValueError("Pinned runtime bundle has an invalid registry manifest")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Pinned runtime bundle has no registry entries")

    required = {"registry.json", "jobs/analysis.yaml", "jobs/reporting.yaml"}
    versions = set(lock.entry_contracts_versions or (lock.contracts_version,))
    observed = set()
    for entry in entries:
        if (
            not isinstance(entry, Mapping)
            or not isinstance(entry.get("contractsVersion"), str)
            or entry.get("contractsVersion") not in versions
        ):
            raise ValueError("Runtime entry differs from the locked contracts version")
        observed.add(entry["contractsVersion"])
        for field in ("policy", "executionTemplate"):
            if field in entry:
                required.add(portable_path(entry[field], f"runtime entry {field}"))
        if "benchmark" in entry:
            benchmark = entry["benchmark"]
            if not isinstance(benchmark, Mapping):
                raise ValueError("Invalid runtime benchmark selection")
            required.add(portable_path(benchmark.get("environmentFile"), "benchmark environment"))
    if observed != versions:
        raise ValueError("Runtime entry contracts versions differ from the explicit selection")
    for relative in required:
        read(f"{lock.bundle_path}/{relative}")


def validate(
    lock_path: Path = DEFAULT_LOCK,
    k8s_root: Path = DEFAULT_K8S_ROOT,
    control_plane_root: Path = DEFAULT_CONTROL_PLANE_ROOT,
) -> DeploymentLock:
    """Validate the lock, chart, bundle and package at one deployment commit."""
    lock = load_lock(lock_path)
    source = revision_reader(
        control_plane_root,
        CONTROL_PLANE_REPOSITORY,
        lock.source_revision,
    )
    source("Dockerfile")
    source("cmd/control-plane/main.go")
    if lock.contracts_version == "registry":
        if b'RegistryContractsVersion = "registry"' not in source(
            "internal/app/artifact_config.go"
        ):
            raise ValueError(
                "Selected control-plane source does not support registry contracts mode"
            )
    read = revision_reader(k8s_root, DEPLOYMENT_REPOSITORY, lock.deployment_revision)
    validate_deployment_source(lock, read)
    if lock.package_path:
        if __package__:
            from . import control_plane_runtime
        else:
            import control_plane_runtime

        control_plane_runtime.validate_package_source(lock, read)
    return lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--k8s-root", type=Path, default=DEFAULT_K8S_ROOT)
    parser.add_argument("--control-plane-root", type=Path, default=DEFAULT_CONTROL_PLANE_ROOT)
    args = parser.parse_args()
    try:
        validate(args.lock, args.k8s_root, args.control_plane_root)
        print("Validated immutable control-plane image, chart, bundle and runtime package.")
    except (
        UnicodeError,
        ValueError,
        OSError,
        TypeError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as error:
        print(f"Control-plane release error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
