"""Prove typed lock attribution, owned disconnect and fail-stop first installation."""

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

import test_schema_migration_install as installation
from test_release_workflows_integration import BASH

SPEC = importlib.util.spec_from_file_location(
    "schema_migration_contention",
    installation.repeats.pause.FIXTURE / "schema_migration_contention.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict("sys.modules", {"schema_migration_install": installation.MODULE}):
    SPEC.loader.exec_module(MODULE)


def populate(root, results):
    installation.populate(root, results)

    def load(name):
        return json.loads((root / name).read_text())

    def save(name, value):
        (root / name).write_text(json.dumps(value))

    workflow = load("schema-install.workflow.json")
    uid, name = workflow["metadata"]["uid"], workflow["metadata"]["name"]
    failure_uid = "00000000-0000-0000-0000-000000000099"
    failure_name = "schema-contention-ci"

    def adapted(value):
        return json.loads(json.dumps(value).replace(uid, failure_uid).replace(name, failure_name))

    workflow = adapted(workflow)
    cluster_uid = load("schema-install.before.namespace.json")["metadata"]["uid"]
    for node_name in ("preflight", "quiescence"):
        parameter = workflow["status"]["nodes"][node_name]["outputs"]["parameters"][0]
        result = json.loads(parameter["value"])
        result["clusterUID"] = cluster_uid
        parameter["value"] = json.dumps(result)
    workflow["status"].update(phase="Failed", finishedAt="2026-10-28T00:00:41+00:00")
    nodes = workflow["status"]["nodes"]
    nodes[failure_name]["phase"] = nodes["migrate"]["phase"] = "Failed"
    nodes[failure_name].pop("outputs")
    nodes["migrate"]["finishedAt"] = "2026-10-28T00:00:40+00:00"
    workers = load("schema-install.workers.json")
    next(w for w in workers if w["nodeID"] == "migrate")["exitCode"] = 1
    job = adapted(load("schema-install.job.json"))
    job["metadata"]["uid"] = "failed-native-job"
    job["status"] = {
        "failed": 1,
        "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
    }
    pods = adapted(load("schema-install.job-pods.json"))
    pod = pods["items"][0]
    pod["metadata"]["ownerReferences"][0]["uid"] = job["metadata"]["uid"]
    pod["status"].update(phase="Failed", podIP="10.244.0.40")
    pod["status"]["containerStatuses"][0]["state"]["terminated"] = {
        "exitCode": 1,
        "reason": "Error",
        "finishedAt": "2026-10-28T00:00:32+00:00",
    }
    for suffix, value in (
        ("workflow", workflow),
        ("workers", workers),
        ("job", job),
        ("job-pods", pods),
        ("retained-workflow", workflow),
        ("retained-job", job),
        ("jobs-after", {"items": [job]}),
    ):
        save(f"schema-contention.{suffix}.json", value)
    for suffix in ("objects", "applications", "pods", "pvs", "roles", "namespace"):
        save(
            f"schema-install.contention-after.{suffix}.json",
            load(f"schema-install.before.{suffix}.json"),
        )
    for suffix in ("templates", "intent"):
        save(
            f"schema-install.{suffix}-contention.json", load(f"schema-install.{suffix}-before.json")
        )
    cluster = load("schema-install.before.namespace.json")["metadata"]["uid"]
    owner = {
        "pid": 101,
        "backendStart": "2026-10-27T23:59:50+00:00",
        "user": "postgres",
        "database": "perfeng",
        "application": "perfeng-schema-contention-" + cluster,
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
        "backendStart": "2026-10-28T00:00:16.1+00:00",
        "user": "perfeng_migration",
        "database": "perfeng",
        "application": "perfeng-schema-migration-v1",
        "backendType": "client backend",
        "clientAddress": "10.244.0.40",
        "state": "active",
        "waitType": "Lock",
        "waitEvent": "advisory",
        "query": f"SELECT pg_advisory_xact_lock({MODULE.LOCK})",
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
        "schemaAbsent": True,
        "applicationRelations": 0,
        "sessions": [owner],
        "locks": [lock(101, True)],
    }
    waited = {
        **deepcopy(held),
        "observedAt": "2026-10-28T00:00:17+00:00",
        "sessions": [owner, waiter],
        "locks": [lock(101, True), lock(202, False)],
    }
    after_exit = {**waited, "observedAt": "2026-10-28T00:00:35+00:00"}
    save("schema-contention.held.json", held)
    save("schema-contention.candidate.json", held)
    save(
        "schema-contention.before-release.json",
        {**deepcopy(held), "observedAt": "2026-10-28T00:00:45+00:00"},
    )
    save(
        "schema-contention.released.json",
        {**held, "observedAt": "2026-10-28T00:00:46+00:00", "sessions": [], "locks": []},
    )
    (root / "schema-contention.watch.ndjson").write_text(
        "\n".join(json.dumps(s) for s in (held, waited, after_exit)) + "\n"
    )
    (root / "schema-contention.migrator.private").write_text(
        "Connecting to PostgreSQL for control-plane migrations...\nChecking migration versions and applying pending migrations...\nMigration failed; the transaction was rolled back or its commit outcome is uncertain. Check database availability, privileges and migration ledger before retrying.\nprivate-token-never-publish\n"
    )


def run(mode, root, results):
    with patch("sys.argv", ["contention", mode, str(root), str(results)]):
        MODULE.main()


class SchemaMigrationContentionTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "Linux FIFO transport is verified on CI")
    def test_real_fifo_holder_and_observer_stop_without_unproved_disconnect(self):
        assert BASH
        text = (installation.repeats.pause.FIXTURE / "schema-migration-contention.sh").read_text()
        library = text[
            text.index("schema_contention_capture() {") : text.rindex("\nschema_contention_suite")
        ]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; results=$1; source_dir=$1; failure=$3; KUBECONFIG=$1/kubeconfig; database_pod=owned; cluster_uid=owned
