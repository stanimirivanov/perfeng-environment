"""Guard the disposable migration Role's create-only denial and restoration."""

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


def source() -> dict[str, Any]:
    return next(
        obj
        for obj in yaml.safe_load_all(
            Path(
                "gitops/release-workflows/rbac/schema-migration/platform-observation.yaml"
            ).read_text()
        )
        if obj["kind"] == "Role"
    )


def narrowed() -> list[dict[str, Any]]:
    rules = deepcopy(source()["rules"])
    targets = [r for r in rules if r["apiGroups"] == ["batch"] and r["resources"] == ["jobs"]]
    assert len(targets) == 1 and targets[0]["verbs"] == ["create", "get", "watch"]
    targets[0]["verbs"] = ["get", "watch"]
    return rules


def validate(original: dict[str, Any], current: dict[str, Any], denied: bool) -> None:
    expected = source()
    for obj in (original, current):
        assert obj["apiVersion"] == expected["apiVersion"] and obj["kind"] == "Role"
        for key in ("namespace", "name"):
            assert obj["metadata"][key] == expected["metadata"][key]
        for key in ("uid", "resourceVersion"):
            assert isinstance(obj["metadata"][key], str) and obj["metadata"][key]
        assert not any(
            obj["metadata"].get(k) for k in ("deletionTimestamp", "ownerReferences", "finalizers")
        )
    assert original["rules"] == expected["rules"]
    ignored = {"resourceVersion", "managedFields"}
    assert {k: v for k, v in original["metadata"].items() if k not in ignored} == {
        k: v for k, v in current["metadata"].items() if k not in ignored
    }
    assert current["rules"] == (narrowed() if denied else original["rules"])


def patch(original: dict[str, Any], current: dict[str, Any], restore: bool) -> list[dict[str, Any]]:
    validate(original, current, denied=restore)
    return [
        *(
            {"op": "test", "path": "/metadata/" + k, "value": current["metadata"][k]}
            for k in ("uid", "resourceVersion")
        ),
        {"op": "test", "path": "/rules", "value": current["rules"]},
        {"op": "replace", "path": "/rules", "value": original["rules"] if restore else narrowed()},
    ]


def main() -> None:
    mode, directory = sys.argv[1:]
    root = Path(directory)
    prefix = "schema-parent-denied"
    original = json.loads((root / (prefix + ".role-original.json")).read_text())
    assert mode in {"prepare", "narrowed", "restore", "restored", "final"}
    if mode == "prepare":
        value = patch(original, original, False)
        (root / (prefix + ".narrow-patch.json")).write_text(json.dumps(value) + "\n")
        return
    current = json.loads(
        (
            root / (prefix + ".role-" + ("current" if mode == "restore" else mode) + ".json")
        ).read_text()
    )
    validate(original, current, denied=mode not in {"restored", "final"})
    if mode == "restore":
        # Called only after the complete-parent refusal and retention gates pass.
        (root / (prefix + ".restore-patch.json")).write_text(
            json.dumps(patch(original, current, True)) + "\n"
        )


if __name__ == "__main__":
    main()
