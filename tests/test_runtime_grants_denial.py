"""Exact native denial, guarded restoration and fail-stop grant acceptance."""

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
from test_release_workflows_integration import BASH


def module(name):
    spec = importlib.util.spec_from_file_location(name, grants.FIXTURE / (name + ".py"))
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ROLE = module("schema_migration_denial")
with patch.dict("sys.modules", {"schema_migration_denial": ROLE}):
    MODULE = module("runtime_grants_denial")


class RuntimeGrantsDenialTests(unittest.TestCase):
    def test_only_create_is_removed_and_each_restoration_boundary_is_guarded(self):
        expected = MODULE.source()
        original = deepcopy(expected)
        original["metadata"].update(uid="role-uid", resourceVersion="10")
        narrowed = deepcopy(original)
        narrowed["rules"] = ROLE.narrowed(expected)
        narrowed["metadata"]["resourceVersion"] = "11"
        self.assertEqual(narrowed["rules"][:-1], original["rules"][:-1])
        self.assertEqual(narrowed["rules"][-1]["verbs"], ["get", "watch"])
        operations = ROLE.patch(original, narrowed, True, expected)
        self.assertEqual(
            [p["path"] for p in operations],
            ["/metadata/uid", "/metadata/resourceVersion", "/rules", "/rules"],
        )
        self.assertEqual([p["op"] for p in operations], ["test", "test", "test", "replace"])
        self.assertEqual(operations[-1]["value"], original["rules"])
        for mutate in (
            lambda x: x["metadata"].update(uid="replacement"),
            lambda x: x["metadata"].update(resourceVersion=""),
            lambda x: x["metadata"].update(name="release-schema-migration"),
            lambda x: x["metadata"].update(finalizers=["foreign"]),
            lambda x: x["rules"][0]["verbs"].clear(),
            lambda x: x["rules"][-1]["verbs"].append("delete"),
        ):
            bad = deepcopy(narrowed)
            mutate(bad)
            with self.assertRaises(AssertionError):
                ROLE.patch(original, bad, True, expected)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, value in (
                ("original", original),
                ("narrowed", narrowed),
                ("current", narrowed),
                ("restored", original),
                ("final", original),
            ):
                (root / f"grant-denied.role-{label}.json").write_text(json.dumps(value))
            for mode in ("prepare", "narrowed", "restore", "restored", "final"):
                with patch("sys.argv", ["role", mode, str(root)]):
                    MODULE.main()
            self.assertEqual(
                json.loads((root / "grant-denied.restore-patch.json").read_text()), operations
            )
            (root / "grant-denied.role-final.json").write_text(json.dumps(narrowed))
            with patch("sys.argv", ["role", "final", str(root)]), self.assertRaises(AssertionError):
                MODULE.main()

    def test_denial_needs_exact_native_create_error_and_unchanged_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            grants.populate(root, results)
            grants.run("verify-denied", root, results)
            log = root / "grant-denied.log"
            original_log = log.read_text()
            for wrong in (
                "",
                original_log.replace("cannot create", "cannot get"),
                original_log.replace("release-runtime-grants", "release-schema-migration"),
                original_log.replace("perf-platform", "argocd"),
                original_log.replace("jobs.batch", "pods"),
                original_log.replace("grant-denied", "wrong-name"),
                original_log.replace("Forbidden", "Timeout"),
            ):
                log.write_text(wrong)
                with self.subTest(log=wrong), self.assertRaises(AssertionError):
                    grants.run("verify-denied", root, results)
            log.write_text(original_log)
            for name, mutate in (
                (
                    "grant-denied.workflow.json",
                    lambda x: x["status"]["nodes"]["quiescence"].update(phase="Failed"),
                ),
                (
                    "grant-denied.workflow.json",
                    lambda x: x["status"]["nodes"][x["metadata"]["name"]].update(
                        outputs={"parameters": [{"name": "job-name", "value": "foreign"}]}
                    ),
                ),
                ("grant-denied.workers.json", lambda x: x[-1].update(exitCode=0)),
                (
                    "grant-denied.pods.json",
                    lambda x: x["items"].append({"metadata": {"uid": "foreign"}}),
                ),
                (
                    "grant-denied-after.jobs.json",
                    lambda x: x["items"].append(
                        {"kind": "Job", "metadata": {"name": "foreign", "uid": "foreign"}}
                    ),
                ),
            ):
                path = root / name
                raw = path.read_bytes()
                value = json.loads(raw)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.subTest(name=name), self.assertRaises((AssertionError, KeyError)):
                    grants.run("verify-denied", root, results)
                path.write_bytes(raw)
            (root / "grant-denied-after.acl.json").write_text("changed ACLs")
            with self.assertRaises(AssertionError):
                grants.run("verify-denied", root, results)
            self.assertFalse(list(results.glob("grant-parent-*")))

    @unittest.skipUnless(BASH, "Bash required for actual restoration sequencing")
    def test_no_restoration_or_successful_grants_after_unverified_fault(self):
        assert BASH
        text = (grants.FIXTURE / "runtime-grants-parent.sh").read_text()
        library = text[text.index("runtime_grant_denial() {") : text.index("\nk apply -f")]
        setup = """set -euo pipefail
fixture=$PWD; source_dir=$PWD; current_stage=outer; access_count=0
step() { echo "$1" >> trace; [[ $FAIL != "$1" ]]; }
start_stage() { :; }; complete_stage() { step complete; }
runtime_grant_access() { access_count=$((access_count + 1)); step "access-$access_count-$1"; }
runtime_grant_parent() { step refused-and-retained; }
uv() { step "verify-${6}"; }
k() {
  if [[ $* == *narrow-patch* ]]; then step narrow-write >&2
  elif [[ $* == *restore-patch* ]]; then step restore-write >&2
  else step read-role >&2; fi
  echo '{}'
}
"""
        failures = (
            "pass",
            "access-1-false",
            "read-role",
            "verify-prepare",
            "narrow-write",
            "verify-narrowed",
            "access-2-true",
            "refused-and-retained",
            "access-3-true",
            "verify-restore",
            "restore-write",
            "access-4-false",
            "verify-restored",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for failure in failures:
                case = root / failure
                case.mkdir()
                script = case / "case.sh"
                script.write_text(
                    setup + library + "\nruntime_grant_denial\necho first-and-repeat >> trace\n"
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
                    if failure == "pass":
                        self.assertLess(
                            trace.index("refused-and-retained"), trace.index("restore-write")
                        )
                        self.assertEqual(trace[-1], "first-and-repeat")
                    else:
                        self.assertEqual(trace[-1], failure)
                        self.assertNotIn("first-and-repeat", trace)
                        if failures.index(failure) <= failures.index("refused-and-retained"):
                            self.assertNotIn("restore-write", trace)

    @unittest.skipUnless(BASH, "Bash required for effective authority checks")
    def test_effective_access_rejects_transport_warnings_and_lost_reads(self):
        assert BASH
        text = (grants.FIXTURE / "runtime-grants-parent.sh").read_text()
        library = text[
            text.index("runtime_grant_access() {") : text.index("runtime_grant_denial() {")
        ]
        setup = """set -euo pipefail
fixture=$PWD
k() {
 echo "$*" >> trace
 if [[ $CASE == transport ]]; then echo no; return 2; fi
 if [[ $CASE == warning ]]; then echo warning >&2; echo no; return 1; fi
 if [[ $CASE == wrong-get && $* == *'can-i get '* ]]; then echo no; return 1; fi
 if [[ $* == *'can-i create '* ]]; then echo no; return 1; fi
 echo yes
}
sleep() { SECONDS=$((SECONDS + 31)); }
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for case_name in ("pass", "transport", "warning", "wrong-get"):
                case = root / case_name
                case.mkdir()
                script = case / "case.sh"
                script.write_text(
                    setup + library + "\nruntime_grant_access true\necho accepted >> trace\n"
                )
                result = subprocess.run(
                    [str(BASH), str(script)],
                    cwd=case,
                    env={**os.environ, "CASE": case_name, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (case / "trace").read_text()
                self.assertEqual(result.returncode, 0 if case_name == "pass" else 1, result.stderr)
                self.assertEqual("accepted" in trace, case_name == "pass")
                self.assertIn("--as-group system:serviceaccounts:argo-workflows", trace)
                self.assertIn("--as-group system:authenticated", trace)

    @unittest.skipUnless(BASH, "Bash required for native wait and final retention gates")
    def test_denied_wait_and_final_authority_cannot_publish_unproved_acceptance(self):
        assert BASH
        text = (grants.FIXTURE / "runtime-grants-parent.sh").read_text()
        native = text[
            text.index("runtime_grant_parent() {") : text.index("runtime_grant_retention() {")
        ]
        retained = text[
            text.index("runtime_grant_retention() {") : text.index("runtime_grant_access() {")
        ]
        setup = """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD; KUBECONFIG=$PWD/kubeconfig; current_stage=outer
step() { echo "$1" >> trace; [[ $CASE != "$1" ]]; }
start_stage() { :; }; complete_stage() { step complete; }
grant_snapshot() { step "snapshot-$1"; }
grant_sources() { step sources; }
a() { echo '{}'; }; k() { echo '{}'; }; jq() { echo native-id; }
uv() { step "verify-${6}"; }
runtime_grant_access() { step final-rights; }
runtime_grant_contention_retention() { step contention-retained; }
runtime_grant_rollback_retention() { step rollback-retained; }
timeout() { return "$WAIT_CODE"; }
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = [
                (f"wait-{code}", code, native, "runtime_grant_parent denied", code == 1)
                for code in (0, 1, 124, 137)
            ]
            cases += [
                (name, 0, retained, "runtime_grant_retention bootstrap", name == "pass")
                for name in (
                    "pass",
                    "final-rights",
                    "verify-final",
                    "contention-retained",
                    "rollback-retained",
                )
            ]
            for name, code, library, command, success in cases:
                case = root / name
                case.mkdir()
                script = case / "case.sh"
                script.write_text(setup + library + "\n" + command + "\necho accepted >> trace\n")
                result = subprocess.run(
                    [str(BASH), str(script)],
                    cwd=case,
                    env={**os.environ, "CASE": name, "WAIT_CODE": str(code), "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (case / "trace").read_text() if (case / "trace").exists() else ""
                self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
                self.assertEqual("accepted" in trace, success)
                if not success:
                    self.assertNotIn("verify-verify-denied", trace)
                    self.assertNotIn("verify-retained", trace)


if __name__ == "__main__":
    unittest.main()
