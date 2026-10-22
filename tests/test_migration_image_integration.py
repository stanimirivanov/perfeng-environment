"""Offline checks for selected-image migration identity, freshness and schema evidence."""

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
