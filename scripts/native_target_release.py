"""Validate the immutable image-backed target selection without reconciling workloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import control_plane_release as releases
    from .workspace import unique_object
else:
    import control_plane_release as releases
    from workspace import unique_object

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "deployments/local-native-target.json"
APPLICATION = "perfeng-local-native-search"
NAMESPACE = "perf-platform"
RELEASE = "native-search"
PULL_SECRET = "perfeng-control-plane-registry"
REPOSITORY = "ghcr.io/stanimirivanov/perfeng-native-search"
SOURCE = "https://github.com/stanimirivanov/perfeng-environment"
CHART_PATH = "charts/native-search"
VERSION = "1.0.0"
FILE_NAMES = ("index.html", "search.js")


@dataclass(frozen=True)
class TargetSelection:
    """Published provenance and immutable workload source; not host qualification or a runtime bundle."""

    candidate: Mapping[str, Any]
    deployment_revision: str

    @property
    def image(self) -> str:
        """Return the validated canonical candidate reference."""
        return self.candidate["image"]


def load_selection(path: Path = LOCK) -> TargetSelection:
    """Reject unknown fields, unreviewed topology and mutable or synthetic release identities."""
    value = json.loads(path.read_bytes(), object_pairs_hook=unique_object)
    root = releases.mapping(
        value, {"schemaVersion", "candidate", "deployment"}, "native target lock"
    )
    candidate = releases.mapping(
        root["candidate"],
        {"schemaVersion", "name", "version", "image", "source", "targetSha256", "platform"},
        "candidate",
    )
    if (
        type(root["schemaVersion"]) is not int
        or root["schemaVersion"] != 1
        or type(candidate["schemaVersion"]) is not int
        or candidate["schemaVersion"] != 1
        or candidate["name"] != "perfeng-native-search"
        or candidate["version"] != VERSION
        or candidate["platform"] != "linux/amd64"
    ):
        raise ValueError("Unsupported native candidate identity or platform")
    image = candidate["image"]
    if not isinstance(image, str) or not image.startswith(REPOSITORY + "@sha256:"):
        raise ValueError("Candidate must use the reviewed digest-only image repository")
    digest = image.removeprefix(REPOSITORY + "@sha256:")
    source = releases.mapping(candidate["source"], {"repository", "gitSha"}, "candidate source")
    if source["repository"] != SOURCE:
        raise ValueError("Candidate source repository differs")
    for value, length in ((digest, 64), (candidate["targetSha256"], 64), (source["gitSha"], 40)):
        if (
            not isinstance(value, str)
            or len(value) != length
            or any(character not in "0123456789abcdef" for character in value)
            or len(set(value)) <= 1
        ):
            raise ValueError("Invalid or synthetic native candidate identity")
    deployment = releases.mapping(
        root["deployment"],
        {"repository", "revision", "chart", "namespace", "releaseName", "imagePullSecret"},
        "native target deployment",
    )
    revision = releases.string(deployment["revision"], releases.REVISION, "native target revision")
    if (
        deployment
        != {
            "repository": releases.DEPLOYMENT_REPOSITORY,
            "revision": revision,
            "chart": {"path": CHART_PATH, "version": VERSION},
            "namespace": NAMESPACE,
            "releaseName": RELEASE,
            "imagePullSecret": PULL_SECRET,
        }
        or len(set(revision)) <= 1
    ):
        raise ValueError("Native target deployment topology differs")
    return TargetSelection(candidate, revision)


def content_identity(files: Mapping[str, bytes]) -> str:
    """Hash the two exact fixture files in the publisher's name/NUL/bytes/NUL order."""
    digest = hashlib.sha256()
    for name in FILE_NAMES:
        digest.update(name.encode("utf-8") + b"\0" + files[name] + b"\0")
    return digest.hexdigest()


def expected_files(root: Path = ROOT) -> dict[str, bytes]:
    """Require checkout expectations to match the selected candidate; never infer new checksums."""
    selection = load_selection(root / "deployments/local-native-target.json")
    files = {name: (root / "native-gateway/search" / name).read_bytes() for name in FILE_NAMES}
    if content_identity(files) != selection.candidate["targetSha256"]:
        raise ValueError("Local expected target bytes differ from the selected published candidate")
    return files


