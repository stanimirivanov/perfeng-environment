"""Replay the staged admission's actual native filters and exact-byte shell gate."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

import test_release_adoption as adoption
import yaml
from jsonschema import Draft202012Validator
from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"
JQ = shutil.which("jq")


class SchemaAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.parent = yaml.safe_load((DIRECTORY / "release-schema-migration-v2.yaml").read_text())
        self.templates = {t["name"]: t for t in self.parent["spec"]["templates"]}
        self.accepted = json.loads((ROOT / "tests/fixtures/release-request-v1.json").read_text())
        self.accepted["promotion"] = {
            "activation": "manual",
            "expectedCurrent": None,
            "migration": {"mode": "none"},
        }
        self.raw = json.dumps(self.accepted, sort_keys=True).encode() + b"\n"
        self.digest = "sha256:" + hashlib.sha256(self.raw).hexdigest()
        self.request = deepcopy(self.accepted)
        self.request["promotion"].update(
            expectedCurrent=self.digest, migration={"mode": "forward-only", "id": "0018"}
        )
        self.parameters = {
            "request-sha256": "sha256:"
            + hashlib.sha256(json.dumps(self.request).encode()).hexdigest(),
            "request-revision": "a" * 40,
        }
        name = "receipt." + self.digest[7:39] + "." + self.digest[39:]

        def metadata(name, purpose):
            return {
                "name": name,
                "namespace": "perfeng-release-ledger",
                "uid": name + "-uid",
                "resourceVersion": "10",
                "labels": {
                    "app.kubernetes.io/managed-by": "perfeng-environment",
                    "perfeng.io/purpose": purpose,
                },
            }

        self.pointer: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": metadata("perfeng-local-current", "current-release"),
            "data": {
                "requestDigest": self.digest,
                "receiptName": name,
                "receiptUID": name + "-uid",
                "clusterUID": self.request["environment"]["clusterUID"],
            },
        }
        image = json.loads((ROOT / "deployments/local-control-plane.json").read_text())[
            "controlPlane"
        ]["image"]
        self.record: dict[str, Any] = {
            "schemaVersion": 1,
            "kind": "PerfengReleaseAdoptionReceipt",
            "status": "adopted-baseline",
            "environment": self.accepted["environment"],
            "requestDigest": self.digest,
            "selectionRevision": self.accepted["selection"]["revision"],
            "rootUID": "root-uid",
            "controlPlaneApplicationUID": "child-uid",
            "deploymentRevision": json.loads(
                (ROOT / "deployments/local-control-plane.json").read_text()
            )["deployment"]["revision"],
            "image": image,
        }
        self.receipt: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": metadata(name, "release-adoption"),
            "immutable": True,
            "binaryData": {
                "request.json": base64.b64encode(self.raw).decode(),
                "receipt.json": base64.b64encode(json.dumps(self.record).encode()).decode(),
            },
        }
        self.receipt["metadata"]["annotations"] = {
            "perfeng.io/first-workflow-uid": "11111111-1111-1111-1111-111111111111",
            "perfeng.io/first-request-revision": "b" * 40,
        }
        self.checkpoint: dict[str, Any] = {
            "schemaVersion": 1,
            "kind": "PerfengMaintenanceCheckpoint",
            "status": "quiescence-observed",
            "environment": "perfeng-local",
            "requestDigest": self.parameters["request-sha256"],
            "requestRevision": self.parameters["request-revision"],
            "selectionRevision": self.request["selection"]["revision"],
            "clusterUID": self.request["environment"]["clusterUID"],
            "resources": [
                {"name": "perfeng-local-root", "uid": "root-uid"},
                {"name": "perfeng-local-control-plane", "uid": "child-uid"},
                {"name": "control-plane", "uid": "deployment-uid"},
            ],
        }

    def replay(self, template, value, **overrides):
        assert JQ
        values = {
            "request": self.request,
            "pointer": self.pointer,
            "receipt": self.receipt,
            "checkpoint": self.checkpoint,
            **overrides,
        }
        code = self.templates[template]["outputs"]["parameters"][0]["valueFrom"]["jqFilter"]
        # Argo toJson on a parameter string produces an escaped JSON string literal.
        code = re.sub(
            r"\{\{=toJson\(inputs.parameters.([a-z]+)\)\}\}",
            lambda m: json.dumps(json.dumps(values[m[1]])),
            code,
        )
        for key, text in self.parameters.items():
            code = code.replace("{{workflow.parameters." + key + "}}", text)
        result = subprocess.run(
            [JQ, "-ce", code],
            input=json.dumps(value),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
        )
        if result.returncode:
            raise ValueError(result.stderr)
        return json.loads(result.stdout)

    def test_order_and_unchanged_native_boundaries(self):
        old = yaml.safe_load((DIRECTORY / "release-schema-migration-v1.yaml").read_text())
        steps = self.templates["migrate"]["steps"]
        self.assertTrue(all(len(group) == 1 for group in steps))
        for group in steps:
            for key in ("when", "continueOn", "withItems", "withParam"):
                self.assertNotIn(key, group[0])
        self.assertEqual(
            [g[0]["name"] for g in steps],
            [
                "preflight",
                "task-selection",
                "migration-intent",
                "candidate",
                "pointer",
                "receipt",
                "accepted-bytes",
                "quiescence",
                "receipt-stable",
                "admission",
                "migrate",
            ],
        )
        for new_index, old_index in ((0, 0), (1, 1), (7, 3), (10, 4)):
            self.assertEqual(steps[new_index], old["spec"]["templates"][0]["steps"][old_index])
        self.assertEqual(
            self.parent["spec"]["serviceAccountName"], old["spec"]["serviceAccountName"]
        )
        self.assertEqual(self.parent["spec"]["synchronization"], old["spec"]["synchronization"])
        self.assertEqual(
            steps[2][0]["templateRef"],
            {"name": "perfeng-release-schema-migration-v1", "template": "migration-intent"},
        )
        for template in self.templates.values():
            for key in ("retryStrategy", "onExit", "hooks"):
                self.assertNotIn(key, template)
            if "resource" in template:
                self.assertEqual(template["resource"]["action"], "get")
                self.assertEqual(template["resource"]["flags"], ["--request-timeout=10s"])
                self.assertNotIn("default", template["outputs"]["parameters"][0]["valueFrom"])

    def test_current_digest_schema_and_container_isolation(self):
        candidate = self.templates["candidate"]
        script = candidate["container"]["args"][0]
        schema = json.loads(script.split("<<'SCHEMA'\n")[1].split("\nSCHEMA")[0])
        validator = Draft202012Validator(schema)
        validator.validate(self.request)
        for current in (None, "main", "sha256:" + "A" * 64, "sha256:" + "a" * 64 + "\n", 3):
            bad = deepcopy(self.request)
            bad["promotion"]["expectedCurrent"] = current
            self.assertFalse(validator.is_valid(bad))
        for name in ("candidate", "accepted-bytes"):
            template = self.templates[name]
            self.assertNotIn("script", template)
            self.assertEqual(
                template["container"]["securityContext"]["capabilities"], {"drop": ["ALL"]}
            )
            self.assertTrue(template["container"]["securityContext"]["readOnlyRootFilesystem"])
            self.assertIn("@sha256:", template["container"]["image"])

    def test_ledger_authority_is_get_only_and_explicitly_staged(self):
        path = ROOT / "gitops/release-workflows-bootstrap/schema-migration-ledger-access.yaml"
        role, binding = list(yaml.safe_load_all(path.read_text()))
        self.assertEqual(role["metadata"]["namespace"], "perfeng-release-ledger")
        self.assertEqual(
            role["rules"], [{"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get"]}]
        )
        self.assertEqual(
            binding["subjects"],
            [
                {
                    "kind": "ServiceAccount",
                    "name": "release-schema-migration",
                    "namespace": "argo-workflows",
                }
            ],
        )
        staged = yaml.safe_load((DIRECTORY / "kustomization.yaml").read_text())
        self.assertIn("release-schema-migration-v2.yaml", staged["resources"])
        active = (ROOT / "gitops/environments/local/kustomization.yaml").read_text()
        self.assertNotIn("release-workflows", active)
        bootstrap = (ROOT / "gitops/release-workflows-bootstrap/kustomization.yaml").read_text()
        self.assertNotIn(path.name, bootstrap)

    def test_valid_records_bytes_and_fresh_identity_are_accepted(self):
        self.assertEqual(self.replay("pointer", self.pointer), self.pointer)
        self.assertEqual(self.replay("receipt", self.receipt), self.receipt)
        newer = deepcopy(self.receipt)
        newer["metadata"]["resourceVersion"] = "11"
        self.assertEqual(self.replay("receipt-stable", newer), newer)
        self.assertEqual(
            self.replay("admission", self.pointer), {"status": "retained-baseline-verified"}
        )

    def test_receipts_from_unchanged_adoption_policy_are_compatible(self):
        fixture = adoption.ReleaseAdoptionTests()
        fixture.setUp()
        self.receipt, self.pointer = fixture.records()
        self.request = deepcopy(fixture.request)
        self.request["promotion"]["expectedCurrent"] = self.pointer["data"]["requestDigest"]
        self.checkpoint["resources"][1]["uid"] = fixture.readiness["applicationUID"]
        self.assertEqual(self.replay("pointer", self.pointer), self.pointer)
        self.assertEqual(self.replay("receipt", self.receipt), self.receipt)
        self.assertEqual(
            self.replay("admission", self.pointer), {"status": "retained-baseline-verified"}
        )

    def test_pointer_refuses_absence_conflict_and_foreign_metadata(self):
        cases = [None]
        for key, value in (
            ("requestDigest", "sha256:" + "0" * 64),
            ("clusterUID", "foreign"),
            ("receiptName", "foreign"),
            ("receiptUID", ""),
        ):
            bad = deepcopy(self.pointer)
            bad["data"][key] = value
            cases.append(bad)
        for key, value in (
            ("uid", ""),
            ("namespace", "foreign"),
            ("labels", {}),
            ("ownerReferences", [{}]),
            ("deletionTimestamp", "now"),
            ("finalizers", ["hold"]),
        ):
            bad = deepcopy(self.pointer)
            bad["metadata"][key] = value
            cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.replay("pointer", bad)

    def test_receipt_refuses_replacement_provenance_content_and_wrong_selection(self):
        cases = [None]
        for field, value in (("immutable", False), ("data", {"other": "data"})):
            bad = deepcopy(self.receipt)
            bad[field] = value
            cases.append(bad)
        for field, value in (
            ("uid", "replacement"),
            ("labels", {}),
            ("annotations", {}),
            ("deletionTimestamp", "now"),
        ):
            bad = deepcopy(self.receipt)
            bad["metadata"][field] = value
            cases.append(bad)
        for field in (
            "image",
            "deploymentRevision",
            "selectionRevision",
            "requestDigest",
            "status",
            "kind",
        ):
            record = {**self.record, field: "wrong"}
            bad = deepcopy(self.receipt)
            bad["binaryData"]["receipt.json"] = base64.b64encode(
                json.dumps(record).encode()
            ).decode()
            cases.append(bad)
        bad = deepcopy(self.receipt)
        accepted = deepcopy(self.accepted)
        accepted["selection"]["revision"] = "f" * 40
        bad["binaryData"]["request.json"] = base64.b64encode(json.dumps(accepted).encode()).decode()
        cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.replay("receipt", bad)

    def test_final_gate_refuses_changed_records_and_checkpoint_identities(self):
        for field in ("uid", "annotations"):
            changed = deepcopy(self.receipt)
            changed["metadata"][field] = "replaced" if field == "uid" else {}
            with self.assertRaises(ValueError):
                self.replay("receipt-stable", changed)
        changed = deepcopy(self.pointer)
        changed["metadata"]["uid"] = "replaced"
        with self.assertRaises(ValueError):
            self.replay("admission", changed)
        for field in (
            "requestDigest",
            "requestRevision",
            "clusterUID",
            "selectionRevision",
            "status",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.replay(
                    "admission", self.pointer, checkpoint={**self.checkpoint, field: "wrong"}
                )
        for index in (0, 1):
            checkpoint = deepcopy(self.checkpoint)
            checkpoint["resources"][index]["uid"] = "replacement"
            with self.assertRaises(ValueError):
                self.replay("admission", self.pointer, checkpoint=checkpoint)

    @unittest.skipUnless(BASH, "Bash required")
    def test_candidate_hash_and_validator_refusal_never_publish_request(self):
        assert BASH
        script = self.templates["candidate"]["container"]["args"][0]
        script = script.replace("/tmp/current.schema.json", '"$SCHEMA"')
        script = script.replace("/tmp/request.json", '"$COPIED"')
        script = script.replace("/usr/local/bin/jsonschema", '"$VALIDATOR"')
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            raw = json.dumps(self.request).encode()
            source = directory / "candidate.json"
            source.write_bytes(raw)
            validator = directory / "validator.sh"
            validator.write_text(
                '#!/bin/sh\nprintf checked > "$MARKER"\nexit "$VALIDATOR_EXIT"\n', newline="\n"
            )
            validator.chmod(0o700)
            for label, digest, status in (
                ("valid", hashlib.sha256(raw).hexdigest(), 0),
                ("wrong-bytes", "0" * 64, 0),
                ("invalid-current", hashlib.sha256(raw).hexdigest(), 23),
            ):
                copied, marker = directory / (label + ".json"), directory / (label + ".marker")
                result = subprocess.run(
                    [BASH, "-ec", script],
                    env={
                        **os.environ,
                        "BASH_ENV": "",
                        "REQUEST_ROOT": directory.as_posix(),
                        "REQUEST_PATH": source.name,
                        "REQUEST_SHA256": "sha256:" + digest,
                        "SCHEMA": (directory / "schema.json").as_posix(),
                        "COPIED": copied.as_posix(),
                        "VALIDATOR": validator.as_posix(),
                        "VALIDATOR_EXIT": str(status),
                        "MARKER": marker.as_posix(),
                    },
                    capture_output=True,
                    timeout=15,
                )
                with self.subTest(label=label):
                    self.assertEqual(result.returncode, 1 if label == "wrong-bytes" else status)
                    self.assertEqual(marker.exists(), label != "wrong-bytes")
                    self.assertEqual(copied.exists(), label == "valid")
                    if label == "valid":
                        self.assertEqual(copied.read_bytes(), raw)

    @unittest.skipUnless(BASH, "Bash required")
    def test_actual_byte_verifier_accepts_only_exact_committed_bytes(self):
        assert BASH
        script = self.templates["accepted-bytes"]["container"]["args"][0].replace(
            "/tmp/accepted.json", '"$OUTPUT"'
        )
        with tempfile.TemporaryDirectory() as temporary:
            for raw in (self.raw, self.raw.rstrip(b"\n"), b"not JSON", self.raw + b" "):
                with self.subTest(raw=raw):
                    result = subprocess.run(
                        [BASH, "-ec", script],
                        env={
                            **os.environ,
                            "BASH_ENV": "",
                            "OUTPUT": (Path(temporary) / "accepted.json").as_posix(),
                            "ACCEPTED_B64": base64.b64encode(raw).decode(),
                            "EXPECTED_CURRENT": self.digest,
                        },
                        capture_output=True,
                        timeout=15,
                    )
                    self.assertEqual(result.returncode, 0 if raw == self.raw else 1, result.stderr)
                    self.assertEqual(result.stdout, b"")
                    if raw != self.raw:
                        self.assertEqual(
                            result.stderr,
                            b"[RELEASE_ADMISSION] Accepted request byte verification failed\n",
                        )


if __name__ == "__main__":
    unittest.main()
