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
        root["deployment"], {"repository", "revision", "chart", "runtimeBundle"}, "deployment"
    )
    if deployment["repository"] != DEPLOYMENT_REPOSITORY:
        raise ValueError("Invalid deployment repository")
    deployment_revision = string(deployment["revision"], REVISION, "deployment revision")
    chart = mapping(deployment["chart"], {"path", "version"}, "chart")
    bundle = mapping(
        deployment["runtimeBundle"],
        {"path", "version", "contractsVersion"},
        "runtime bundle",
    )
    chart_path = portable_path(chart["path"], "chart path")
    chart_version = string(chart["version"], VERSION, "chart version")
    bundle_path = portable_path(bundle["path"], "runtime-bundle path")
    bundle_version = string(bundle["version"], VERSION, "runtime-bundle version")
    contracts_version = string(bundle["contractsVersion"], VERSION, "contracts version")
    if PurePosixPath(bundle_path).name != bundle_version:
        raise ValueError("Runtime-bundle path and version differ")

    return DeploymentLock(
        image=image,
        source_revision=source_revision,
        deployment_revision=deployment_revision,
        chart_path=chart_path,
        chart_version=chart_version,
        bundle_path=bundle_path,
        bundle_version=bundle_version,
        contracts_version=contracts_version,
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
    root = root.absolute()
    resolved = root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink() or resolved != root:
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

    manifest_path = f"{lock.bundle_path}/registry.json"
    manifest = json.loads(read(manifest_path), object_pairs_hook=unique_object)
    if not isinstance(manifest, Mapping) or manifest.get("schemaVersion") != 1:
        raise ValueError("Pinned runtime bundle has an invalid registry manifest")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Pinned runtime bundle has no registry entries")

    required = {"registry.json", "jobs/analysis.yaml", "jobs/reporting.yaml"}
    for entry in entries:
        if (
            not isinstance(entry, Mapping)
            or entry.get("contractsVersion") != lock.contracts_version
        ):
            raise ValueError("Runtime entry differs from the locked contracts version")
        for field in ("policy", "executionTemplate"):
            if field in entry:
                required.add(portable_path(entry[field], f"runtime entry {field}"))
    for relative in required:
        read(f"{lock.bundle_path}/{relative}")


def validate(
    lock_path: Path = DEFAULT_LOCK,
    k8s_root: Path = DEFAULT_K8S_ROOT,
    control_plane_root: Path = DEFAULT_CONTROL_PLANE_ROOT,
) -> DeploymentLock:
    """Validate the lock and its exact deployment-source commit."""
    lock = load_lock(lock_path)
    source = revision_reader(
        control_plane_root,
        CONTROL_PLANE_REPOSITORY,
        lock.source_revision,
    )
    source("Dockerfile")
    source("cmd/control-plane/main.go")
    read = revision_reader(k8s_root, DEPLOYMENT_REPOSITORY, lock.deployment_revision)
    validate_deployment_source(lock, read)
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
        print("Validated immutable control-plane image, chart and runtime-bundle selection.")
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
