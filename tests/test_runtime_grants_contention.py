"""Causal SQL timeout, owned disconnect and fresh-parent gates, without a cluster."""

import importlib.util
import json
import os
import signal
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
    "runtime_grants_contention", grants.FIXTURE / "runtime_grants_contention.py"
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

    def save(name, value):
        (root / name).write_text(json.dumps(value))

    cluster = json.loads((root / "parent-request.json").read_text())["environment"]["clusterUID"]
    owner = {
        "pid": 101,
        "backendStart": "2026-10-27T23:59:50+00:00",
        "user": "postgres",
        "database": "perfeng",
        "application": "perfeng-grant-contention-" + cluster,
        "backendType": "client backend",
        "clientAddress": None,
        "state": "idle",
        "waitType": "Client",
        "waitEvent": "ClientRead",
        "query": f"SELECT pg_advisory_lock({MODULE.LOCK});",
        "blockers": [],
    }
    waiter = {
        "pid": 202,
        "backendStart": "2026-10-28T00:00:16.3+00:00",
        "user": "perfeng_migration",
        "database": "perfeng",
        "application": "perfeng-runtime-grants-v1",
        "backendType": "client backend",
        "clientAddress": "10.244.0.40",
        "state": "active",
        "waitType": "Lock",
        "waitEvent": "advisory",
        "query": f"SELECT pg_advisory_xact_lock({MODULE.LOCK});",
        "blockers": [101],
    }

    def lock(pid, granted):
        return {
            "pid": pid,
            "databaseOID": "1234",
            "classid": str(MODULE.LOCK >> 32),
            "objid": str(MODULE.LOCK & 0xFFFFFFFF),
            "objsubid": 1,
            "mode": "ExclusiveLock",
            "granted": granted,
        }

    held = {
        "observedAt": "2026-10-27T23:59:55+00:00",
        "databaseOID": "1234",
        "sessions": [owner],
        "locks": [lock(101, True)],
    }
    waited = {
        **deepcopy(held),
        "observedAt": "2026-10-28T00:00:17+00:00",
        "sessions": [owner, waiter],
        "locks": [lock(101, True), lock(202, False)],
    }
    for name in ("held", "candidate"):
        save(f"grant-contention.{name}.json", held)
    save(
        "grant-contention.before-release.json", {**held, "observedAt": "2026-10-28T00:00:41+00:00"}
    )
    save(
        "grant-contention.released.json",
        {**held, "observedAt": "2026-10-28T00:00:42+00:00", "sessions": [], "locks": []},
    )
    (root / "grant-contention.watch.ndjson").write_text(json.dumps(waited) + "\n")
    (root / "grant-contention.worker.private").write_text(
        "[RUNTIME-GRANTS] Grant transaction failed; inspect retained Job state and private SQL diagnostics before a separately authorized fresh submission. No recovery or activation was performed.\n"
    )
    prefix = "user=perfeng_migration database=perfeng app=perfeng-runtime-grants-v1 "
    (root / "grant-contention.postgres.private").write_text(
        "2026-10-28T00:00:21.3Z "
        + prefix
        + "ERROR:  canceling statement due to lock timeout\n"
        + "2026-10-28T00:00:21.3Z unrelated background message\n"
        + "2026-10-28T00:00:21.4Z "
        + prefix
        + f"STATEMENT:  SELECT pg_advisory_xact_lock({MODULE.LOCK});\n"
    )
    for label in ("denied", "contention", "rollback", "first", "repeat"):
        grants.run("verify-" + label, root, results)


def run(mode, root, results):
    with patch("sys.argv", ["contention", mode, str(root), str(results)]):
        MODULE.main()


