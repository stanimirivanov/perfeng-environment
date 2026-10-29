"""Prove attempted-GRANT rollback and fixture-only cleanup gates offline."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import test_runtime_grants_parent as grants
import test_schema_migration_contention as schema
from test_release_workflows_integration import BASH

SPEC = importlib.util.spec_from_file_location(
    "runtime_grants_rollback", grants.FIXTURE / "runtime_grants_rollback.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(
    "sys.modules",
    {"runtime_grants_parent": grants.MODULE, "schema_migration_contention": schema.MODULE},
):
    SPEC.loader.exec_module(MODULE)


def populate(root, results):
    grants.populate(root, results)
    tag = MODULE.marker(root)
    baseline = {
        "observedAt": "2026-10-27T23:59:50+00:00",
        "databaseOID": "1234",
        "schemaOID": json.loads((root / "grant-rollback-before.schema.json").read_text())[
            "schemaOID"
        ],
        "schemaUsage": False,
        "functions": [],
        "triggers": [],
        "grantSessions": 0,
        "grantLocks": 0,
    }
    function = {
        "oid": "1001",
        "owner": "postgres",
        "comment": tag,
        "proname": MODULE.NAME,
        "schema": "public",
        "language": "plpgsql",
        "returnType": "event_trigger",
        "pronargs": 0,
        "prosrc": MODULE.BODY,
        "proconfig": ["search_path=pg_catalog"],
        "prosecdef": False,
        "proleakproof": False,
        "provolatile": "v",
        "proparallel": "u",
        "prokind": "f",
        "proacl": None,
    }
    trigger = {
        "oid": "1002",
        "owner": "postgres",
        "comment": tag,
        "evtname": MODULE.NAME,
        "evtfoid": "1001",
        "evtevent": "ddl_command_end",
        "evtenabled": "O",
        "evttags": ["GRANT"],
    }
    installed = {**deepcopy(baseline), "functions": [function], "triggers": [trigger]}
    for label, value in (
        ("before", baseline),
        ("installed", installed),
        ("failed", {**installed, "observedAt": "2026-10-28T00:00:41+00:00"}),
        ("current", {**installed, "observedAt": "2026-10-28T00:00:42+00:00"}),
        ("cleaned", {**baseline, "observedAt": "2026-10-28T00:00:43+00:00"}),
        ("retained", {**baseline, "schemaUsage": True, "observedAt": "2026-10-28T00:01:00+00:00"}),
    ):
        (root / f"grant-rollback.catalog-{label}.json").write_text(json.dumps(value))
    (root / "grant-rollback.worker.private").write_text(
        "[RUNTIME-GRANTS] Grant transaction failed; inspect retained Job state and private SQL diagnostics before a separately authorized fresh submission. No recovery or activation was performed.\n"
    )
    prefix = "user=perfeng_migration database=perfeng app=perfeng-runtime-grants-v1 "
    (root / "grant-rollback.postgres.private").write_text(
        "2026-10-28T00:00:21.3Z "
        + prefix
        + "ERROR:  "
        + MODULE.ERROR
        + "\n"
        + "2026-10-28T00:00:21.3Z "
        + prefix
        + "CONTEXT:  PL/pgSQL function fixture line 10 at RAISE\n"
        + "2026-10-28T00:00:21.4Z "
        + prefix
        + "STATEMENT:  "
        + MODULE.STATEMENT
        + "\n"
    )
    for label in ("denied", "contention", "rollback", "first", "repeat"):
        grants.run("verify-" + label, root, results)


def run(mode, root, results):
    with patch("sys.argv", ["rollback", mode, str(root), str(results)]):
        MODULE.main()


class RuntimeGrantsRollbackTests(unittest.TestCase):
    def test_exact_post_grant_error_and_retention_generate_only_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for mode in ("prepare", "installed", "cleanup", "cleaned", "retained"):
                run(mode, root, results)
            create = (root / "grant-rollback.create.sql").read_text()
            cleanup = (root / "grant-rollback.cleanup.sql").read_text()
            self.assertIn("SECURITY INVOKER SET search_path=pg_catalog", create)
            self.assertIn("ON ddl_command_end WHEN TAG IN ('GRANT')", create)
            self.assertIn(MODULE.BODY, create)
            self.assertNotIn("OR REPLACE", create)
            self.assertLess(
                MODULE.BODY.index("IF NOT has_schema_privilege"), MODULE.BODY.index(MODULE.ERROR)
            )
            self.assertIn(
                "current_user = 'perfeng_migration' AND session_user = 'perfeng_migration'",
                MODULE.BODY,
            )
            self.assertIn("btrim(current_query()) = '" + MODULE.STATEMENT + "'", MODULE.BODY)
            self.assertLess(
                cleanup.index("IF actual IS DISTINCT"), cleanup.index("DROP EVENT TRIGGER")
            )
            self.assertIn("DROP FUNCTION public." + MODULE.NAME + "() RESTRICT;", cleanup)
            self.assertNotIn("CASCADE", cleanup)
            self.assertNotIn("IF EXISTS", cleanup)
            self.assertFalse(list(results.glob("grant-parent-*")))
            grants.run("retained", root, results)
            self.assertEqual(len(list(results.glob("grant-parent-*.outcome.json"))), 5)
            self.assertEqual(
                json.loads((results / "grant-parent-rollback.outcome.json").read_text())[
                    "decision"
                ],
                "owner-grant-rollback-refused",
            )

    def test_foreign_definitions_wrong_errors_and_state_drift_never_generate_cleanup(self):
        cases = (
            ("grant-rollback.catalog-before.json", lambda x: x.update(schemaUsage=True)),
            (
                "grant-rollback.catalog-installed.json",
                lambda x: x["functions"][0].update(prosecdef=True),
            ),
            (
                "grant-rollback.catalog-installed.json",
                lambda x: x["triggers"][0].update(evttags=["REVOKE"]),
            ),
            (
                "grant-rollback.catalog-failed.json",
                lambda x: x["functions"][0].update(oid="replacement"),
            ),
            ("grant-rollback.catalog-failed.json", lambda x: x.update(schemaUsage=True)),
            (
                "grant-rollback.catalog-current.json",
                lambda x: x["functions"][0].update(owner="foreign"),
            ),
            (
                "grant-rollback.catalog-current.json",
                lambda x: x["functions"][0].update(prosrc="BEGIN RETURN; END;"),
            ),
            ("grant-rollback.catalog-current.json", lambda x: x["triggers"][0].update(oid="9999")),
            (
                "grant-rollback.catalog-current.json",
                lambda x: x["triggers"][0].update(comment="foreign"),
            ),
            ("grant-rollback.catalog-current.json", lambda x: x.update(grantSessions=1)),
            ("grant-rollback-after.schema.json", lambda x: x["ledger"].pop()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for name, mutate in cases:
                path = root / name
                raw = path.read_bytes()
                value = json.loads(raw)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.subTest(name=name), self.assertRaises((AssertionError, KeyError)):
                    run("cleanup", root, results)
                self.assertFalse((root / "grant-rollback.cleanup.sql").exists())
                path.write_bytes(raw)
            for name, value in (
                ("grant-rollback-after.acl.json", "changed ACLs"),
                ("grant-rollback-after.data.txt", "changed rows"),
            ):
                path = root / name
                raw = path.read_bytes()
                path.write_text(value)
                with self.assertRaises(AssertionError):
                    run("cleanup", root, results)
                path.write_bytes(raw)
            path = root / "grant-rollback.postgres.private"
            original = path.read_text()
            for wrong in (
                "",
                original.replace(MODULE.ERROR, "wrong error"),
                original.replace("perfeng_migration", "postgres"),
                original.replace("GRANT USAGE", "REVOKE USAGE"),
                original.replace("00:00:21.3", "00:00:23.3"),
            ):
                path.write_text(wrong)
                with self.subTest(log=wrong), self.assertRaises(AssertionError):
                    run("cleanup", root, results)
                self.assertFalse((root / "grant-rollback.cleanup.sql").exists())

    def test_retention_refuses_leftover_fixture_or_missing_successful_privilege(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            cleaned = root / "grant-rollback.catalog-cleaned.json"
            raw = cleaned.read_bytes()
            value = json.loads(raw)
            value["triggers"] = json.loads(
                (root / "grant-rollback.catalog-installed.json").read_text()
            )["triggers"]
            cleaned.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                run("cleaned", root, results)
            cleaned.write_bytes(raw)
            path = root / "grant-rollback.catalog-retained.json"
            value = json.loads(path.read_text())
            value["schemaUsage"] = False
            path.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                run("retained", root, results)

    @unittest.skipUnless(BASH, "Bash required for actual cleanup/continuation gates")
    def test_actual_coordinator_never_cleans_or_continues_after_unverified_boundaries(self):
        assert BASH
        text = (grants.FIXTURE / "runtime-grants-rollback.sh").read_text()
        library = text[text.index("runtime_grant_rollback() {") :]
        setup = """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD; current_stage=outer
