"""Offline rollback attribution, fixture identity and destructive-cleanup gates."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_schema_migration_contention as contention
from test_release_workflows_integration import BASH

SPEC = importlib.util.spec_from_file_location(
    "schema_migration_rollback",
    contention.installation.repeats.pause.FIXTURE / "schema_migration_rollback.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict("sys.modules", {"schema_migration_install": contention.installation.MODULE}):
    SPEC.loader.exec_module(MODULE)


def populate(root, results):
    contention.populate(root, results)

    def load(name):
        return json.loads((root / name).read_text())

    def save(name, value):
        (root / name).write_text(json.dumps(value))

    originals = {
        suffix: load(f"schema-contention.{suffix}.json")
        for suffix in ("workflow", "job", "job-pods", "workers")
    }
    old_uid = originals["workflow"]["metadata"]["uid"]
    old_name = originals["workflow"]["metadata"]["name"]
    for suffix, value in originals.items():
        raw = (
            json.dumps(value)
            .replace(old_uid, "00000000-0000-0000-0000-000000000088")
            .replace(old_name, "schema-rollback-ci")
            .replace("failed-native-job", "rollback-native-job")
            .replace("2026-10-28T00:00:", "2026-10-28T00:01:")
        )
        save(f"schema-rollback.{suffix}.json", json.loads(raw))
    workflow = load("schema-rollback.workflow.json")
    workflow["metadata"]["creationTimestamp"] = "2026-10-28T00:01:00+00:00"
    save("schema-rollback.workflow.json", workflow)
    save("schema-rollback.retained-workflow.json", workflow)
    job = load("schema-rollback.job.json")
    save("schema-rollback.retained-job.json", job)
    save("schema-rollback.jobs-after.json", {"items": [originals["job"], job]})
    for label in ("rollback-after", "rollback-cleaned"):
        for suffix in ("objects", "applications", "pods", "pvs", "roles", "namespace"):
            save(
                f"schema-install.{label}.{suffix}.json",
                load(f"schema-install.before.{suffix}.json"),
            )
    for suffix in ("templates", "intent"):
        save(f"schema-install.{suffix}-rollback.json", load(f"schema-install.{suffix}-before.json"))
    base = {
        "observedAt": "2026-10-28T00:00:50+00:00",
        "databaseOID": "1234",
        "schema": None,
        "relations": [],
        "columns": [],
        "migrationSessions": 0,
        "migrationLocks": 0,
    }
    save("schema-rollback.empty.catalog.json", base)
    value = MODULE.marker(root)
    fixture = {
        **base,
        "observedAt": "2026-10-28T00:00:55+00:00",
        "schema": {"oid": "5000", "owner": "perfeng_migration", "comment": value},
        "relations": [
            {
                "oid": "5001",
                "name": "artifacts",
                "kind": "r",
                "owner": "perfeng_migration",
                "comment": value,
                "toastOID": "0",
            }
        ],
        "columns": [
            {
                "name": "probe",
                "type": "integer",
                "notNull": True,
                "default": None,
                "identity": "",
                "generated": "",
            }
        ],
    }
    save("schema-rollback.fixture.catalog.json", fixture)
    save(
        "schema-rollback.after.catalog.json", {**fixture, "observedAt": "2026-10-28T00:01:45+00:00"}
    )
    save(
        "schema-rollback.cleaned.catalog.json", {**base, "observedAt": "2026-10-28T00:01:46+00:00"}
    )
    for label in ("fixture", "after"):
        save(f"schema-rollback.{label}.rows.json", [{"probe": 1}])
    (root / "schema-rollback.migrator.private").write_text(
        (root / "schema-contention.migrator.private").read_text()
    )
    (root / "schema-rollback.postgres.private").write_text(
        '2026-10-28T00:01:17Z user=perfeng_migration database=perfeng app=perfeng-schema-migration-v1 ERROR:  relation "artifacts" already exists\n2026-10-28T00:01:17Z user=perfeng_migration database=perfeng app=perfeng-schema-migration-v1 STATEMENT: CREATE TABLE perfeng_control.runs (...);\n CREATE TABLE perfeng_control.create_bindings (...);\n CREATE TABLE perfeng_control.artifacts (...);\nprivate-token-do-not-publish\n'
    )


def run(mode, root, results):
    with patch("sys.argv", ["rollback", mode, str(root), str(results)]):
        MODULE.main()


class SchemaMigrationRollbackTests(unittest.TestCase):
    def test_proved_rollback_generates_transactional_guarded_restrict_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for mode in ("prepare", "fixture", "cleanup", "cleaned", "retained"):
                run(mode, root, results)
            sql = (root / "schema-rollback.cleanup.sql").read_text()
            self.assertTrue(sql.startswith("BEGIN;"))
            self.assertTrue(sql.endswith("COMMIT;\n"))
            self.assertLess(sql.index("LOCK TABLE"), sql.index("IS DISTINCT FROM"))
            self.assertLess(sql.index("RAISE EXCEPTION"), sql.index("DROP TABLE"))
            self.assertIn('"oid": "5000"', sql)
            self.assertIn('"oid": "5001"', sql)
            self.assertIn("DROP TABLE perfeng_control.artifacts RESTRICT;", sql)
            self.assertIn("DROP SCHEMA perfeng_control RESTRICT;", sql)
            self.assertNotIn("CASCADE", sql)
            self.assertNotIn("IF EXISTS", sql)
            for path in results.glob("schema-rollback-*.json"):
                self.assertNotIn("private-token", path.read_text())

    def test_any_unproved_identity_rollback_or_native_outcome_withholds_cleanup(self):
        cases = (
            ("schema-rollback.after.catalog.json", lambda x: x["schema"].update(oid="6000")),
            ("schema-rollback.after.catalog.json", lambda x: x["schema"].update(owner="postgres")),
            (
                "schema-rollback.after.catalog.json",
                lambda x: x["relations"][0].update(comment="foreign"),
            ),
            (
                "schema-rollback.after.catalog.json",
                lambda x: x["relations"].append({"name": "schema_migrations"}),
            ),
            ("schema-rollback.after.catalog.json", lambda x: x.update(migrationSessions=1)),
            ("schema-rollback.after.rows.json", lambda x: x[0].update(probe=2)),
            (
                "schema-rollback.job.json",
                lambda x: x["status"]["conditions"][0].update(reason="DeadlineExceeded"),
            ),
            (
                "schema-rollback.workflow.json",
                lambda x: x["status"]["nodes"]["quiescence"].update(phase="Skipped"),
            ),
            ("schema-install.rollback-after.roles.json", lambda x: x.update(roles="changed")),
            (
                "schema-install.templates-rollback.json",
                lambda x: x["items"][0]["metadata"].update(uid="replacement"),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for name, mutate in cases:
                path = root / name
                original = path.read_bytes()
                obj = json.loads(original)
                mutate(obj)
                path.write_text(json.dumps(obj))
                with self.subTest(file=name), self.assertRaises((AssertionError, KeyError)):
                    run("cleanup", root, results)
                self.assertFalse((root / "schema-rollback.cleanup.sql").exists())
                path.write_bytes(original)
            path = root / "schema-rollback.postgres.private"
            original = path.read_text()
            for wrong in (
                original.replace("user=perfeng_migration", "user=postgres"),
                original.replace('relation "artifacts"', 'relation "runs"'),
                original.replace("00:01:17Z", "00:00:17Z"),
                original.replace("CREATE TABLE perfeng_control.runs", "unrelated"),
            ):
                path.write_text(wrong)
                with self.assertRaises(AssertionError):
                    run("cleanup", root, results)
                self.assertFalse((root / "schema-rollback.cleanup.sql").exists())
            path.write_text(original)

    def test_cleaned_and_retained_gates_refuse_leftover_schema_and_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            path = root / "schema-rollback.cleaned.catalog.json"
            original = path.read_bytes()
            path.write_text((root / "schema-rollback.after.catalog.json").read_text())
            with self.assertRaises(AssertionError):
                run("cleaned", root, results)
            self.assertFalse((results / "schema-rollback-cleaned.outcome.json").exists())
            path.write_bytes(original)
            for suffix in ("job", "workflow"):
                path = root / f"schema-rollback.retained-{suffix}.json"
                original = path.read_bytes()
                value = json.loads(original)
                value["metadata"]["uid"] = "replacement"
                path.write_text(json.dumps(value))
                with self.assertRaises(AssertionError):
                    run("retained", root, results)
                path.write_bytes(original)

    @unittest.skipUnless(BASH, "Bash required")
    def test_actual_shell_never_deletes_fixture_or_installs_after_unproved_boundary(self):
        assert BASH
        text = (
            contention.installation.repeats.pause.FIXTURE / "schema-migration-rollback.sh"
        ).read_text()
        library = text[
            text.index("schema_rollback_suite() {") : text.rindex("\nschema_rollback_suite")
        ]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; results=$1; source_dir=$1; failure=$3; database_pod=owned
step() { echo "$1" >> "$fixture/trace"; [[ $failure != "$1" ]]; }
start_stage() { :; }; complete_stage() { :; }
schema_rollback_catalog() { step "catalog-$1"; }
schema_rollback_rows() { step "rows-$1"; }
schema_install_snapshot() { step "snapshot-$1"; }
schema_install_sources() { step sources; }
schema_native_parent() { step native; }
k() { step api >&2; echo '{}'; }; jq() { echo time; }
uv() {
 step "verify-${6}" || return $?
 if [[ ${6} == prepare ]]; then echo create > "$fixture/schema-rollback.create.sql"; fi
 if [[ ${6} == cleanup ]]; then echo cleanup > "$fixture/schema-rollback.cleanup.sql"; fi
}
sql() { local command; command=$(cat); step "sql-$command"; }
source "$2"
schema_rollback_suite
echo fresh-install >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "body.sh").write_text(library, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            failures = (
                "pass",
                "catalog-empty",
                "verify-prepare",
                "sql-create",
                "catalog-fixture",
                "rows-fixture",
                "verify-fixture",
                "native",
                "catalog-after",
                "rows-after",
                "snapshot-rollback-after",
                "sources",
                "api",
                "verify-cleanup",
                "sql-cleanup",
                "catalog-cleaned",
                "snapshot-rollback-cleaned",
                "verify-cleaned",
            )
            for failure in failures:
                case = root / failure
                case.mkdir()
                process = subprocess.run(
                    [
                        BASH,
                        (root / "run.sh").as_posix(),
                        case.as_posix(),
                        (root / "body.sh").as_posix(),
                        failure,
                    ],
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                with self.subTest(failure=failure):
                    trace = (case / "trace").read_text().splitlines()
                    self.assertEqual(
                        process.returncode, 0 if failure == "pass" else 1, process.stderr
                    )
                    if failure == "pass":
                        self.assertLess(trace.index("verify-cleanup"), trace.index("sql-cleanup"))
                        self.assertEqual(trace[-1], "fresh-install")
                    else:
                        self.assertNotIn("fresh-install", trace)
                        if failures.index(failure) <= failures.index("verify-cleanup"):
                            self.assertNotIn("sql-cleanup", trace)


if __name__ == "__main__":
    unittest.main()
