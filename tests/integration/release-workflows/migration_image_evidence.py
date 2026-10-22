"""Verify disposable image/schema observations without database or Kubernetes clients."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path


def validate_schema(snapshot: dict, contract: dict) -> None:
    assert snapshot["schemaOwner"] == "perfeng_migration"
    assert isinstance(snapshot["schemaOID"], str) and snapshot["schemaOID"].isdigit()
    relations = snapshot["relations"]
    assert relations and all(
        r["owner"] == "perfeng_migration" and r["oid"].isdigit() for r in relations
    )
    assert (
        len({r["oid"] for r in relations}) == len({r["name"] for r in relations}) == len(relations)
    )
    assert "schema_migrations" in {r["name"] for r in relations}
    expected = contract["migrations"]
    assert len(expected) == 18
    assert [int(name[:4]) for name in sorted(expected)] == list(range(1, 19))
    assert all(
        re.fullmatch(r"[0-9]{4}_[a-z0-9_]+\.sql", name) and re.fullmatch(r"[0-9a-f]{64}", digest)
        for name, digest in expected.items()
    )
    ledger = snapshot["ledger"]
    assert len(ledger) == len(expected)
    assert {row["version"]: row["sha256"] for row in ledger} == expected
    assert all(isinstance(row["appliedAt"], str) and row["appliedAt"] for row in ledger)


def check(directory: Path, contract: dict) -> None:
    def load(name):
        return json.loads((directory / name).read_text())

    selected = json.loads(Path("deployments/local-control-plane.json").read_text())["controlPlane"]
    assert (contract["image"], contract["sourceRevision"]) == (
        selected["image"],
        selected["sourceRevision"],
    )
    preflight = load("parent-first.result.json")
    workflow_uids, job_uids, checkpoint_uids = set(), set(), set()
    for label, key, code in (
        ("runtime-role", "runtime-url", 1),
        ("first", "migration-url", 0),
        ("repeat", "migration-url", 0),
    ):
        prefix = f"migration-image-{label}"
        workflow, job, checkpoint_workflow, checkpoint, workers = (
            load(prefix + suffix)
            for suffix in (
                ".workflow.json",
                ".job.json",
                ".checkpoint.workflow.json",
                ".checkpoint.json",
                ".worker.json",
            )
        )
        assert checkpoint_workflow["status"]["phase"] == "Succeeded"
        root = checkpoint_workflow["status"]["nodes"][checkpoint_workflow["metadata"]["name"]]
        materialized = [p["value"] for p in root["outputs"]["parameters"] if p["name"] == "result"]
        assert len(materialized) == 1 and json.loads(materialized[0]) == checkpoint
        assert (
            checkpoint["kind"] == "PerfengMaintenanceCheckpoint"
            and checkpoint["status"] == "quiescence-observed"
        )
        for field in ("requestRevision", "requestDigest", "selectionRevision", "clusterUID"):
            assert checkpoint[field] == preflight[field]
        checkpoint_time = datetime.fromisoformat(checkpoint_workflow["status"]["finishedAt"])
        workflow_time = datetime.fromisoformat(workflow["metadata"]["creationTimestamp"])
        job_time = datetime.fromisoformat(job["metadata"]["creationTimestamp"])
        assert all(t.utcoffset() is not None for t in (checkpoint_time, workflow_time, job_time))
        assert checkpoint_time <= workflow_time <= job_time
        pod = job["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["imagePullSecrets"] == [{"name": "migration-ci-registry"}]
        assert len(pod["containers"]) == 1
        container = pod["containers"][0]
        assert container["image"] == contract["image"] and container["command"] == [
            "/usr/local/bin/migrate"
        ]
        environment = {v["name"]: v for v in container["env"]}
        assert environment["PERFENG_DATABASE_URL"]["valueFrom"]["secretKeyRef"] == {
            "name": "perfeng-control-plane-database",
            "key": key,
        }
        assert environment["PGAPPNAME"]["value"] == f"migration-ci-{label}"
        assert len(workers) == 1 and workers[0]["uid"]
        assert workers[0]["containers"] == [
            {"name": "migrate", "exitCode": code, "waitingReason": None}
        ]
        if code == 0:
            assert workflow["status"]["phase"] == "Succeeded" and job["status"]["succeeded"] == 1
        else:
            assert (
                workflow["status"]["phase"] in {"Failed", "Error"} and job["status"]["failed"] == 1
            )
        workflow_uids.add(workflow["metadata"]["uid"])
        checkpoint_uids.add(checkpoint_workflow["metadata"]["uid"])
        job_uids.add(job["metadata"]["uid"])
    assert len(workflow_uids) == len(checkpoint_uids) == len(job_uids) == 3
    first, repeat = (
        load("migration-image-first.schema.json"),
        load("migration-image-repeat.schema.json"),
    )
    validate_schema(first, contract)
    validate_schema(repeat, contract)
    assert first == repeat


if __name__ == "__main__":
    try:
        check(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()))
    except (AssertionError, ValueError, KeyError, TypeError) as error:
        print(f"Migration image evidence refused ({type(error).__name__})", file=sys.stderr)
        sys.exit(1)
