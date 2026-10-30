"""Exact complete-parent refusal and gated, identity-checked Role restoration."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_database_preparation_acceptance as preparation
from test_release_workflows_integration import BASH

MODULE = preparation.MODULE


def run(mode, root, results):
    with patch("sys.argv", ["preparation", mode, str(root), str(results)]):
        MODULE.main()


class PreparationJobDenialTests(unittest.TestCase):
    def test_restore_requires_exact_refusal_state_and_guarded_current_role(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            preparation.populate(root, results)
            earlier = root / "grant-denied.role-original.json"
            earlier.write_text("retained earlier denial evidence\n")
            raw_earlier = earlier.read_bytes()
            run("role-prepare", root, results)
            narrow = MODULE.load(root, "preparation-denied.narrow-patch.json")
            self.assertEqual(
                [op["path"] for op in narrow],
                ["/metadata/uid", "/metadata/resourceVersion", "/rules", "/rules"],
            )
            self.assertEqual(narrow[-1]["value"], preparation.DENIAL.narrowed(MODULE.role_source()))
            run("role-narrowed", root, results)
            restore = root / "preparation-denied.restore-patch.json"
            log = root / "preparation-denied.worker.private"
            correct_log = log.read_text()
            for bad_log in (
                "Error from server (NotFound): missing Job",
                correct_log.replace("release-runtime-grants", "release-schema-migration"),
                correct_log.replace("perf-platform", "foreign"),
            ):
                log.write_text(bad_log)
                with self.subTest(log=bad_log[:40]), self.assertRaises(AssertionError):
                    run("role-restore", root, results)
                self.assertFalse(restore.exists())
            log.write_text(correct_log)
            cases = (
                (
                    "preparation-denied.diagnostic-pod.json",
                    lambda v: v["metadata"].update(uid="another-worker"),
                ),
                (
                    "preparation-denied.authority.json",
                    lambda v: next(
                        o
                        for o in v["items"]
                        if o["kind"] == "Role" and o["metadata"]["namespace"] == "perf-platform"
                    )["rules"][0]["verbs"].append("patch"),
                ),
                (
                    "preparation-denied.role-current.json",
                    lambda v: v["metadata"].update(uid="replacement"),
                ),
                (
                    "preparation-denied.role-current.json",
                    lambda v: v.update(rules=MODULE.role_source()["rules"]),
                ),
                (
                    "preparation-denied.jobs.json",
                    lambda v: v["items"].append(
                        MODULE.load(root, "preparation-first.migrate.job.json")
                    ),
                ),
            )
            for name, mutate in cases:
                path = root / name
                raw = path.read_bytes()
                value = json.loads(raw)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    run("role-restore", root, results)
                self.assertFalse(restore.exists())
                path.write_bytes(raw)
            path = root / "preparation-denied.acl.json"
            raw = path.read_bytes()
            path.write_bytes(b"changed SQL privileges")
            with self.assertRaises(AssertionError):
                run("role-restore", root, results)
            self.assertFalse(restore.exists())
            path.write_bytes(raw)
            run("role-restore", root, results)
            planned = json.loads(restore.read_text())
            current = MODULE.load(root, "preparation-denied.role-current.json")
            self.assertEqual(planned[0]["value"], current["metadata"]["uid"])
            self.assertEqual(planned[1]["value"], current["metadata"]["resourceVersion"])
            self.assertEqual(planned[2]["value"], current["rules"])
            self.assertEqual(planned[-1]["value"], MODULE.role_source()["rules"])
            run("role-restored", root, results)
            self.assertEqual(earlier.read_bytes(), raw_earlier)

    def test_failed_parent_and_restored_authority_remain_required_at_final_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            preparation.populate(root, results)
            MODULE.verify(root, results, "complete")
            cases = (
                (
                    "preparation-denied.current-workflow.json",
                    lambda v: v["status"].update(phase="Succeeded"),
                ),
                (
                    "preparation-denied.current-workers.json",
                    lambda v: next(w for w in v if w["nodeID"].endswith("migrate-migrate")).update(
                        exitCode=0
                    ),
                ),
                (
                    "preparation-denied.role-final.json",
                    lambda v: v["metadata"].update(uid="replacement"),
                ),
                (
                    "preparation-denied.role-final.json",
                    lambda v: v.update(rules=preparation.DENIAL.narrowed(MODULE.role_source())),
                ),
                (
                    "preparation-denied.qualify.current-job.json",
                    lambda v: MODULE.load(root, "preparation-first.qualify.job.json"),
                ),
            )
            for name, mutate in cases:
                path = root / name
                raw = path.read_bytes()
                value = json.loads(raw)
                replacement = mutate(value)
                path.write_text(json.dumps(replacement if replacement is not None else value))
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    MODULE.verify(root, results, "final")
                self.assertFalse((results / "database-preparation-final.outcome.json").exists())
                path.write_bytes(raw)
            MODULE.verify(root, results, "final")
            reports = MODULE.load(results, "database-preparation-final.outcome.json")
            self.assertEqual(len(reports), 4)
            self.assertEqual(reports[1]["decision"], "database-preparation-job-create-refused")
            self.assertEqual(reports[1]["jobUIDs"], [])

    @unittest.skipUnless(BASH, "Bash required for effective-authority diagnostic isolation")
    def test_effective_authority_checks_preserve_earlier_denial_diagnostics(self):
        assert BASH
        source = (preparation.FIXTURE / "runtime-grants-parent.sh").read_text()
        function = source[
            source.index("runtime_grant_access() {") : source.index("\nruntime_grant_denial() {")
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            earlier = root / "grant-denied.auth.private"
            earlier.write_text("earlier diagnostic")
            script = root / "access.sh"
            script.write_text(
                """set -euo pipefail
fixture=$PWD
k() { if [[ $* == *'can-i create '* ]]; then echo no; return 1; fi; echo yes; }
"""
                + function
                + "\nruntime_grant_access true preparation-denied\n",
                newline="\n",
            )
            result = subprocess.run(
                [BASH, str(script)],
                cwd=root,
                capture_output=True,
                text=True,
                env={**os.environ, "BASH_ENV": ""},
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(earlier.read_text(), "earlier diagnostic")
            self.assertEqual((root / "preparation-denied.auth.private").read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
