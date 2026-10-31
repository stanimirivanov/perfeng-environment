"""Bind reusable phase predicates to real nested nodes; never persist projections."""

from copy import deepcopy
from datetime import datetime
from typing import Any

PHASES = {
    "migrate": "perfeng-release-schema-migration-v2",
    "qualify": "perfeng-release-schema-qualification-v1",
    "grant": "perfeng-release-runtime-grants-v2",
}


def controller_form(value: Any) -> Any:
    """Match the existing Argo empty-struct normalization, not arbitrary defaults."""
    if isinstance(value, list):
        return [controller_form(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: controller_form(item)
        for key, item in value.items()
        if not (
            (key in {"inputs", "outputs", "metadata", "arguments"} and item == {})
            or (key == "setOwnerReference" and item is False)
        )
    }


def scopes(
    workflow: dict[str, Any],
    wanted: dict[str, Any],
    parameters: dict[str, str],
    *,
    migration_failed: bool = False,
) -> dict[str, dict[str, Any]]:
    """Validate the enclosing execution before constructing any in-memory subview."""
    metadata, spec, status = (workflow[key] for key in ("metadata", "spec", "status"))
    assert metadata["namespace"] == "argo-workflows" and metadata["uid"]
    assert not metadata.get("deletionTimestamp")
    assert spec["workflowTemplateRef"] == {"name": "perfeng-release-database-prepare-v1"}
    assert set(spec) <= {
        "workflowTemplateRef",
        "arguments",
        "serviceAccountName",
        "entrypoint",
        "activeDeadlineSeconds",
        "synchronization",
    }
    arguments = spec["arguments"]["parameters"]
    assert len(arguments) == 4 and {p["name"]: p["value"] for p in arguments} == parameters
    stored = status["storedWorkflowTemplateSpec"]
    for key in (
        "templates",
        "serviceAccountName",
        "entrypoint",
        "activeDeadlineSeconds",
        "synchronization",
    ):
        assert controller_form(stored[key]) == controller_form(wanted[key])
        if key in spec:
            assert controller_form(spec[key]) == controller_form(wanted[key])
    assert status["phase"] in ({"Failed", "Error"} if migration_failed else {"Succeeded"})
    nodes = status["nodes"]
    assert all(key == node["id"] for key, node in nodes.items())
    root_id = metadata["name"]
    root = nodes[root_id]
    assert root["type"] == "Steps" and root["templateName"] == "prepare"
    assert root["phase"] == status["phase"]
    roots = {}
    for phase, reference in PHASES.items():
        found = [
            n
            for n in nodes.values()
            if n.get("boundaryID") == root_id and n["displayName"] == phase and n["type"] == "Steps"
        ]
        if migration_failed and phase != "migrate":
            assert not found or all(n["phase"] in {"Skipped", "Omitted"} for n in found)
            continue
        assert len(found) == 1
        node = found[0]
        assert node["templateRef"] == {"name": reference, "template": phase}
        assert node["phase"] in ({"Failed", "Error"} if migration_failed else {"Succeeded"})
        roots[phase] = node
    root_phases = {node["id"]: phase for phase, node in roots.items()}

    def owner(node: dict[str, Any]) -> str | None:
        seen = set()
        while node["id"] != root_id:
            identity = node["id"]
            assert identity not in seen
            seen.add(identity)
            if identity in root_phases:
                return root_phases[identity]
            node = nodes[node["boundaryID"]]
        return None

    selected: dict[str, dict[str, Any]] = {phase: {} for phase in roots}
    for identity, node in nodes.items():
        phase = owner(node)
        if phase:
            if node["phase"] in {"Succeeded", "Failed", "Error"}:
                start, finish = (
                    datetime.fromisoformat(node[k]) for k in ("startedAt", "finishedAt")
                )
                first, last = (
                    datetime.fromisoformat(roots[phase][k]) for k in ("startedAt", "finishedAt")
                )
                assert start.utcoffset() is not None and first <= start <= finish <= last
            selected[phase][identity] = deepcopy(node)
        elif identity != root_id:
            assert node["type"] == "StepGroup" or (
                migration_failed
                and node["displayName"] in {"qualify", "grant"}
                and node["phase"] in {"Skipped", "Omitted"}
                and node["type"] != "Pod"
            )
            assert not node.get("outputs", {}).get("parameters")
    views = {}
    for phase, phase_nodes in selected.items():
        view = deepcopy(workflow)
        phase_root = phase_nodes.pop(roots[phase]["id"])
        # Only the root lookup/ref are adapted for existing standalone predicates.
        # Every observed node/worker/Job UID and output remains the raw value.
        phase_root["displayName"] = root_id
        phase_nodes[root_id] = phase_root
        view["status"]["nodes"] = phase_nodes
        view["status"]["phase"] = phase_root["phase"]
        view["spec"]["workflowTemplateRef"] = {"name": PHASES[phase]}
        views[phase] = view
    return views