step() { echo "$1" >> trace; [[ $FAIL != "$1" ]]; }
start_stage() { :; }; complete_stage() { :; }
grant_rollback_capture() { step "capture-${1##*catalog-}"; echo '{}' > "$1"; }
runtime_grant_parent() { step parent; }
uv() { step "verify-${6}"; }
sql() { read -r action; step "write-$action"; }
"""
        failures = (
            "pass",
            "capture-before.json",
            "verify-prepare",
            "write-create",
            "verify-installed",
            "parent",
            "capture-failed.json",
            "capture-current.json",
            "verify-cleanup",
            "write-cleanup",
            "capture-cleaned.json",
            "verify-cleaned",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for failure in failures:
                case = root / failure
                case.mkdir()
                (case / "grant-rollback.create.sql").write_text("create\n", newline="\n")
                (case / "grant-rollback.cleanup.sql").write_text("cleanup\n", newline="\n")
                script = case / "case.sh"
                script.write_text(
                    setup + library + "\nruntime_grant_rollback\necho fresh-parent >> trace\n"
                )
                result = subprocess.run(
                    [str(BASH), str(script)],
                    cwd=case,
                    env={**os.environ, "FAIL": failure, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (case / "trace").read_text().splitlines()
                with self.subTest(failure=failure):
                    self.assertEqual(
                        result.returncode, 0 if failure == "pass" else 1, result.stderr
                    )
                    self.assertEqual("fresh-parent" in trace, failure == "pass")
                    if failure != "pass" and failures.index(failure) <= failures.index(
                        "verify-cleanup"
                    ):
                        self.assertNotIn("write-cleanup", trace)


if __name__ == "__main__":
    unittest.main()
