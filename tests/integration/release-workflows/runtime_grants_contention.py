"""Causal SQL-lock evidence for the fixed grant Job and normal holder disconnect."""

import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any

import runtime_grants_parent as grants
from schema_migration_contention import LOCK, identity, timestamp


def catalog(sample: dict[str, Any]) -> None:
    assert isinstance(sample["databaseOID"], str) and sample["databaseOID"].isdigit()
    timestamp(sample["observedAt"])
    assert isinstance(sample["sessions"], list) and isinstance(sample["locks"], list)
    for lock in sample["locks"]:
        assert lock["databaseOID"] == sample["databaseOID"]
        assert (int(lock["classid"]) << 32) | int(lock["objid"]) == LOCK
        assert lock["objsubid"] == 1 and lock["mode"] == "ExclusiveLock"
        assert type(lock["pid"]) is int and lock["pid"] > 0 and type(lock["granted"]) is bool


def holder(sample: dict[str, Any], cluster_uid: str) -> dict[str, Any]:
    catalog(sample)
    matches = [
        s
        for s in sample["sessions"]
        if s["application"] == "perfeng-grant-contention-" + cluster_uid
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


def proof(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    def load(name: str) -> Any:
        return json.loads((root / name).read_text())

    _, request = grants.install.request_input(root)
    cluster = request["environment"]["clusterUID"]
    held = load("grant-contention.held.json")
    owner = holder(held, cluster)
    assert len(held["sessions"]) == len(held["locks"]) == 1
    workflow, job, pods = (
        load(f"grant-contention.{suffix}.json") for suffix in ("workflow", "job", "pods")
    )
    report = grants.verify_case(root, "contention")
    assert load("grant-contention.verified.json") == report
    pod = pods["items"][0]
    terminated = pod["status"]["containerStatuses"][0]["state"]["terminated"]
    start, finish = timestamp(terminated["startedAt"]), timestamp(terminated["finishedAt"])
    node = grants.parent.maintenance.readiness.named(workflow, "grant")
    assert (
        timestamp(job["metadata"]["creationTimestamp"])
        <= start
        <= finish
        <= timestamp(node["finishedAt"])
    )
    log = (root / "grant-contention.worker.private").read_text(errors="replace")
    assert "[RUNTIME-GRANTS] Grant transaction failed;" in log
    assert "Reviewed runtime database privileges established." not in log
    with (root / "grant-contention.watch.ndjson").open(encoding="utf-8") as source:
        stream = source.read(4194305)
    assert len(stream) <= 4194304
    waits = []
    wait_times = []
    clients = []
    for line in stream.splitlines():
        sample = json.loads(line)
        assert identity(holder(sample, cluster)) == identity(owner)
        assert sample["databaseOID"] == held["databaseOID"]
        assert timestamp(sample["observedAt"]) >= timestamp(held["observedAt"])
        for session in sample["sessions"]:
            if session["pid"] == owner["pid"]:
                continue
            identity(session)
            assert (
                session["user"] == "perfeng_migration"
                and session["application"] == "perfeng-runtime-grants-v1"
            )
            assert timestamp(job["metadata"]["creationTimestamp"]) <= timestamp(
                session["backendStart"]
            )
            assert start <= timestamp(session["backendStart"]) <= finish
            assert ipaddress.ip_address(session["clientAddress"]) == ipaddress.ip_address(
                pod["status"]["podIP"]
            )
            clients.append(session)
            # Connecting, idle-in-transaction or aborted samples are not lock proof.
            if session["waitEvent"] != "advisory":
                continue
            assert session["waitType"] == "Lock" and session["state"] == "active"
            assert timestamp(session["backendStart"]) <= timestamp(sample["observedAt"])
            assert session["query"].strip() == f"SELECT pg_advisory_xact_lock({LOCK});"
            assert session["blockers"] == [owner["pid"]]
            assert len(sample["sessions"]) == len(sample["locks"]) == 2
            assert any(
                o["pid"] == session["pid"] and o["granted"] is False for o in sample["locks"]
            )
            if start <= timestamp(sample["observedAt"]) <= finish:
                waits.append(session)
                wait_times.append(timestamp(sample["observedAt"]))
    assert waits and all(identity(s) == identity(waits[0]) for s in waits)
    assert all(identity(s) == identity(waits[0]) for s in clients)
    # Require the server's exact lock-timeout and matching statement in this Pod's lifetime.
    prefix = r"user=perfeng_migration database=perfeng app=perfeng-runtime-grants-v1 "
    server = (root / "grant-contention.postgres.private").read_text(errors="replace")
    errors = re.findall(
        r"(?m)^(\S+) " + prefix + r"ERROR:  canceling statement due to lock timeout$", server
    )
    statements = re.findall(
        r"(?m)^(\S+) " + prefix + rf"STATEMENT:  SELECT pg_advisory_xact_lock\({LOCK}\);$", server
    )
    assert len(errors) == len(statements) == 1
    assert start <= timestamp(errors[0]) <= timestamp(statements[0]) <= finish
    assert min(wait_times) <= timestamp(errors[0])
    current = load("grant-contention.before-release.json")
    assert identity(holder(current, cluster)) == identity(owner)
    assert holder(current, cluster)["state"] == "idle"
    assert current["databaseOID"] == held["databaseOID"]
    assert timestamp(current["observedAt"]) >= timestamp(node["finishedAt"])
    assert all(identity(s) in {identity(owner), identity(waits[0])} for s in current["sessions"])
    assert all(
        o["pid"] in {owner["pid"], waits[0]["pid"]} and o["granted"] is (o["pid"] == owner["pid"])
        for o in current["locks"]
    )
    return owner, waits[0]


def main() -> None:
    mode, directory, _output = sys.argv[1:]
    root = Path(directory)
    _, request = grants.install.request_input(root)
    cluster = request["environment"]["clusterUID"]
    if mode == "held":
        sample = json.loads((root / "grant-contention.candidate.json").read_text())
        catalog(sample)
        if not sample["locks"]:
            assert len(sample["sessions"]) <= 1
            for session in sample["sessions"]:
                identity(session)
                assert (
                    session["user"] == "postgres"
                    and session["application"] == "perfeng-grant-contention-" + cluster
                )
            sys.exit(3)
        holder(sample, cluster)
        assert len(sample["sessions"]) == len(sample["locks"]) == 1
        (root / "grant-contention.held.json").write_text(json.dumps(sample) + "\n")
        return
    assert mode in {"release", "released", "retained"}
    owner, waiter = proof(root)
    if mode == "release":
        return
    sample = json.loads((root / "grant-contention.released.json").read_text())
    catalog(sample)
    before = json.loads((root / "grant-contention.before-release.json").read_text())
    assert sample["databaseOID"] == before["databaseOID"]
    assert timestamp(sample["observedAt"]) >= timestamp(before["observedAt"])
    assert all(identity(s) in {identity(owner), identity(waiter)} for s in sample["sessions"])
    assert all(o["pid"] in {owner["pid"], waiter["pid"]} for o in sample["locks"])
    if sample["sessions"] or sample["locks"]:
        sys.exit(3)


if __name__ == "__main__":
    main()
