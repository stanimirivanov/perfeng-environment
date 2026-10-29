"""Validate native DDL rollback before generating fixture-only guarded cleanup."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import schema_migration_install as install

CATALOG = Path("tests/integration/release-workflows/schema-rollback-catalog.sql")


def time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    assert result.utcoffset() is not None
    return result


def empty(value: dict[str, Any]) -> None:
    assert value["schema"] is None and value["relations"] == value["columns"] == []
    assert type(value["migrationSessions"]) is int and value["migrationSessions"] == 0
    assert type(value["migrationLocks"]) is int and value["migrationLocks"] == 0
    assert value["databaseOID"].isdigit()
    time(value["observedAt"])


def marker(root: Path) -> str:
    uid = json.loads((root / "schema-install.before.namespace.json").read_text())["metadata"]["uid"]
    assert re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", uid)
    return "perfeng-ci-schema-rollback-v1:" + uid


def owned(value: dict[str, Any], expected_marker: str) -> None:
    time(value["observedAt"])
    assert value["databaseOID"].isdigit() and value["schema"]["oid"].isdigit()
    assert (
        value["schema"]["owner"] == "perfeng_migration"
        and value["schema"]["comment"] == expected_marker
    )
    assert len(value["relations"]) == 1
    table = value["relations"][0]
    assert table["oid"].isdigit()
    assert table == {
        "oid": table["oid"],
        "name": "artifacts",
        "kind": "r",
        "owner": "perfeng_migration",
        "comment": expected_marker,
        "toastOID": "0",
    }
    assert value["columns"] == [
        {
            "name": "probe",
            "type": "integer",
            "notNull": True,
            "default": None,
            "identity": "",
            "generated": "",
        }
    ]
    assert type(value["migrationSessions"]) is int and value["migrationSessions"] == 0
    assert type(value["migrationLocks"]) is int and value["migrationLocks"] == 0


def stable(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k != "observedAt"}


def cleanup_sql(value: dict[str, Any]) -> str:
    query = CATALOG.read_text().strip().removesuffix(";")
    expected = json.dumps(stable(value), sort_keys=True).replace("'", "''")
    return f"""BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '10s';
LOCK TABLE perfeng_control.artifacts IN ACCESS EXCLUSIVE MODE;
DO $guard$
DECLARE actual jsonb;
BEGIN
 SELECT snapshot - 'observedAt' INTO actual FROM (
{query}
 ) AS current_state;
 IF actual IS DISTINCT FROM '{expected}'::jsonb THEN
   RAISE EXCEPTION 'CI rollback fixture identity/ownership/inventory changed';
 END IF;
 IF (SELECT jsonb_agg(to_jsonb(t) ORDER BY probe) FROM perfeng_control.artifacts t)
      IS DISTINCT FROM '[{{"probe":1}}]'::jsonb THEN
   RAISE EXCEPTION 'CI rollback fixture sentinel changed';
 END IF;