step() { echo "$1" >> "$fixture/trace"; [[ $failure != "$1" ]]; }
start_stage() { :; }; complete_stage() { :; }
sql() { cat >/dev/null; echo '{}'; }
schema_install_snapshot() { :; }; schema_install_sources() { :; }
schema_native_parent() { step native; }
k() { echo '{}'; }
uv() { step "verify-${6}"; }
kubectl() {
  while IFS= read -r command; do
    if [[ $command == '\q' ]]; then echo disconnected >> "$fixture/trace"; return 0; fi
    echo holder-acquired >> "$fixture/trace"
  done
}
timeout() { if [[ $1 == --kill-after=* ]]; then shift; fi; shift; "$@"; }
source "$2"
schema_contention_suite
echo fresh-install >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "library.sh").write_text(library, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            (root / "schema-contention.sql").write_text("SELECT 1;\n")
            for failure in ("pass", "native", "verify-release", "verify-released"):
                case = root / failure
                case.mkdir()
                (case / "schema-contention.sql").write_text("SELECT 1;\n")
                process = subprocess.Popen(
                    [BASH, str(root / "run.sh"), str(case), str(root / "library.sh"), failure],
                    env={**os.environ, "BASH_ENV": ""},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                try:
                    _, stderr = process.communicate(timeout=15)
                    trace = (case / "trace").read_text()
                    with self.subTest(failure=failure):
                        self.assertEqual(process.returncode, 0 if failure == "pass" else 1, stderr)
                        if failure == "pass":
                            self.assertIn("disconnected", trace)
                            self.assertIn("fresh-install", trace)
                        else:
                            self.assertNotIn("fresh-install", trace)
                            if failure != "verify-released":
                                self.assertNotIn("disconnected", trace)
                finally:
                    # Only the fake transport's newly created process group is owned here.
                    try:
                        kill_group = getattr(os, "killpg", None)
                        assert callable(kill_group)
                        kill_group(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)

    def test_exact_native_failure_owned_release_and_retention_with_lingering_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for mode in ("held", "release", "released", "retained"):
                run(mode, root, results)
            for path in results.glob("schema-contention*.json"):
                self.assertNotIn("private-token", path.read_text())
                self.assertNotIn("SELECT", path.read_text())
            self.assertEqual(
                json.loads((results / "schema-contention-retained.outcome.json").read_text())[
                    "decision"
                ],
                "schema-contention-retained",
            )

    def test_wrong_backend_lock_source_state_or_native_failure_never_permits_release(self):
        cases = (
            (
                "schema-contention.before-release.json",
                lambda x: x["sessions"][0].update(backendStart="2026-10-27T23:59:51+00:00"),
            ),
            ("schema-contention.before-release.json", lambda x: x.update(schemaAbsent=False)),
            (
                "schema-contention.before-release.json",
                lambda x: x.update(observedAt="2026-10-28T00:00:30+00:00"),
            ),
            ("schema-contention.before-release.json", lambda x: x["locks"][0].update(objid="1")),
            ("schema-contention.before-release.json", lambda x: x.update(databaseOID="4321")),
            (
                "schema-contention.job.json",
                lambda x: x["status"]["conditions"][0].update(reason="DeadlineExceeded"),
            ),
            (
                "schema-contention.workflow.json",
                lambda x: x["status"]["nodes"]["quiescence"].update(phase="Skipped"),
            ),
            (
                "schema-contention.workflow.json",
                lambda x: x["status"]["nodes"]["schema-contention-ci"].update(
                    outputs={"parameters": [{"name": "job-name", "value": "unexpected"}]}
                ),
            ),
            (
                "schema-contention.job-pods.json",
                lambda x: x["items"][0]["status"]["containerStatuses"][0]["state"][
                    "terminated"
                ].update(exitCode=143),
            ),
            ("schema-install.contention-after.roles.json", lambda x: x.update(roles="changed")),
            (
                "schema-install.templates-contention.json",
                lambda x: x["items"][0]["metadata"].update(uid="replacement"),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for filename, mutate in cases:
                path = root / filename
                original = path.read_bytes()
                value = json.loads(original)
                mutate(value)
                path.write_text(json.dumps(value))
                with self.subTest(file=filename), self.assertRaises((AssertionError, KeyError)):
                    run("release", root, results)
                self.assertFalse((results / "schema-contention.outcome.json").exists())
                path.write_bytes(original)
            watch = root / "schema-contention.watch.ndjson"
            original = watch.read_bytes()
            for field, value in (
                ("clientAddress", "10.244.0.99"),
                ("blockers", [303]),
                ("user", "postgres"),
                ("query", "CREATE SCHEMA wrong"),
                ("backendStart", "2026-10-27T00:00:00+00:00"),
            ):
                samples = [json.loads(line) for line in original.splitlines()]
                samples[1]["sessions"][1][field] = value
                watch.write_text("\n".join(json.dumps(s) for s in samples) + "\n")
                with self.subTest(field=field), self.assertRaises(AssertionError):
                    run("release", root, results)
                watch.write_bytes(original)

    def test_release_poll_allows_only_known_sessions_and_refuses_pid_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            before = json.loads((root / "schema-contention.before-release.json").read_text())
            path = root / "schema-contention.released.json"
            before["observedAt"] = "2026-10-28T00:00:46+00:00"
            path.write_text(json.dumps(before))
            with self.assertRaises(SystemExit) as pending:
                run("released", root, results)
            self.assertEqual(pending.exception.code, 3)
            before["sessions"][0]["backendStart"] = "2026-10-28T00:00:45+00:00"
            path.write_text(json.dumps(before))
            with self.assertRaises(AssertionError):
                run("released", root, results)
            self.assertFalse((results / "schema-contention-released.outcome.json").exists())

    @unittest.skipUnless(BASH, "Bash required")
    def test_normal_disconnect_and_fresh_install_are_gated_by_every_proof(self):
        assert BASH
        text = (installation.repeats.pause.FIXTURE / "schema-migration-contention.sh").read_text()
        # Exercise the actual post-parent proof/release section with an owned writable FD.
        body = text[
            text.index("  start_stage schema-contention-proof") : text.rindex(
                "\n}\n\nschema_contention_suite"
            )
        ]
        self.assertIn("printf", body)
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; results=$1; source_dir=$1; failure=$3; cluster_uid=owned
holder_pid=owned; exec {holder_fd}> "$fixture/input"
step() { echo "$1" >> "$fixture/trace"; [[ $failure != "$1" ]]; }
start_stage() { :; }; complete_stage() { step complete; }
schema_install_snapshot() { step snapshot; }; schema_install_sources() { step sources; }
schema_contention_capture() { step capture; echo '{}' > "$1"; }
k() { step inventory >&2; echo '{}'; }; kill() { step holder-alive; }
uv() { step "verify-${6}"; }
wait() { step holder-exit; }
source "$2"
echo fresh-install >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "body.sh").write_text(body, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            for failure in (
                "pass",
                "snapshot",
                "sources",
                "capture",
                "inventory",
                "holder-alive",
                "verify-release",
                "holder-exit",
                "verify-released",
            ):
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
                    trace = (case / "trace").read_text()
                    self.assertEqual(
                        process.returncode, 0 if failure == "pass" else 1, process.stderr
                    )
                    if failure == "pass":
                        self.assertIn("fresh-install", trace)
                        self.assertEqual((case / "input").read_text(), "\\q\n")
                    else:
                        self.assertNotIn("fresh-install", trace)
                        if failure not in {"holder-exit", "verify-released"}:
                            self.assertEqual((case / "input").read_text(), "")


if __name__ == "__main__":
    unittest.main()