def application_document(selection: TargetSelection) -> dict[str, Any]:
    """Describe the sole GitOps owner with conservative initial sync policy."""
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": APPLICATION,
            "namespace": "argocd",
            "labels": {
                "app.kubernetes.io/managed-by": "perfeng-environment",
                "perfeng.io/environment": "local",
                "perfeng.io/workload": RELEASE,
            },
        },
        "spec": {
            "project": "perfeng-local",
            "source": {
                "repoURL": releases.DEPLOYMENT_REPOSITORY,
                "targetRevision": selection.deployment_revision,
                "path": CHART_PATH,
                "helm": {
                    "releaseName": RELEASE,
                    "valuesObject": {"image": selection.image, "imagePullSecret": PULL_SECRET},
                },
            },
            "destination": {"server": "https://kubernetes.default.svc", "namespace": NAMESPACE},
            "syncPolicy": {
                "automated": {"enabled": True, "prune": False, "selfHeal": False},
                "syncOptions": ["CreateNamespace=false"],
            },
        },
    }


def validate_application(value: dict[str, Any], selection: TargetSelection) -> None:
    """Require exact desired state while allowing server-added metadata and status."""
    expected = application_document(selection)
    metadata = value.get("metadata", {})
    if (
        value.get("apiVersion") != expected["apiVersion"]
        or value.get("kind") != "Application"
        or value.get("spec") != expected["spec"]
        or not isinstance(metadata, dict)
        or metadata.get("name") != APPLICATION
        or metadata.get("namespace") != "argocd"
        or not isinstance(metadata.get("labels"), dict)
        or any(
            metadata["labels"].get(key) != item
            for key, item in expected["metadata"]["labels"].items()
        )
    ):
        raise ValueError("Native target Application differs from the reviewed selection")


def validate_sources(
    selection: TargetSelection,
    k8s_read: Callable[[str], bytes],
    source_read: Callable[[str], bytes],
) -> None:
    """Bind chart, published record and expected fixture bytes to their exact Git revisions."""
    chart = yaml.safe_load(k8s_read(f"{CHART_PATH}/Chart.yaml"))
    if not isinstance(chart, dict) or any(
        chart.get(key) != value
        for key, value in {
            "apiVersion": "v2",
            "name": "perfeng-native-search",
            "version": VERSION,
            "appVersion": VERSION,
        }.items()
    ):
        raise ValueError("Pinned native chart identity differs")
    identity = json.loads(
        k8s_read(f"releases/native-search/{VERSION}/image.json"), object_pairs_hook=unique_object
    )
    if identity != selection.candidate:
        raise ValueError("Pinned native candidate provenance differs")
    files = {name: source_read(f"native-gateway/search/{name}") for name in FILE_NAMES}
    if content_identity(files) != selection.candidate["targetSha256"]:
        raise ValueError("Pinned candidate source bytes differ")


def validate(root: Path = ROOT, k8s_root: Path | None = None) -> TargetSelection:
    """Inspect pinned Git content and lint/render its chart offline; never use sibling working files."""
    selection = load_selection(root / "deployments/local-native-target.json")
    expected_files(root)
    read = releases.revision_reader(
        k8s_root or root.parent / "perfeng-k8s",
        releases.DEPLOYMENT_REPOSITORY,
        selection.deployment_revision,
    )
    source = releases.revision_reader(root, SOURCE, selection.candidate["source"]["gitSha"])
    validate_sources(selection, read, source)
    with tempfile.TemporaryDirectory() as directory:
        chart = Path(directory) / "native-search"
        for name in (
            "Chart.yaml",
            "values.yaml",
            "values.schema.json",
            "templates/deployment.yaml",
            "templates/service.yaml",
            "templates/service-account.yaml",
        ):
            destination = chart / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(read(f"{CHART_PATH}/{name}"))
        values = [
            "--set-string",
            f"image={selection.image}",
            "--set-string",
            f"imagePullSecret={PULL_SECRET}",
        ]
        for arguments in (
            ["lint", str(chart), "--strict", *values],
            ["template", RELEASE, str(chart), "--namespace", NAMESPACE, *values],
        ):
            try:
                result = subprocess.run(
                    ["helm", *arguments], capture_output=True, check=False, timeout=60
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise ValueError("Helm could not validate the pinned native chart") from error
            if result.returncode:
                raise ValueError("Helm rejected the selected native chart")
    return selection


def main(argv: list[str] | None = None) -> int:
    """Expose a bounded read-only pinned selection check, distinct from cluster health."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate"])
    parser.add_argument("--k8s-root", type=Path)
    args = parser.parse_args(argv)
    try:
        validate(k8s_root=args.k8s_root)
        print("Validated immutable native candidate, chart, provenance and pinned source bytes.")
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, yaml.YAMLError) as error:
        print(f"Native target release error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
