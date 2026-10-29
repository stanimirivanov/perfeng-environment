"""Prove attempted schema GRANT rollback before fixture-only guarded deletion."""

import json
import re
import sys
from pathlib import Path
from typing import Any

import runtime_grants_parent as grants
from schema_migration_contention import timestamp

NAME = "perfeng_ci_grant_rollback"
ERROR = "PERFENG_CI_GRANT_ROLLBACK_AFTER_SCHEMA_USAGE"
STATEMENT = "GRANT USAGE ON SCHEMA perfeng_control TO perfeng_runtime;"
CATALOG = Path("tests/integration/release-workflows/grant-rollback-catalog.sql")
BODY = f"""
BEGIN
  IF TG_EVENT = 'ddl_command_end' AND TG_TAG = 'GRANT'
     AND current_user = 'perfeng_migration' AND session_user = 'perfeng_migration'
     AND current_setting('application_name') = 'perfeng-runtime-grants-v1'
     AND btrim(current_query()) = '{STATEMENT}' THEN
    IF NOT has_schema_privilege('perfeng_runtime','perfeng_control','USAGE') THEN
      RAISE EXCEPTION 'CI grant rollback fixture did not observe schema USAGE';
    END IF;
    RAISE EXCEPTION '{ERROR}';
  END IF;
  RETURN;
END;
"""


def marker(root: Path) -> str:
    _, request = grants.install.request_input(root)
    uid = request["environment"]["clusterUID"]
    assert re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", uid)
    return "perfeng-ci-grant-rollback-v1:" + uid


def stable(value: dict[str, Any], *, usage: bool = False) -> dict[str, Any]:
    timestamp(value["observedAt"])
    assert value["databaseOID"].isdigit() and value["schemaOID"].isdigit()
    assert value["schemaUsage"] is usage
    assert type(value["grantSessions"]) is int and value["grantSessions"] == 0
    assert type(value["grantLocks"]) is int and value["grantLocks"] == 0
    return {k: v for k, v in value.items() if k != "observedAt"}


def owned(value: dict[str, Any], expected_marker: str) -> None:
    stable(value)
    assert len(value["functions"]) == len(value["triggers"]) == 1
    function, trigger = value["functions"][0], value["triggers"][0]
    assert function["owner"] == trigger["owner"] == "postgres"
    assert function["comment"] == trigger["comment"] == expected_marker
    assert function["proname"] == trigger["evtname"] == NAME
    assert function["schema"] == "public" and function["language"] == "plpgsql"
    assert function["returnType"] == "event_trigger" and function["pronargs"] == 0
    assert function["prosrc"] == BODY and function["proconfig"] == ["search_path=pg_catalog"]
    assert function["prosecdef"] is False and function["proleakproof"] is False
    assert function["provolatile"] == "v" and function["proparallel"] == "u"
    assert function["prokind"] == "f" and function["proacl"] is None
    assert function["oid"].isdigit() and trigger["oid"].isdigit()
    assert trigger["evtfoid"] == function["oid"]
    assert trigger["evtevent"] == "ddl_command_end" and trigger["evtenabled"] == "O"
    assert trigger["evttags"] == ["GRANT"]


def create_sql(expected_marker: str) -> str:
    return f"""BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='10s';
CREATE FUNCTION public.{NAME}() RETURNS event_trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $fixture${BODY}$fixture$;
COMMENT ON FUNCTION public.{NAME}() IS '{expected_marker}';
CREATE EVENT TRIGGER {NAME} ON ddl_command_end WHEN TAG IN ('GRANT')
EXECUTE FUNCTION public.{NAME}();
COMMENT ON EVENT TRIGGER {NAME} IS '{expected_marker}';
COMMIT;
"""


