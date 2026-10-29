"""Typed SQL-lock evidence for failure, owned release and retained native execution."""

import ipaddress
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import schema_migration_install as install

LOCK = 70657266656


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    assert result.utcoffset() is not None
    return result


def empty(sample: dict[str, Any]) -> None:
    assert (
        sample["schemaAbsent"] is True
        and type(sample["applicationRelations"]) is int
        and sample["applicationRelations"] == 0
    )
    assert isinstance(sample["databaseOID"], str) and sample["databaseOID"].isdigit()
    timestamp(sample["observedAt"])
    assert isinstance(sample["sessions"], list) and isinstance(sample["locks"], list)
    for lock in sample["locks"]:
        assert lock["databaseOID"] == sample["databaseOID"]
        assert (int(lock["classid"]) << 32) | int(lock["objid"]) == LOCK
        assert lock["objsubid"] == 1 and lock["mode"] == "ExclusiveLock"
        assert type(lock["pid"]) is int and lock["pid"] > 0 and type(lock["granted"]) is bool


def identity(session: dict[str, Any]) -> tuple[int, str, str, str, str]:
    assert type(session["pid"]) is int and session["pid"] > 0
    timestamp(session["backendStart"])
    assert session["backendType"] == "client backend" and session["database"] == "perfeng"
    return tuple(session[k] for k in ("pid", "backendStart", "user", "database", "application"))


def holder(sample: dict[str, Any], cluster_uid: str) -> dict[str, Any]:
    empty(sample)
    matches = [
        s
        for s in sample["sessions"]
        if s["application"] == "perfeng-schema-contention-" + cluster_uid
    ]
    assert len(matches) == 1
    session = matches[0]
    identity(session)
    assert session["user"] == "postgres" and session["state"] in {"idle", "active"}
    assert timestamp(session["backendStart"]) <= timestamp(sample["observedAt"])
    assert session["query"].strip() == f"SELECT pg_advisory_lock({LOCK});"
    assert session["blockers"] == []
    owned = [o for o in sample["locks"] if o["pid"] == session["pid"]]
    assert len(owned) == 1 and owned[0]["granted"] is True
    return session


