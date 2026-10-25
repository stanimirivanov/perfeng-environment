"""Adoption ledger planning is pure; native creates never replace conflicting records."""

import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator

from tests.release_verifier_support import policy, runtime

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"
planner = runtime


class ReleaseAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = json.loads((ROOT / "tests/fixtures/release-request-v1.json").read_text())
        self.raw = json.dumps(self.request, sort_keys=True).encode() + b"\n"
        lock = json.loads((ROOT / "deployments/local-control-plane.json").read_text())
        self.readiness = {
            "schemaVersion": 1,
            "kind": "PerfengReleaseReadiness",
            "scope": "control-plane-foundation",
            "status": "readiness-verified",
            "requestDigest": "sha256:" + hashlib.sha256(self.raw).hexdigest(),
            "clusterUID": self.request["environment"]["clusterUID"],
            "selectionRevision": self.request["selection"]["revision"],
            "applicationUID": "control-plane-application-uid",
            "deploymentRevision": lock["deployment"]["revision"],
            "image": lock["controlPlane"]["image"],
        }
        self.root = yaml.safe_load(
            (ROOT / "gitops/bootstrap/local-root-application.yaml").read_text()
        )
        self.root["metadata"].update(uid="root-uid", resourceVersion="10")
        self.root["status"] = {
            "sync": {
                "status": "Synced",
                "revision": self.request["selection"]["revision"],
                "comparedTo": {"source": deepcopy(self.root["spec"]["source"])},
            },
            "health": {"status": "Healthy"},
            "operationState": {"phase": "Succeeded"},
        }

    def plan(
        self,
        receipt: dict[str, Any] | None = None,
        pointer: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return policy.plan_adoption(self.raw, self.readiness, self.root, receipt, pointer, after)

    def test_normal_root_refresh_preserves_each_ledger_decision(self) -> None:
        self.root["metadata"]["generation"] = 10
        self.root["status"]["reconciledAt"] = "2026-10-25T20:14:13Z"
        receipt, pointer = self.records()
        after = deepcopy(self.root)
        after["metadata"].update(generation=11, resourceVersion="9814")
        after["status"]["reconciledAt"] = "2026-10-25T20:17:35Z"
        originals = deepcopy((self.root, after))
        for r, p in ((None, None), (receipt, None), (receipt, pointer)):
            with self.subTest(receipt=r is not None, pointer=p is not None):
                self.assertEqual(self.plan(r, p, after), self.plan(r, p, self.root))
        self.assertEqual((self.root, after), originals)

    def records(self) -> tuple[dict[str, Any], dict[str, Any]]:
        value = self.plan()
        receipt = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": value["name"],
                "namespace": policy.NAMESPACE,
                "uid": "receipt-uid",
                "resourceVersion": "20",
                "labels": policy.labels("release-adoption"),
                "annotations": {
                    "perfeng.io/first-workflow-uid": "00000000-0000-0000-0000-000000000001",
                    "perfeng.io/first-request-revision": "a" * 40,
                },
            },
            "immutable": True,
            "binaryData": {
                "request.json": value["request-b64"],
                "receipt.json": value["receipt-b64"],
            },
        }
        pointer = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": policy.POINTER,
                "namespace": policy.NAMESPACE,
                "uid": "pointer-uid",
                "resourceVersion": "30",
                "labels": policy.labels("current-release"),
            },
            "data": {
                "requestDigest": value["request-digest"],
                "receiptName": value["name"],
                "receiptUID": "receipt-uid",
                "clusterUID": value["cluster-uid"],
            },
        }
        return receipt, pointer

    def test_initial_adoption_partial_recovery_and_exact_retry_are_content_bound(self) -> None:
        value = self.plan()
        self.assertEqual(value["create-receipt"], "true")
        self.assertEqual(base64.b64decode(value["request-b64"]), self.raw)
        self.assertRegex(value["name"], r"^receipt\.[0-9a-f]{32}\.[0-9a-f]{32}$")
        receipt, pointer = self.records()
        partial = self.plan(receipt)
        self.assertEqual((partial["create-receipt"], partial["create-pointer"]), ("false", "true"))
        complete = self.plan(receipt, pointer, deepcopy(self.root))
        self.assertEqual(
            (complete["create-receipt"], complete["create-pointer"]), ("false", "false")
        )
        self.assertEqual(complete["result"]["pointerUID"], "pointer-uid")
        self.assertEqual(receipt, self.records()[0])
        schema = json.loads((ROOT / "deployments/release-adoption-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(json.loads(base64.b64decode(value["receipt-b64"])))
        Draft202012Validator(schema["$defs"]["pointer"]).validate(pointer["data"])
        validator = Draft202012Validator(schema["$defs"]["result"])
        validator.validate(complete["result"])
        self.assertFalse(validator.is_valid({**complete["result"], "promoted": True}))

    def test_ci_recovery_reports_require_the_expected_production_policy_decision(self) -> None:
        source = (ROOT / "tests/integration/release-workflows/recovery.sh").read_text()
        code = re.findall(r"<<'PY'\n(.*?)\nPY", source, re.S)[0]
        receipt, pointer = self.records()
        records = {"root": self.root, "receipt": receipt, "pointer": pointer}
        payload = base64.b64decode(receipt["binaryData"]["receipt.json"])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "ledger-request.json").write_bytes(self.raw)
            (directory / "ledger-receipt.json").write_bytes(payload)
            for case in (
                "partial",
                "complete",
                "retained",
                "changed-root",
                "dangling",
                "replaced",
                "unexpected",
            ):
                observed, after = deepcopy(records), deepcopy(records)
                if case == "partial":
                    observed["pointer"] = None
                elif case == "changed-root":
                    after["root"]["status"]["health"]["status"] = "Degraded"
                elif case == "dangling":
                    observed["receipt"] = None
                elif case == "replaced":
                    observed["receipt"]["metadata"]["uid"] = "recreated-uid"
                elif case == "unexpected":
                    observed["receipt"]["metadata"]["labels"] = {}
                path, fresh = directory / "observed.json", directory / "fresh.json"
                path.write_text(json.dumps(observed))
                fresh.write_text(json.dumps(after if case == "changed-root" else observed))
                result = subprocess.run(
                    [
                        sys.executable,
                        "-",
                        case if case != "unexpected" else "dangling",
                        str(path),
                        temporary,
                        str(fresh),
                    ],
                    input=code,
                    text=True,
                    capture_output=True,
                    cwd=ROOT,
                    timeout=20,
                )
                with self.subTest(case=case):
                    if case == "unexpected":
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, "")
                        continue
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertNotIn("result", report)
                    if case in {"partial", "complete", "retained"}:
                        self.assertEqual(report["createReceipt"], False)
                        self.assertEqual(report["createPointer"], case == "partial")
                    else:
                        self.assertEqual(report["decision"], "refused")

    def test_readiness_request_and_cluster_binding_refuse_before_any_creation(self) -> None:
        for key, value in (
            ("requestDigest", "sha256:" + "0" * 64),
            ("clusterUID", "foreign"),
            ("selectionRevision", "b" * 40),
            ("status", "unknown"),
            ("scope", "platform"),
        ):
            original = self.readiness[key]
            self.readiness[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.plan()
            self.readiness[key] = original
        self.request["promotion"]["expectedCurrent"] = "sha256:" + "c" * 64
        raw = json.dumps(self.request).encode()
        with self.assertRaises(ValueError):
            policy.plan_adoption(raw, self.readiness, self.root, None, None)
        with self.assertRaises(ValueError):
            policy.plan_adoption(self.raw + b"\n", self.readiness, self.root, None, None)
        with self.assertRaises(ValueError):
            policy.plan_adoption(b"x" * 65537, self.readiness, self.root, None, None)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            json.loads('{"name":1,"name":2}', object_pairs_hook=policy.unique)

    def test_foreign_stale_unhealthy_or_running_root_is_refused(self) -> None:
        for change in (
            lambda obj: obj["metadata"].update(ownerReferences=[{"kind": "ApplicationSet"}]),
            lambda obj: obj["metadata"].update(deletionTimestamp="now"),
            lambda obj: obj["metadata"]["labels"].update(
                {"app.kubernetes.io/managed-by": "foreign"}
            ),
            lambda obj: obj["spec"]["source"].update(targetRevision="branch"),
            lambda obj: obj.update(operation={"sync": {}}),
            lambda obj: obj["status"]["operationState"].update(phase="Running"),
            lambda obj: obj["status"]["sync"].update(revision="c" * 40),
            lambda obj: obj["status"]["sync"].update(status="OutOfSync"),
            lambda obj: obj["status"].update(conditions=[{"type": "ComparisonError"}]),
            lambda obj: obj["status"]["health"].update(status="Degraded"),
        ):
            original = deepcopy(self.root)
            change(self.root)
            with self.assertRaises(ValueError):
                self.plan()
            self.root = original
        after = deepcopy(self.root)
        after["metadata"].setdefault("annotations", {})["ci.example/change"] = "changed"
        with self.assertRaisesRegex(ValueError, "changed during"):
            self.plan(after=after)

    def test_refresh_does_not_hide_other_root_changes_or_invalid_fresh_samples(self) -> None:
        changes = (
            lambda obj: obj["metadata"].update(uid="recreated"),
            lambda obj: obj["metadata"].update(resourceVersion=""),
            lambda obj: obj["metadata"].update(deletionTimestamp="now"),
            lambda obj: obj["metadata"].update(annotations={"unexpected": "annotation"}),
            lambda obj: obj["spec"]["syncPolicy"].update(retry={"limit": 2}),
            lambda obj: obj.update(operation={"sync": {}}),
            lambda obj: obj["status"]["health"].update(status="Degraded"),
            lambda obj: obj["status"]["sync"].update(status="OutOfSync"),
            lambda obj: obj["status"]["sync"].update(revision="c" * 40),
            lambda obj: obj["status"]["sync"]["comparedTo"].update(source={}),
            lambda obj: obj["status"].update(conditions=[{"type": "ComparisonError"}]),
            lambda obj: obj["status"]["operationState"].update(phase="Running"),
            lambda obj: obj["status"].update(resources=[{"kind": "Secret"}]),
            lambda obj: obj["status"].update(extra="unknown"),
            lambda obj: obj.update(extra="unknown"),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                after = deepcopy(self.root)
                after["metadata"].update(generation=11, resourceVersion="9814")
                after["status"]["reconciledAt"] = "2026-10-25T20:17:35Z"
                change(after)
                for first, last in ((self.root, after), (after, self.root)):
                    with self.assertRaises(ValueError):
                        policy.plan_adoption(self.raw, self.readiness, first, None, None, last)

    def test_receipt_ownership_content_mutability_and_recreation_conflicts_refuse(self) -> None:
        receipt, pointer = self.records()
        for change in (
            lambda obj: obj.update(immutable=False),
            lambda obj: obj["metadata"].pop("annotations"),
            lambda obj: obj.update(data={"unexpected": "data"}),
            lambda obj: obj["binaryData"].update({"request.json": "changed"}),
            lambda obj: obj["metadata"].update(namespace="argo-workflows"),
            lambda obj: obj["metadata"].update(ownerReferences=[{"kind": "Workflow"}]),
            lambda obj: obj["metadata"].update(finalizers=["foreign"]),
            lambda obj: obj["metadata"].update(
                annotations={"argocd.argoproj.io/tracking-id": "foreign"}
            ),
        ):
            changed = deepcopy(receipt)
            change(changed)
            with self.assertRaises(ValueError):
                self.plan(changed, pointer)
        receipt["metadata"]["uid"] = "recreated"
        with self.assertRaisesRegex(ValueError, "pointer conflicts"):
            self.plan(receipt, pointer)

    def test_existing_pointer_conflict_and_dangling_reference_are_not_initial_state(self) -> None:
        receipt, pointer = self.records()
        with self.assertRaises(ValueError):
            self.plan(None, pointer)
        for key in ("requestDigest", "receiptName", "receiptUID", "clusterUID"):
            changed = deepcopy(pointer)
            changed["data"][key] = "foreign"
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.plan(receipt, changed)
        pointer["immutable"] = True
        with self.assertRaises(ValueError):
            self.plan(receipt, pointer)

    def test_driver_never_publishes_success_for_planned_or_incomplete_state(self) -> None:
        receipt, pointer = self.records()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "request"
            (root / ".git").mkdir(parents=True)
            (root / ".git/HEAD").write_text("a" * 40 + "\n")
            path = "deployments/release-requests/current.json"
            (root / path).parent.mkdir(parents=True)
            (root / path).write_bytes(self.raw)
            environment = {
                "REQUEST_REVISION": "a" * 40,
                "REQUEST_PATH": path,
                "REQUEST_DIGEST": self.readiness["requestDigest"],
                "READINESS": json.dumps(self.readiness),
                "ROOT": json.dumps(self.root),
                "ROOT_AFTER": "null",
                "RECEIPT": "null",
                "POINTER": "null",
                "PHASE": "plan",
            }

            def location(value: str) -> Path:
                return root if value == "/inputs/request" else Path(temporary) / Path(value).name

            with (
                patch.dict(os.environ, environment),
                patch.object(planner, "Path", side_effect=location),
            ):
                planner.run_adoption()
                result = Path(temporary) / "result"
                self.assertIsNone(json.loads(result.read_text()))
                with patch.dict(os.environ, {"PHASE": "finish"}):
                    with self.assertRaises(ValueError):
                        planner.run_adoption()
                self.assertIsNone(json.loads(result.read_text()))
                with patch.dict(
                    os.environ,
                    {
                        "PHASE": "finish",
                        "RECEIPT": json.dumps(receipt),
                        "POINTER": json.dumps(pointer),
                        "ROOT_AFTER": json.dumps(self.root),
                    },
                ):
                    planner.run_adoption()
                self.assertEqual(json.loads(result.read_text())["status"], "adopted-baseline")
        with (
            patch.object(planner, "run_adoption", side_effect=ValueError("sensitive-vendor-input")),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertEqual(planner.cli("adoption"), 2)
        self.assertNotIn("sensitive-vendor-input", stderr.getvalue())

    def test_native_operations_are_create_only_isolated_and_not_workflow_owned(self) -> None:
        operations = yaml.safe_load((DIRECTORY / "adoption-records.yaml").read_text())
        for template in operations["spec"]["templates"]:
            resource = template["resource"]
            self.assertIn(resource["action"], {"get", "create"})
            if resource["action"] == "create":
                obj = yaml.safe_load(resource["manifest"])
                self.assertEqual(obj["kind"], "ConfigMap")
                self.assertEqual(obj["metadata"]["namespace"], policy.NAMESPACE)
                self.assertIs(resource["setOwnerReference"], False)
                self.assertNotIn("ownerReferences", obj["metadata"])
        role = next(
            obj
            for obj in yaml.safe_load_all(
                (ROOT / "gitops/release-workflows-bootstrap/ledger-access.yaml").read_text()
            )
            if obj["kind"] == "Role"
        )
        self.assertEqual(
            role["rules"],
            [{"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "create"]}],
        )
        self.assertEqual(role["metadata"]["namespace"], policy.NAMESPACE)
        parent = yaml.safe_load((DIRECTORY / "release-adopt-existing-v3.yaml").read_text())
        steps = [group[0] for group in parent["spec"]["templates"][0]["steps"]]
        self.assertEqual(steps[0]["templateRef"]["name"], "perfeng-release-readiness-v3")
        self.assertEqual(steps[-1]["name"], "finish")
        self.assertEqual(
            [s["name"] for s in steps[-4:]],
            ["stored-pointer", "final-receipt", "final-root", "finish"],
        )
        for name in ("create-receipt", "create-pointer"):
            self.assertIn(" == true", next(s for s in steps if s["name"] == name)["when"])
        self.assertEqual(
            parent["spec"]["synchronization"]["mutexes"], [{"name": "perfeng-local-external-state"}]
        )


if __name__ == "__main__":
    unittest.main()
