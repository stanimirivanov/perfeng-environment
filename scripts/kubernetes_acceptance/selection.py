"""Resolve acceptance inputs from the immutable deployment selection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts import control_plane_release as release
from scripts.kubernetes_acceptance.errors import AcceptanceError
from scripts.kubernetes_benchmark_contract import document

TEST_ID = "pod-startup-k8s"
BASELINE_ID = "approved-local-pod-startup-contained"
BASELINE_VERSION = "1.0.0"


@dataclass(frozen=True)
class Selection:
    """Frozen request and reporting trust; never a reusable admission grant."""

    lock: release.DeploymentLock
    entry: dict[str, Any]
    policy: dict[str, Any]
    request: dict[str, Any]

    @property
    def baseline_path(self) -> str:
        """Return the sole baseline version selected by this integration policy."""
        return f"/v1/baselines/{BASELINE_ID}/versions/{BASELINE_VERSION}"


def candidate_revision(value: str | None) -> str:
    """Require explicit reviewed candidate source, not an inferred runner/HEAD label."""
    if not isinstance(value, str) or release.REVISION.fullmatch(value) is None:
        raise AcceptanceError(
            "Supply --candidate-git-sha with the reviewed pause candidate source revision"
        )
    if value == "0" * 40:
        raise AcceptanceError("A placeholder candidate source revision is not evidence")
    return value


def load(root: Path, revision: str) -> Selection:
    """Validate pinned siblings and freeze their exact benchmark inputs; may invoke Git/Helm."""
    candidate_revision(revision)
    lock = release.validate(
        root / "deployments/local-control-plane.json",
        root.parent / "perfeng-k8s",
        root.parent / "perfeng-control-plane",
    )
    read = release.revision_reader(
        root.parent / "perfeng-k8s", release.DEPLOYMENT_REPOSITORY, lock.deployment_revision
    )
    prefix = lock.bundle_path + "/"
    registry = document(read(prefix + "registry.json"))
    matches = [entry for entry in registry["entries"] if entry.get("testId") == TEST_ID]
    if len(matches) != 1:
        raise AcceptanceError("Select exactly one contained Kubernetes benchmark entry")
    entry = matches[0]
    policy_bytes = read(prefix + entry["policy"])
    policy = document(policy_bytes)
    if (
        entry["contractsVersion"] != "0.22.0"
        or entry["profile"] != "smoke"
        or entry["environment"]["id"] != "local-kind-integration"
        or len(entry["candidateImages"]) != 1
        or policy["spec"]["mode"] != "observe"
        or len(policy["spec"]["rules"]) != 1
        or policy["spec"]["rules"][0]["regression"]["reference"]
        != {"baselineId": BASELINE_ID, "version": BASELINE_VERSION}
    ):
        raise AcceptanceError(
            "Acceptance supports only the reviewed integration recipe and baseline"
        )
    request = {
        "testSuite": TEST_ID,
        "catalogue": entry["catalogue"],
        "profile": entry["profile"],
        "candidate": {"gitSha": revision, "image": entry["candidateImages"][0]},
        "environment": {key: entry["environment"][key] for key in ("id", "version", "sha256")},
        "policy": {
            "id": policy["metadata"]["name"],
            "version": policy["metadata"]["version"],
            "sha256": hashlib.sha256(policy_bytes).hexdigest(),
        },
    }
    return Selection(lock, entry, policy, request)