def cleanup_sql(value: dict[str, Any]) -> str:
    query = CATALOG.read_text().strip().removesuffix(";")
    expected = json.dumps(stable(value), sort_keys=True).replace("'", "''")
    # The private CI fixture has one DDL writer; the advisory key serializes grant Jobs.
    return f"""BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='10s';
SELECT pg_advisory_xact_lock(70657266656);
DO $guard$
DECLARE actual jsonb;
BEGIN
 SELECT snapshot - 'observedAt' INTO actual FROM (
{query}
 ) AS current_state;
 IF actual IS DISTINCT FROM '{expected}'::jsonb THEN
   RAISE EXCEPTION 'CI grant rollback fixture identity/ownership/definition changed';
 END IF;
END;
$guard$;
DROP EVENT TRIGGER {NAME} RESTRICT;
DROP FUNCTION public.{NAME}() RESTRICT;
COMMIT;
"""


def proof(root: Path) -> dict[str, Any]:
    def load(name: str) -> Any:
        return json.loads((root / name).read_text())

    before, after = (
        load(f"grant-rollback.catalog-{label}.json") for label in ("installed", "failed")
    )
    owned(before, marker(root))
    owned(after, marker(root))
    assert stable(before) == stable(after)
    report = grants.verify_case(root, "rollback")
    assert report == load("grant-rollback.verified.json")
    workflow, pods = load("grant-rollback.workflow.json"), load("grant-rollback.pods.json")
    end = pods["items"][0]["status"]["containerStatuses"][0]["state"]["terminated"]
    start, finish = timestamp(end["startedAt"]), timestamp(end["finishedAt"])
    node = grants.parent.maintenance.readiness.named(workflow, "grant")
    assert timestamp(before["observedAt"]) <= start <= finish <= timestamp(node["finishedAt"])
    assert timestamp(after["observedAt"]) >= timestamp(node["finishedAt"])
    worker = (root / "grant-rollback.worker.private").read_text(errors="replace")
    assert "[RUNTIME-GRANTS] Grant transaction failed;" in worker
    assert "Reviewed runtime database privileges established." not in worker
    server = (root / "grant-rollback.postgres.private").read_text(errors="replace")
    prefix = r"user=perfeng_migration database=perfeng app=perfeng-runtime-grants-v1 "
    errors = re.findall(r"(?m)^(\S+) " + prefix + "ERROR:  " + ERROR + "$", server)
    statements = re.findall(
        r"(?m)^(\S+) " + prefix + "STATEMENT:  " + re.escape(STATEMENT) + "$", server
    )
    assert len(errors) == len(statements) == 1
    assert start <= timestamp(errors[0]) <= timestamp(statements[0]) <= finish
    return after


def main() -> None:
    mode, directory, _output = sys.argv[1:]
    root = Path(directory)

    def load(label: str) -> Any:
        return json.loads((root / f"grant-rollback.catalog-{label}.json").read_text())

    assert mode in {"prepare", "installed", "cleanup", "cleaned", "retained"}
    baseline = load("before")
    stable(baseline)
    assert (
        baseline["schemaOID"]
        == json.loads((root / "grant-rollback-before.schema.json").read_text())["schemaOID"]
    )
    assert baseline["functions"] == baseline["triggers"] == []
    if mode == "prepare":
        (root / "grant-rollback.create.sql").write_text(create_sql(marker(root)))
        return
    installed = load("installed")
    owned(installed, marker(root))
    assert {k: v for k, v in stable(installed).items() if k not in {"functions", "triggers"}} == {
        k: v for k, v in stable(baseline).items() if k not in {"functions", "triggers"}
    }
    if mode == "installed":
        return
    after = proof(root)
    if mode == "cleanup":
        current = load("current")
        owned(current, marker(root))
        assert stable(current) == stable(after)
        (root / "grant-rollback.cleanup.sql").write_text(cleanup_sql(current))
        return
    assert stable(load("cleaned")) == stable(baseline)
    assert timestamp(load("cleaned")["observedAt"]) >= timestamp(after["observedAt"])
    if mode == "retained":
        current = load("retained")
        assert stable(current, usage=True) == {**stable(baseline), "schemaUsage": True}
        assert timestamp(current["observedAt"]) >= timestamp(load("cleaned")["observedAt"])


if __name__ == "__main__":
    main()