def proof(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    def load(name: str) -> Any:
        return json.loads((root / name).read_text())

    parent = install.parent
    cluster_uid = load("schema-install.before.namespace.json")["metadata"]["uid"]
    original = load("schema-contention.held.json")
    owner = holder(original, cluster_uid)
    assert len(original["sessions"]) == len(original["locks"]) == 1
    parameters, request = install.request_input(root)
    before = load("schema-install.before.objects.json")
    resources = install.baseline(
        before,
        load("schema-install.before.applications.json"),
        load("schema-install.before.pods.json"),
    )
    for suffix in ("objects", "applications", "pods", "pvs"):
        assert install.inventory(load(f"schema-install.before.{suffix}.json")) == install.inventory(
            load(f"schema-install.contention-after.{suffix}.json")
        )
    install.baseline(
        load("schema-install.contention-after.objects.json"),
        load("schema-install.contention-after.applications.json"),
        load("schema-install.contention-after.pods.json"),
    )
    assert load("schema-install.before.roles.json") == load(
        "schema-install.contention-after.roles.json"
    )
    assert cluster_uid == load("schema-install.contention-after.namespace.json")["metadata"]["uid"]
    assert parent.installed(
        load("schema-install.templates-before.json"), load("schema-install.intent-before.json")
    ) == parent.installed(
        load("schema-install.templates-contention.json"),
        load("schema-install.intent-contention.json"),
    )
    workflow, job, pods = (
        load("schema-contention.workflow.json"),
        load("schema-contention.job.json"),
        load("schema-contention.job-pods.json"),
    )
    parent.execution(
        "contention",
        workflow,
        load("schema-contention.workers.json"),
        parameters,
        request,
        cluster_uid,
        resources,
        job,
        pods,
    )
    assert load("schema-install.jobs-before.json")["items"] == []
    assert [j["metadata"]["uid"] for j in load("schema-contention.jobs-after.json")["items"]] == [
        job["metadata"]["uid"]
    ]
    log = (root / "schema-contention.migrator.private").read_text(errors="replace")
    assert "Connecting to PostgreSQL for control-plane migrations..." in log
    assert "Checking migration versions and applying pending migrations..." in log
    assert (
        "Migration failed; the transaction was rolled back or its commit outcome is uncertain. Check database availability, privileges and migration ledger before retrying."
        in log
    )
    assert "Control-plane schema is up to date." not in log
    end = timestamp(workflow["status"]["finishedAt"])
    pod = pods["items"][0]
    terminated = pod["status"]["containerStatuses"][0]["state"]["terminated"]
    assert terminated["exitCode"] == 1 and terminated["reason"] == "Error"
    pod_end = timestamp(terminated["finishedAt"])
    with (root / "schema-contention.watch.ndjson").open(encoding="utf-8") as source:
        stream = source.read(4194305)
    assert len(stream) <= 4194304
    waits = []
    for line in stream.splitlines():
        sample = json.loads(line)
        assert identity(holder(sample, cluster_uid)) == identity(owner)
        assert sample["databaseOID"] == original["databaseOID"]
        assert timestamp(sample["observedAt"]) >= timestamp(original["observedAt"])
        for session in sample["sessions"]:
            if session["pid"] == owner["pid"] or session.get("waitEvent") != "advisory":
                continue
            identity(session)
            assert (
                session["user"] == "perfeng_migration"
                and session["application"] == "perfeng-schema-migration-v1"
            )
            assert session["state"] == "active" and session["waitType"] == "Lock"
            assert session["query"].strip() == f"SELECT pg_advisory_xact_lock({LOCK})"
            assert session["blockers"] == [owner["pid"]]
            assert ipaddress.ip_address(session["clientAddress"]) == ipaddress.ip_address(
                pod["status"]["podIP"]
            )
            assert (
                timestamp(job["metadata"]["creationTimestamp"])
                <= timestamp(session["backendStart"])
                <= timestamp(sample["observedAt"])
            )
            assert pod_end <= end
            assert len(sample["sessions"]) == len(sample["locks"]) == 2
            assert any(
                lock["pid"] == session["pid"] and lock["granted"] is False
                for lock in sample["locks"]
            )
            if timestamp(sample["observedAt"]) <= pod_end:
                waits.append(session)
    assert waits and all(identity(w) == identity(waits[0]) for w in waits)
    current = load("schema-contention.before-release.json")
    assert identity(holder(current, cluster_uid)) == identity(owner)
    assert holder(current, cluster_uid)["state"] == "idle"
    assert (
        current["databaseOID"] == original["databaseOID"]
        and timestamp(current["observedAt"]) >= end
    )
    allowed = {identity(owner), identity(waits[0])}
    assert all(identity(s) in allowed for s in current["sessions"])
    assert all(lock["pid"] in {owner["pid"], waits[0]["pid"]} for lock in current["locks"])
    assert all(lock["granted"] is (lock["pid"] == owner["pid"]) for lock in current["locks"])
    return owner, waits[0], workflow


def released(root: Path, owner: dict[str, Any], waiter: dict[str, Any]) -> bool:
    sample = json.loads((root / "schema-contention.released.json").read_text())
    empty(sample)
    before = json.loads((root / "schema-contention.before-release.json").read_text())
    assert sample["databaseOID"] == before["databaseOID"]
    assert timestamp(sample["observedAt"]) >= timestamp(before["observedAt"])
    assert all(identity(s) in {identity(owner), identity(waiter)} for s in sample["sessions"])
    assert all(lock["pid"] in {owner["pid"], waiter["pid"]} for lock in sample["locks"])
    return not sample["sessions"] and not sample["locks"]


def main() -> None:
    mode, directory, output = sys.argv[1:]
    root, results = Path(directory), Path(output)
    if mode == "held":
        value = json.loads((root / "schema-contention.candidate.json").read_text())
        empty(value)
        if not value["locks"]:
            # Catalog reads may catch the owned connection before its lock query runs.
            if not value["sessions"]:
                sys.exit(3)
            cluster_uid = json.loads((root / "schema-install.before.namespace.json").read_text())[
                "metadata"
            ]["uid"]
            assert len(value["sessions"]) == 1
            session = value["sessions"][0]
            identity(session)
            assert (
                session["user"] == "postgres"
                and session["application"] == "perfeng-schema-contention-" + cluster_uid
            )
            sys.exit(3)
        cluster_uid = json.loads((root / "schema-install.before.namespace.json").read_text())[
            "metadata"
        ]["uid"]
        holder(value, cluster_uid)
        assert len(value["sessions"]) == len(value["locks"]) == 1
        (root / "schema-contention.held.json").write_text(json.dumps(value) + "\n")
        return
    assert mode in {"release", "released", "retained"}
    owner, waiter, workflow = proof(root)
    report = {"decision": "schema-contention-refused", "workflowUID": workflow["metadata"]["uid"]}
    if mode == "release":
        (results / "schema-contention.outcome.json").write_text(json.dumps(report) + "\n")
        return
    if not released(root, owner, waiter):
        sys.exit(3)
    report["decision"] = "schema-contention-released"
    if mode == "retained":
        job = json.loads((root / "schema-contention.job.json").read_text())
        current_job = json.loads((root / "schema-contention.retained-job.json").read_text())
        install.parent.retained_job(job, current_job, failed=True)
        current = json.loads((root / "schema-contention.retained-workflow.json").read_text())
        assert (
            current["metadata"]["uid"] == workflow["metadata"]["uid"]
            and current["spec"] == workflow["spec"]
        )
        assert not current["metadata"].get("deletionTimestamp")
        assert current["status"]["phase"] == workflow["status"]["phase"] in {"Failed", "Error"}
        name = workflow["metadata"]["name"]
        assert current["status"]["nodes"][name].get("outputs", {}) == workflow["status"]["nodes"][
            name
        ].get("outputs", {})
        report["decision"] = "schema-contention-retained"
    (results / f"schema-contention-{mode}.outcome.json").write_text(json.dumps(report) + "\n")


if __name__ == "__main__":
    main()