class RuntimeGrantsContentionTests(unittest.TestCase):
    def test_exact_causal_failure_normal_release_and_retained_native_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for mode in ("held", "release", "released", "retained"):
                run(mode, root, results)
            self.assertFalse(list(results.glob("grant-parent-*")))
            grants.run("retained", root, results)
            self.assertEqual(len(list(results.glob("grant-parent-*.outcome.json"))), 5)
            report = json.loads((results / "grant-parent-contention.outcome.json").read_text())
            self.assertEqual(report["decision"], "owner-grant-contention-refused")

    def test_missing_or_wrong_attribution_state_and_native_failure_forbid_release(self):
        mutations = (
            ("grant-contention.watch.ndjson", lambda x: x["sessions"][1].update(user="postgres")),
            (
                "grant-contention.watch.ndjson",
                lambda x: x["sessions"][1].update(clientAddress="10.244.0.99"),
            ),
            ("grant-contention.watch.ndjson", lambda x: x["sessions"][1].update(blockers=[999])),
            ("grant-contention.watch.ndjson", lambda x: x["sessions"][1].update(query="SELECT 1;")),
            ("grant-contention.watch.ndjson", lambda x: x["locks"][1].update(granted=True)),
            ("grant-contention.watch.ndjson", lambda x: x["locks"][1].update(objid="1")),
            (
                "grant-contention.watch.ndjson",
                lambda x: x.update(observedAt="2026-10-28T00:00:30+00:00"),
            ),
            (
                "grant-contention.before-release.json",
                lambda x: x["sessions"][0].update(backendStart="2026-10-27T23:59:51+00:00"),
            ),
            (
                "grant-contention.job.json",
                lambda x: x["status"]["conditions"][0].update(reason="DeadlineExceeded"),
            ),
            (
                "grant-contention.pods.json",
                lambda x: x["items"][0]["status"]["containerStatuses"][0]["state"][
                    "terminated"
                ].update(exitCode=2),
            ),
            ("grant-contention-after.schema.json", lambda x: x["ledger"].pop()),
            ("grant-contention-after.sql.json", lambda x: x["metadata"].update(uid="replacement")),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for name, mutate in mutations:
                path = root / name
                raw = path.read_bytes()
                value = json.loads(raw)
                mutate(value)
                path.write_text(json.dumps(value) + "\n")
                with self.subTest(name=name), self.assertRaises((AssertionError, KeyError)):
                    run("release", root, results)
                path.write_bytes(raw)
            for name, replacement in (
                ("grant-contention.watch.ndjson", ""),
                ("grant-contention-after.acl.json", "changed ACLs"),
                ("grant-contention-after.data.txt", "changed rows"),
                (
                    "grant-contention.worker.private",
                    "[RUNTIME-GRANTS] failure\nReviewed runtime database privileges established.",
                ),
            ):
                path = root / name
                raw = path.read_bytes()
                path.write_text(replacement)
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    run("release", root, results)
                path.write_bytes(raw)
            path = root / "grant-contention.postgres.private"
            original = path.read_text()
            for wrong in (
                "",
                original.replace("lock timeout", "statement timeout"),
                original.replace("perfeng_migration", "perfeng_runtime"),
                original.replace(str(MODULE.LOCK), "42"),
                original.replace("00:00:21.3", "00:00:23.3"),
            ):
                path.write_text(wrong)
                with self.subTest(log=wrong), self.assertRaises(AssertionError):
                    run("release", root, results)
            self.assertFalse(list(results.glob("grant-parent-*")))

    def test_acquisition_and_release_poll_only_known_owned_sessions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            candidate = root / "grant-contention.candidate.json"
            value = json.loads(candidate.read_text())
            value["locks"] = []
            candidate.write_text(json.dumps(value))
            with self.assertRaises(SystemExit) as pending:
                run("held", root, results)
            self.assertEqual(pending.exception.code, 3)
            value["sessions"][0]["user"] = "foreign"
            candidate.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                run("held", root, results)
            released = root / "grant-contention.released.json"
            value = json.loads((root / "grant-contention.before-release.json").read_text())
            released.write_text(json.dumps(value))
            with self.assertRaises(SystemExit) as pending:
                run("released", root, results)
            self.assertEqual(pending.exception.code, 3)
            value["sessions"][0]["backendStart"] = "2026-10-27T23:59:51+00:00"
            released.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                run("released", root, results)

    @unittest.skipUnless(BASH, "Bash required for normal disconnect sequencing")
    def test_no_disconnect_or_fresh_parent_before_verified_failure_and_cleanup(self):
        assert BASH
        text = (grants.FIXTURE / "runtime-grants-contention.sh").read_text()
        body = text[text.index("  start_stage grant-contention-proof") : text.rindex("\n}")]
        self.assertIn("printf", body)
        setup = """set -euo pipefail
fixture=$PWD; results=$PWD; source_dir=$PWD; previous=outer
holder_pid=owned; exec {holder_fd}> "$fixture/input"
step() { echo "$1" >> trace; [[ $FAIL != "$1" ]]; }
start_stage() { :; }; complete_stage() { step complete; }
grant_contention_capture() { step capture; echo '{}' > "$1"; }
kill() { step holder-alive; }; uv() { step "verify-${6}"; }; wait() { step holder-exit; }
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for failure in (
                "pass",
                "capture",
                "holder-alive",
                "verify-release",
                "holder-exit",
                "verify-released",
            ):
                case = root / failure
                case.mkdir()
                script = case / "case.sh"
                script.write_text(setup + body + "\necho fresh-parent >> trace\n")
                result = subprocess.run(
                    [str(BASH), str(script)],
                    cwd=case,
                    env={**os.environ, "FAIL": failure, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                trace = (case / "trace").read_text()
                self.assertEqual(result.returncode, 0 if failure == "pass" else 1, result.stderr)
                self.assertEqual("fresh-parent" in trace, failure == "pass")
                if failure in {"capture", "holder-alive", "verify-release"}:
                    self.assertEqual((case / "input").read_text(), "")
                elif failure == "pass":
                    self.assertEqual((case / "input").read_text(), "\\q\n")

    @unittest.skipUnless(BASH and os.name != "nt", "Linux FIFO transport is verified on CI")
    def test_real_fifo_and_observer_stop_without_unproved_holder_disconnect(self):
        assert BASH
        text = (grants.FIXTURE / "runtime-grants-contention.sh").read_text()
        library = text[text.index("grant_contention_capture() {") :]
        setup = """set -euo pipefail
fixture=$1; results=$1; source_dir=$1; FAIL=$3; KUBECONFIG=$1/kubeconfig; database_pod=owned; cluster_uid=owned; current_stage=outer
step() { echo "$1" >> "$fixture/trace"; [[ $FAIL != "$1" ]]; }
start_stage() { :; }; complete_stage() { :; }
sql() { cat >/dev/null; echo '{}'; }; runtime_grant_parent() { step native; }
uv() { step "verify-${6}"; }
kubectl() {
 while IFS= read -r command; do
  if [[ $command == '\\q' ]]; then echo disconnected >> "$fixture/trace"; return 0; fi
  echo holder-acquired >> "$fixture/trace"
 done
}
timeout() { if [[ $1 == --kill-after=* ]]; then shift; fi; shift; "$@"; }
source "$2"
if [[ $FAIL == observer ]]; then grant_contention_observe() { step observer; }; fi
runtime_grant_contention
echo fresh-parent >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "library.sh").write_text(library, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            for failure in (
                "pass",
                "verify-held",
                "native",
                "observer",
                "verify-release",
                "verify-released",
            ):
                case = root / failure
                case.mkdir()
                (case / "grant-contention.sql").write_text("SELECT 1;\n")
                process = subprocess.Popen(
                    [str(BASH), str(root / "run.sh"), str(case), str(root / "library.sh"), failure],
                    env={**os.environ, "BASH_ENV": ""},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                try:
                    _, error = process.communicate(timeout=15)
                    trace = (case / "trace").read_text()
                    self.assertEqual(process.returncode, 0 if failure == "pass" else 1, error)
                    self.assertEqual("fresh-parent" in trace, failure == "pass")
                    if failure in {"verify-held", "native", "observer", "verify-release"}:
                        self.assertNotIn("disconnected", trace)
                    else:
                        self.assertIn("disconnected", trace)
                finally:
                    # Only the newly created fake-transport process group is owned here.
                    kill_group = getattr(os, "killpg", None)
                    assert callable(kill_group)
                    try:
                        kill_group(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
