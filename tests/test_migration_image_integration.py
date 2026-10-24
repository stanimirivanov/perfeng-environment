"""Offline checks for selected-image migration identity, freshness and schema evidence."""

import copy
import importlib.util
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SPEC = importlib.util.spec_from_file_location(
    "migration_evidence", FIXTURE / "migration_image_evidence.py"
)
assert SPEC is not None and SPEC.loader is not None
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)


class MigrationImageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads((FIXTURE / "migration-image.json").read_text())

    def startup_objects(self):
        manifest = yaml.safe_load((FIXTURE / "migration-image-workflow.yaml").read_text())
        job = yaml.safe_load(manifest["spec"]["templates"][0]["resource"]["manifest"])
        job["metadata"].update(
            name="migration-image-ci-test", uid="00000000-0000-0000-0000-000000000001"
        )
        job["spec"]["template"]["spec"]["containers"][0]["image"] = self.contract["image"]
        pod = {
            "kind": "Pod",
            "metadata": {
                "namespace": "perf-platform",
                "uid": "00000000-0000-0000-0000-000000000002",
                "ownerReferences": [
                    {
                        "kind": "Job",
                        "name": job["metadata"]["name"],
                        "uid": job["metadata"]["uid"],
                        "controller": True,
                    }
                ],
            },
            "spec": {"nodeName": "private-node", "secret": "must-never-be-emitted"},
            "status": {
                "phase": "Pending",
                "containerStatuses": [
                    {
                        "name": "migrate",
                        "state": {
                            "waiting": {
                                "reason": "ErrImagePull",
                                "message": "unauthorized https://user:secret@registry/private",
                            }
                        },
                    }
                ],
            },
        }
        return job, pod

    def test_startup_retains_owned_image_pull_evidence_after_pod_deletion(self):
        job, pod = self.startup_objects()
        stream = "\n".join(
            json.dumps({"type": kind, "object": pod}) for kind in ("ADDED", "MODIFIED", "DELETED")
        )
        report = EVIDENCE.startup(stream, job, self.contract, 143, 1)
        self.assertEqual(report["purpose"], "diagnostic-only")
        self.assertEqual(report["nativeWaitExitCode"], 1)
        self.assertEqual(report["watch"], "interrupted")
        self.assertEqual(report["limitations"], [])
        self.assertEqual(len(report["observations"]), 1)
        value = report["observations"][0]
        self.assertEqual(value["waitingReason"], "ErrImagePull")
        self.assertEqual(value["signals"], ["authorization"])
        for secret in ("secret", "private-node", "https", "user", "registry", "must-never"):
            self.assertNotIn(secret, json.dumps(report))

    def test_startup_rejects_foreign_pods_and_preserves_diagnostic_limits(self):
        job, pod = self.startup_objects()
        for change in ("uid", "name", "kind", "controller", "namespace"):
            with self.subTest(change=change):
                foreign = copy.deepcopy(pod)
                if change == "namespace":
                    foreign["metadata"]["namespace"] = "foreign"
                else:
                    foreign["metadata"]["ownerReferences"][0][change] = "foreign"
                report = EVIDENCE.startup(
                    json.dumps({"type": "ADDED", "object": foreign}), job, self.contract, 1, 64
                )
                self.assertEqual(report["observations"], [])
                self.assertIn("no-owned-pod-observed", report["limitations"])
                self.assertEqual(report["watch"], "failed")
        stream = json.dumps({"type": "ADDED", "object": pod}) + '\n{"type":'
        report = EVIDENCE.startup(stream, job, self.contract, 143, 1)
        self.assertEqual(len(report["observations"]), 1)
        self.assertIn("incomplete-or-invalid-stream", report["limitations"])
        report = EVIDENCE.startup(
            json.dumps({"type": "ERROR", "object": {"message": "secret"}}), job, self.contract, 0, 0
        )
        self.assertIn("api-watch-error", report["limitations"])
        self.assertNotIn("secret", json.dumps(report))

    def test_startup_distinguishes_states_and_redacts_unknown_values(self):
        job, pod = self.startup_objects()
        cases = (
            (
                {"waiting": {"reason": "ImagePullBackOff", "message": "manifest unknown"}},
                "waiting",
                "manifest-missing",
            ),
            (
                {"waiting": {"reason": "CreateContainerConfigError", "message": "secret missing"}},
                "waiting",
                None,
            ),
            (
                {
                    "waiting": {
                        "reason": "private-secret",
                        "message": "x509 certificate signed by unknown authority",
                    }
                },
                "waiting",
                "tls",
            ),
            (
                {"waiting": {"reason": "ContainerCreating", "message": "no such host"}},
                "waiting",
                "network",
            ),
            ({"running": {"startedAt": "private-secret"}}, "running", None),
            ({"terminated": {"exitCode": 1, "message": "private-secret"}}, "terminated", None),
        )
        for state, expected_state, signal in cases:
            with self.subTest(state=expected_state, signal=signal):
                pod["status"]["containerStatuses"][0]["state"] = state
                report = EVIDENCE.startup(
                    json.dumps({"type": "MODIFIED", "object": pod}), job, self.contract, 0, 1
                )
                observed = report["observations"][0]
                self.assertEqual(observed["containerState"], expected_state)
                self.assertEqual(observed["signals"], [signal] if signal else [])
                self.assertNotIn("private-secret", json.dumps(report))
                if expected_state == "terminated":
                    self.assertEqual(observed["exitCode"], 1)
        pod["status"] = {
            "phase": "Pending",
            "conditions": [
                {
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "private-secret",
                }
            ],
        }
        pod["spec"].pop("nodeName")
        observed = EVIDENCE.startup(
            json.dumps({"type": "ADDED", "object": pod}), job, self.contract, 0, 1
        )["observations"][0]
        self.assertFalse(observed["scheduled"])
        self.assertEqual(observed["schedulingReason"], "Unschedulable")

    def test_startup_cli_projects_only_safe_report_and_binds_selected_job(self):
        job, pod = self.startup_objects()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            watch, selected, contract = (
                directory / name for name in ("watch.json", "job.json", "contract.json")
            )
            watch.write_text(json.dumps({"type": "ADDED", "object": pod}))
            selected.write_text(json.dumps(job))
            contract.write_text(json.dumps(self.contract))
            import sys

            command = [
                sys.executable,
                str(FIXTURE / "migration_image_evidence.py"),
                "startup",
                str(watch),
                str(selected),
                str(contract),
                "143",
                "64",
            ]
            process = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(
                json.loads(process.stdout),
                EVIDENCE.startup(watch.read_text(), job, self.contract, 143, 64),
            )
            job["spec"]["template"]["spec"]["containers"][0]["image"] = "foreign"
            selected.write_text(json.dumps(job))
            process = subprocess.run(command, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(process.returncode, 0)
            self.assertNotIn("foreign", process.stderr)

    def test_startup_watch_is_stopped_before_post_wait_log_failure_paths(self):
        shell = (FIXTURE / "migration-image.sh").read_text()
        self.assertLess(shell.index("migration_watch_pid=$!"), shell.index("if timeout 270s argo"))
        self.assertLess(
            shell.index('wait "$migration_watch_pid"'), shell.index('a get "$migration_name"')
        )
        self.assertLess(
            shell.index('migration_image_evidence.py" startup'), shell.index('logs "job/$job"')
        )
        self.assertIn('"$migration_watch_rc" "$migration_rc"', shell)
        self.assertNotIn('"$results/migration-startup.watch', shell)

    @unittest.skipUnless(BASH, "Bash unavailable")
    def test_native_wait_codes_survive_watch_completion_and_failure(self):
        assert BASH is not None
        shell = (FIXTURE / "migration-image.sh").read_text()
        block = (
            "  timeout --kill-after=5s 275s kubectl"
            + shell.split("  timeout --kill-after=5s 275s kubectl", 1)[1].split(
                '\n  a get "$migration_name"', 1
            )[0]
        )
        # Exercise the real coordinator block with inert command boundaries.
        # A failed watch must not replace the native wait's captured exit code.
        for native_code, watch_code in ((0, 0), (1, 0), (64, 7), (124, 7), (137, 0)):
            with (
                self.subTest(native=native_code, watch=watch_code),
                tempfile.TemporaryDirectory() as temporary,
            ):
                script = (
                    """set -euo pipefail
fixture=$1
KUBECONFIG=$fixture/kubeconfig
migration_name=owned-workflow
timeout() {
  if [[ $1 == --kill-after=* ]]; then shift; fi
  shift
  "$@"
}
kubectl() { echo '{"type":"BOOKMARK"}'; return "$watch_code"; }
argo() {
  wait "$migration_watch_pid" || true
  return "$native_code"
}
"""
                    + f"native_code={native_code}\nwatch_code={watch_code}\n"
                    + block
                    + """
printf '%s %s' "$migration_rc" "$migration_watch_rc"
"""
                )
                process = subprocess.run(
                    [BASH, "-s", "--", Path(temporary).as_posix()],
                    input=script,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                codes = process.stdout.split()
                self.assertEqual(int(codes[0]), native_code)
                self.assertEqual(int(codes[1]), watch_code)

    def schema(self):
        return {
            "schemaOID": "100",
            "schemaOwner": "perfeng_migration",
            "relations": [
                {"name": "schema_migrations", "oid": "101", "owner": "perfeng_migration"}
            ],
            "ledger": [
                {"version": name, "sha256": digest, "appliedAt": "2026-10-22T10:00:00+00:00"}
                for name, digest in self.contract["migrations"].items()
            ],
        }

    def populate(self, directory):
        def save(name, value):
            (directory / name).write_text(json.dumps(value))

        preflight = {
            "requestRevision": "a" * 40,
            "requestDigest": "sha256:" + "b" * 64,
            "selectionRevision": "c" * 40,
            "clusterUID": "cluster-ci",
        }
        save("parent-first.result.json", preflight)
        manifest = yaml.safe_load((FIXTURE / "migration-image-workflow.yaml").read_text())
        for label, key, code in (
            ("runtime-role", "runtime-url", 1),
            ("first", "migration-url", 0),
            ("repeat", "migration-url", 0),
        ):
            prefix = "migration-image-" + label
            checkpoint = {
                **preflight,
                "kind": "PerfengMaintenanceCheckpoint",
                "status": "quiescence-observed",
            }
            save(prefix + ".checkpoint.json", checkpoint)
            save(
                prefix + ".checkpoint.workflow.json",
                {
                    "metadata": {"name": "checkpoint-" + label, "uid": "checkpoint-uid-" + label},
                    "status": {
                        "phase": "Succeeded",
                        "finishedAt": "2026-10-22T10:00:00Z",
                        "nodes": {
                            "checkpoint-" + label: {
                                "outputs": {
                                    "parameters": [
                                        {"name": "result", "value": json.dumps(checkpoint)}
                                    ]
                                }
                            }
                        },
                    },
                },
            )
            save(
                prefix + ".workflow.json",
                {
                    "metadata": {
                        "uid": "workflow-uid-" + label,
                        "creationTimestamp": "2026-10-22T10:00:01Z",
                    },
                    "status": {"phase": "Failed" if code else "Succeeded"},
                },
            )
            job = yaml.safe_load(manifest["spec"]["templates"][0]["resource"]["manifest"])
            job["metadata"] = {
                "uid": "job-uid-" + label,
                "creationTimestamp": "2026-10-22T10:00:02Z",
            }
            container = job["spec"]["template"]["spec"]["containers"][0]
            container["image"] = self.contract["image"]
            container["env"][0]["valueFrom"]["secretKeyRef"]["key"] = key
            container["env"][1]["value"] = "migration-ci-" + label
            job["status"] = {"failed": 1} if code else {"succeeded": 1}
            save(prefix + ".job.json", job)
            save(
                prefix + ".worker.json",
                [
                    {
                        "uid": "pod-uid-" + label,
                        "containers": [
                            {"name": "migrate", "exitCode": code, "waitingReason": None}
                        ],
                    }
                ],
            )
        save("migration-image-first.schema.json", self.schema())
        save("migration-image-repeat.schema.json", self.schema())

    def test_contract_matches_selected_image_source_and_contiguous_migration_profile(self):
        selected = json.loads((ROOT / "deployments/local-control-plane.json").read_text())[
            "controlPlane"
        ]
        self.assertEqual(self.contract["image"], selected["image"])
        self.assertEqual(self.contract["sourceRevision"], selected["sourceRevision"])
        EVIDENCE.validate_schema(self.schema(), self.contract)

    def test_accepts_complete_fresh_attempts_and_identical_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.populate(directory)
            EVIDENCE.check(directory, self.contract)

    def test_schema_rejects_wrong_missing_duplicate_or_foreign_ledger(self):
        for change in ("digest", "missing", "duplicate", "owner"):
            with self.subTest(change=change):
                snapshot = self.schema()
                if change == "digest":
                    snapshot["ledger"][0]["sha256"] = "0" * 64
                elif change == "missing":
                    snapshot["ledger"].pop()
                elif change == "duplicate":
                    snapshot["ledger"][-1] = snapshot["ledger"][0]
                else:
                    snapshot["schemaOwner"] = "postgres"
                with self.assertRaises(AssertionError):
                    EVIDENCE.validate_schema(snapshot, self.contract)

    def test_rejects_schema_changes_wrong_image_credentials_or_api_authority(self):
        for change in ("oid", "timestamp", "image", "credential", "api-token", "worker"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                self.populate(directory)
                path = directory / (
                    "migration-image-repeat.schema.json"
                    if change in {"oid", "timestamp"}
                    else "migration-image-first.job.json"
                )
                value = json.loads(path.read_text())
                if change == "oid":
                    value["schemaOID"] = "999"
                elif change == "timestamp":
                    value["ledger"][0]["appliedAt"] = "2026-10-22T11:00:00Z"
                elif change == "image":
                    value["spec"]["template"]["spec"]["containers"][0]["image"] = (
                        "unreviewed:latest"
                    )
                elif change == "credential":
                    value["spec"]["template"]["spec"]["containers"][0]["env"][0]["valueFrom"][
                        "secretKeyRef"
                    ]["key"] = "runtime-url"
                elif change == "api-token":
                    value["spec"]["template"]["spec"]["automountServiceAccountToken"] = True
                else:
                    path = directory / "migration-image-first.worker.json"
                    value = [
                        {
                            "uid": "pod",
                            "containers": [
                                {
                                    "name": "migrate",
                                    "exitCode": None,
                                    "waitingReason": "ImagePullBackOff",
                                }
                            ],
                        }
                    ]
                path.write_text(json.dumps(value))
                with self.assertRaises(AssertionError):
                    EVIDENCE.check(directory, self.contract)

    def test_rejects_stale_replayed_or_detached_checkpoints(self):
        for change in ("time", "uid", "request", "output"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                self.populate(directory)
                path = directory / "migration-image-repeat.checkpoint.workflow.json"
                value = json.loads(path.read_text())
                if change == "time":
                    value["status"]["finishedAt"] = "2026-10-22T10:01:00Z"
                elif change == "uid":
                    value["metadata"]["uid"] = "checkpoint-uid-first"
                else:
                    checkpoint_path = directory / "migration-image-repeat.checkpoint.json"
                    checkpoint = json.loads(checkpoint_path.read_text())
                    checkpoint["requestDigest"] = "sha256:" + "e" * 64
                    checkpoint_path.write_text(json.dumps(checkpoint))
                    if change == "request":
                        value["status"]["nodes"]["checkpoint-repeat"]["outputs"]["parameters"][0][
                            "value"
                        ] = json.dumps(checkpoint)
                path.write_text(json.dumps(value))
                with self.assertRaises(AssertionError):
                    EVIDENCE.check(directory, self.contract)

    @unittest.skipUnless(BASH, "Bash unavailable")
    def test_permission_diagnostic_requires_the_attempt_role_database_and_application(self):
        assert BASH is not None
        function = re.search(
            r"^migration_permission_denied\(\) \{\n.*?^\}",
            (FIXTURE / "migration-image.sh").read_text(),
            re.M | re.S,
        )
        assert function is not None
        correct = "user=perfeng_runtime database=perfeng app=migration-ci-runtime-role ERROR: permission denied for database perfeng"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "postgres.log"
            for message, accepted in (
                (correct, True),
                (correct.replace("user=perfeng_runtime", "user=postgres"), False),
                (correct.replace("app=migration-ci-runtime-role", "app=psql"), False),
                (correct.replace("database=perfeng", "database=other"), False),
                ("Migration connection failed", False),
                (
                    correct.replace("permission denied for database perfeng", "connection refused"),
                    False,
                ),
            ):
                with self.subTest(message=message):
                    path.write_text(message + "\n")
                    result = subprocess.run(
                        [BASH, "-s", "--", path.as_posix()],
                        input=function[0] + '\nmigration_permission_denied "$1"\n',
                        text=True,
                        capture_output=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode == 0, accepted, result.stderr)


if __name__ == "__main__":
    unittest.main()
