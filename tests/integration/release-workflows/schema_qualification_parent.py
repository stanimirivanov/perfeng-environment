"""CI-only retained evidence for independent, read-only schema qualification."""

import json
import sys
from pathlib import Path
from typing import Any

import runtime_grants_adopted as adopted
import yaml

SOURCES = (
    "release-schema-qualification-v1.yaml",
    "schema-qualification-job-v1.yaml",
    "release-schema-migration-v2.yaml",
    "release-schema-migration-v1.yaml",
    "release-preflight-v3.yaml",
    "release-task-selection-v3.yaml",
    "verify-release-quiescence-v3.yaml",
)
PREFIX = "qualification-"


def source_identity(root: Path, label: str) -> Any:
    def load(suffix: str) -> Any:
        return json.loads((root / f"{PREFIX}{label}.{suffix}.json").read_text())

    templates = load("templates")
    actual = {obj["metadata"]["name"]: obj for obj in templates["items"]}
    expected = [
        yaml.safe_load((adopted.grants.parent.DIRECTORY / name).read_text()) for name in SOURCES
    ]
    assert len(actual) == len(templates["items"]) == len(expected)
    assert set(actual) == {obj["metadata"]["name"] for obj in expected}
    for obj in expected:
        current = actual[obj["metadata"]["name"]]
        assert current["spec"] == obj["spec"]
        assert current["metadata"]["namespace"] == "argo-workflows"
    sql, intent = load("sql"), load("intent")
    for name, obj in (
        ("schema-qualification-sql-v1.yaml", sql),
        ("schema-migration-intent-v1.yaml", intent),
    ):
        expected_config = yaml.safe_load((adopted.grants.parent.DIRECTORY / name).read_text())
        adopted.grants.parent.subset(expected_config, obj)
        assert obj["data"] == expected_config["data"] and not obj.get("binaryData")
        assert not obj["metadata"].get("ownerReferences")
    return (
        adopted.grants.install.inventory({"items": [*templates["items"], sql, intent]}),
        adopted.grants.parent.ledger_authority(load("ledger"), "release-runtime-grants"),
    )


def stable(root: Path, label: str, *, final: bool = False) -> dict[str, Any]:
    observed = adopted.state(root, PREFIX + label)
    if not final:
        adopted.paused(root, label, prefix=PREFIX)
    else:
        baseline = adopted.state(root, PREFIX + "before")
        for field in ("records", "secrets", "config", "git"):
            assert observed[field] == baseline[field]
        assert observed["namespace"]["metadata"]["uid"] == baseline["namespace"]["metadata"]["uid"]
        assert not observed["namespace"]["metadata"].get("deletionTimestamp")
    for suffix in (*adopted.PERSISTENT, "schema.json", "roles.json", "data.txt"):
        assert (root / f"{PREFIX}{label}.{suffix}").read_bytes() == (
            root / f"schema-parent-baseline.{suffix}"
        ).read_bytes()
    # ACL is captured independently of the persistent snapshot's normalized ACL.
    if label != "before":
        assert (root / f"{PREFIX}{label}.acl.json").read_bytes() == (
            root / "qualification-before.acl.json"
        ).read_bytes()
    adopted.grants.parent.migration.validate_schema(
        json.loads((root / f"{PREFIX}{label}.schema.json").read_text()),
        json.loads(Path("tests/integration/release-workflows/migration-image.json").read_text()),
    )
    return observed


def proof(root: Path, label: str, *, retained: bool = False) -> dict[str, Any]:
    def load(suffix: str) -> Any:
        return json.loads((root / f"{PREFIX}{label}.{suffix}.json").read_text())

    before = stable(root, "before")
    parameters, candidate = adopted.request(root, before)
    after = stable(root, label)
    assert source_identity(root, label) == source_identity(root, "before")
    workflow = load("retained-workflow" if retained else "workflow")
    job = load("retained-job" if retained else "job")
    pods = load("retained-job-pods" if retained else "job-pods")
    report = adopted.grants.execution(
        workflow,
        load("retained-workers" if retained else "workers"),
        parameters,
        candidate,
        [*after["applications"]["items"], after["deployment"]],
        job,
        pods,
        retained_admission=True,
        qualification=True,
    )
    adopted.grants.parent.admission_nodes(
        workflow, (root / "upgrade-request-request.json").read_bytes(), after
    )
    assert (
        root / f"{PREFIX}{label}.worker.private"
    ).read_text().strip() == "Selected migration ledger and ownership qualified."
    if retained:
        adopted.grants.parent.retained_job(load("job"), job)
        inventory = adopted.grants.install.inventory
        assert inventory({"items": [load("workflow")]}) == inventory({"items": [workflow]})
        assert inventory(load("job-pods")) == inventory(pods)
    return report


def verify(root: Path, results: Path, mode: str) -> None:
    assert mode in {"before", "first", "repeat", "retained", "final"}
    before = stable(root, "before")
    adopted.request(root, before)
    identity = source_identity(root, "before")
    if mode == "before":
        assert json.loads((root / "qualification-before.jobs.json").read_text())["items"] == []
        return
    labels = ("first",) if mode == "first" else ("first", "repeat")
    reports = [proof(root, label, retained=mode in {"retained", "final"}) for label in labels]
    assert len({r["workflowUID"] for r in reports}) == len(reports)
    assert len({r["jobUID"] for r in reports}) == len(reports)
    inventory = adopted.grants.install.inventory
    jobs = [json.loads((root / f"qualification-{label}.job.json").read_text()) for label in labels]
    sample = mode if mode in {"retained", "final"} else labels[-1]
    assert inventory(
        json.loads((root / f"qualification-{sample}.jobs.json").read_text())
    ) == inventory({"items": jobs})
    if mode in {"retained", "final"}:
        stable(root, mode, final=mode == "final")
        assert source_identity(root, mode) == identity
        assert reports == json.loads((root / "qualification-repeat.verified.json").read_text())
    else:
        (root / f"qualification-{mode}.verified.json").write_text(json.dumps(reports) + "\n")
    # Publish bounded summaries only after all evidence has been revalidated.
    if mode in {"retained", "final"}:
        (results / f"schema-qualification-{mode}.outcome.json").write_text(
            json.dumps(reports) + "\n"
        )


def main() -> None:
    mode, directory, output = sys.argv[1:]
    verify(Path(directory), Path(output), mode)


if __name__ == "__main__":
    main()