END;
$guard$;
DROP TABLE perfeng_control.artifacts RESTRICT;
DROP SCHEMA perfeng_control RESTRICT;
COMMIT;
"""


def retained_state(root: Path, label: str) -> None:
    def load(name: str) -> Any:
        return json.loads((root / name).read_text())

    for suffix in ("objects", "applications", "pods", "pvs"):
        assert install.inventory(load(f"schema-install.before.{suffix}.json")) == install.inventory(
            load(f"schema-install.{label}.{suffix}.json")
        )
    install.baseline(
        load(f"schema-install.{label}.objects.json"),
        load(f"schema-install.{label}.applications.json"),
        load(f"schema-install.{label}.pods.json"),
    )
    assert load("schema-install.before.roles.json") == load(f"schema-install.{label}.roles.json")
    assert (
        load("schema-install.before.namespace.json")["metadata"]["uid"]
        == load(f"schema-install.{label}.namespace.json")["metadata"]["uid"]
    )


def proof(root: Path) -> dict[str, Any]:
    def load(name: str) -> Any:
        return json.loads((root / name).read_text())

    before, fixture, after = (
        load(f"schema-rollback.{label}.catalog.json") for label in ("empty", "fixture", "after")
    )
    empty(before)
    for value in (fixture, after):
        owned(value, marker(root))
        assert value["databaseOID"] == before["databaseOID"]
    assert stable(fixture) == stable(after)
    assert (
        load("schema-rollback.fixture.rows.json")
        == load("schema-rollback.after.rows.json")
        == [{"probe": 1}]
    )
    assert time(before["observedAt"]) <= time(fixture["observedAt"]) <= time(after["observedAt"])
    retained_state(root, "rollback-after")
    parent = install.parent
    assert parent.installed(
        load("schema-install.templates-before.json"), load("schema-install.intent-before.json")
    ) == parent.installed(
        load("schema-install.templates-rollback.json"), load("schema-install.intent-rollback.json")
    )
    parameters, request = install.request_input(root)
    workflow, job = load("schema-rollback.workflow.json"), load("schema-rollback.job.json")
    resources = install.baseline(
        load("schema-install.before.objects.json"),
        load("schema-install.before.applications.json"),
        load("schema-install.before.pods.json"),
    )
    parent.execution(
        "rollback",
        workflow,
        load("schema-rollback.workers.json"),
        parameters,
        request,
        request["environment"]["clusterUID"],
        resources,
        job,
        load("schema-rollback.job-pods.json"),
    )
    assert time(after["observedAt"]) >= time(workflow["status"]["finishedAt"])
    assert time(fixture["observedAt"]) <= time(workflow["metadata"]["creationTimestamp"])
    earlier = load("schema-contention.job.json")
    jobs = load("schema-rollback.jobs-after.json")["items"]
    assert len(jobs) == 2 and {j["metadata"]["uid"] for j in jobs} == {
        earlier["metadata"]["uid"],
        job["metadata"]["uid"],
    }
    parent.retained_job(
        earlier,
        next(j for j in jobs if j["metadata"]["uid"] == earlier["metadata"]["uid"]),
        failed=True,
    )
    native_log = (root / "schema-rollback.migrator.private").read_text(errors="replace")
    assert "Connecting to PostgreSQL for control-plane migrations..." in native_log
    assert "Checking migration versions and applying pending migrations..." in native_log
    assert (
        "Migration failed; the transaction was rolled back or its commit outcome is uncertain."
        in native_log
    )
    assert "Control-plane schema is up to date." not in native_log
    log = (root / "schema-rollback.postgres.private").read_text(errors="replace")
    matches = re.findall(
        r'^([^\s]+) user=perfeng_migration database=perfeng app=perfeng-schema-migration-v1\s+ERROR:\s+relation "artifacts" already exists\s*$',
        log,
        re.MULTILINE,
    )
    assert matches and all(
        time(job["metadata"]["creationTimestamp"])
        <= time(t)
        <= time(workflow["status"]["finishedAt"])
        for t in matches
    )
    assert all(
        "CREATE TABLE perfeng_control." + name in log
        for name in ("runs", "create_bindings", "artifacts")
    )
    return workflow


def main() -> None:
    mode, directory, output = sys.argv[1:]
    root, results = Path(directory), Path(output)
    if mode == "prepare":
        empty(json.loads((root / "schema-rollback.empty.catalog.json").read_text()))
        value = marker(root)
        sql = f"""BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '10s';
DO $guard$ BEGIN
 IF to_regnamespace('perfeng_control') IS NOT NULL THEN RAISE EXCEPTION 'CI schema already exists'; END IF;
END; $guard$;
SET LOCAL ROLE perfeng_migration;
CREATE SCHEMA perfeng_control AUTHORIZATION perfeng_migration;
CREATE TABLE perfeng_control.artifacts(probe integer NOT NULL);
INSERT INTO perfeng_control.artifacts VALUES (1);
COMMENT ON SCHEMA perfeng_control IS '{value}';
COMMENT ON TABLE perfeng_control.artifacts IS '{value}';
COMMIT;
"""
        (root / "schema-rollback.create.sql").write_text(sql)
        return
    if mode == "fixture":
        owned(json.loads((root / "schema-rollback.fixture.catalog.json").read_text()), marker(root))
        assert json.loads((root / "schema-rollback.fixture.rows.json").read_text()) == [
            {"probe": 1}
        ]
        return
    assert mode in {"cleanup", "cleaned", "retained"}
    workflow = proof(root)
    if mode == "cleanup":
        value = json.loads((root / "schema-rollback.after.catalog.json").read_text())
        (root / "schema-rollback.cleanup.sql").write_text(cleanup_sql(value))
        return
    cleaned = json.loads((root / "schema-rollback.cleaned.catalog.json").read_text())
    empty(cleaned)
    before = json.loads((root / "schema-rollback.after.catalog.json").read_text())
    assert cleaned["databaseOID"] == before["databaseOID"] and time(cleaned["observedAt"]) >= time(
        before["observedAt"]
    )
    retained_state(root, "rollback-cleaned")
    if mode == "retained":
        job, current = (
            json.loads((root / f"schema-rollback.{suffix}.json").read_text())
            for suffix in ("job", "retained-job")
        )
        install.parent.retained_job(job, current, failed=True)
        current = json.loads((root / "schema-rollback.retained-workflow.json").read_text())
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
    (results / f"schema-rollback-{mode}.outcome.json").write_text(
        json.dumps(
            {"decision": "schema-rollback-" + mode, "workflowUID": workflow["metadata"]["uid"]}
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
