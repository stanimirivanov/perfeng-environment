"""Complete parents must refuse receipt loss/replacement without repairing the pointer."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_adoption_parent_integration import FIXTURE, READINESS, evidence
from test_adoption_parent_integration import MODULE as ADOPTION

SPEC = importlib.util.spec_from_file_location(
    "adoption_identity_evidence", FIXTURE / "adoption_identity_evidence.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(
    sys.modules, {"adoption_parent_evidence": ADOPTION, "readiness_parent_evidence": READINESS}
):
    SPEC.loader.exec_module(MODULE)

REASON = "current pointer conflicts or references a missing/replaced receipt"


def fault(case):
    args = list(evidence(False)[:6])
    workflow, _, _, _, _, api = args
    baseline = sorted(
        copy.deepcopy([api["receipt"], api["pointer"]]), key=lambda o: o["metadata"]["name"]
    )
    workflow["status"]["phase"] = "Failed"
    nodes = workflow["status"]["nodes"]
    nodes["adoption"].update(phase="Failed", outputs={})
    for name in (
        "create-receipt",
        "stored-receipt",
        "root-after",
        "pointer-plan",
        "create-pointer",
        "stored-pointer",
        "final-receipt",
        "final-root",
        "finish",
    ):
        del nodes[name]
    nodes["plan"].update(phase="Failed", outputs={})
    if case == "missing-receipt":
        api["receipt"] = None
    else:
        api["receipt"]["metadata"].update(uid="replacement-uid", resourceVersion="50")
    nodes["existing-receipt"]["outputs"]["parameters"][0]["value"] = json.dumps(api["receipt"])
    return [case, *args, [{"nodeID": "plan", "exitCode": 2}], REASON, baseline]


class AdoptionIdentityRefusalTests(unittest.TestCase):
    def test_missing_and_replaced_refusals_require_fresh_evidence_and_unchanged_pointer(self):
        for case in ("missing-receipt", "replaced-receipt"):
            with self.subTest(case=case):
                args = fault(case)
                report = MODULE.refusal(*args)
                self.assertEqual(report["decision"], case + "-refused")
                self.assertEqual(report["originalReceiptUID"], "receipt-uid")
                self.assertEqual(report["pointerUID"], "pointer-uid")
                self.assertEqual(
                    report["observedReceiptUID"],
                    None if case == "missing-receipt" else "replacement-uid",
                )
                self.assertNotIn("status", report)
                for change in (
                    "readiness",
                    "worker",
                    "exit",
                    "log",
                    "pointer",
                    "early-result",
                    "later-write",
                    "root",
                    "receipt",
                ):
                    values = copy.deepcopy(args)
                    nodes = values[1]["status"]["nodes"]
                    if change == "readiness":
                        nodes["readiness"]["phase"] = "Failed"
                    elif change == "worker":
                        values[7][0]["nodeID"] = "root"
                    elif change == "exit":
                        values[7][0]["exitCode"] = 1
                    elif change == "log":
                        values[8] = "root is stale"
                    elif change == "pointer":
                        values[6]["pointer"]["data"]["receiptUID"] = "changed"
                    elif change == "early-result":
                        nodes["adoption"]["outputs"] = {
                            "parameters": [{"name": "result", "value": "{}"}]
                        }
                    elif change == "later-write":
                        nodes["create-pointer"] = {"displayName": "create-pointer", "type": "Pod"}
                    elif change == "root":
                        root = ADOPTION.record(values[1], "root")
                        root["status"]["sync"]["status"] = "OutOfSync"
                        nodes["root"]["outputs"]["parameters"][0]["value"] = json.dumps(root)
                    else:
                        receipt = copy.deepcopy(values[9][1])
                        if case == "replaced-receipt":
                            receipt["metadata"].update(uid="replacement-uid")
                            receipt["metadata"]["annotations"]["perfeng.io/first-workflow-uid"] = (
                                "00000000-0000-0000-0000-000000000099"
                            )
                        values[6]["receipt"] = receipt
                        nodes["existing-receipt"]["outputs"]["parameters"][0]["value"] = json.dumps(
                            receipt
                        )
                    with (
                        self.subTest(case=case, change=change),
                        self.assertRaises((AssertionError, KeyError, ValueError)),
                    ):
                        MODULE.refusal(*values)

    def test_prepare_checks_baseline_and_builds_conditional_delete_and_original_content(self):
        args = fault("replaced-receipt")
        baseline, workflow, raw = args[9], args[1], args[4]
        health = READINESS.outputs(READINESS.named(workflow, "readiness", "Steps"))["result"]
        root = ADOPTION.record(workflow, "root")
        delete, replacement = MODULE.prepare(baseline, copy.deepcopy(baseline), raw, health, root)
        self.assertEqual(
            delete,
            {
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "preconditions": {"uid": "receipt-uid", "resourceVersion": "20"},
            },
        )
        self.assertEqual(replacement, MODULE.recreate(baseline[1]))
        self.assertEqual(
            set(replacement["metadata"]), {"name", "namespace", "labels", "annotations"}
        )
        for field, value in (
            ("metadata.namespace", "foreign"),
            ("metadata.uid", "replaced"),
            ("metadata.resourceVersion", ""),
            ("metadata.finalizers", ["foreign"]),
            ("immutable", False),
            ("binaryData.request.json", "e30="),
        ):
            values = copy.deepcopy(baseline)
            target = values[1]
            parts = field.split(".", 1)
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
            with self.subTest(field=field), self.assertRaises((AssertionError, ValueError)):
                MODULE.prepare(values, copy.deepcopy(values), raw, health, root)
        changed = copy.deepcopy(baseline)
        changed[1]["metadata"]["resourceVersion"] = "21"
        with self.assertRaises(AssertionError):
            MODULE.prepare(baseline, changed, raw, health, root)

    def test_cli_prepares_private_files_and_reports_refusals_without_adoption_result(self):
        for case in ("missing-receipt", "replaced-receipt"):
            args = fault(case)
            _, workflow, parameters, preflight, raw, _, api, workers, log, baseline = args
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary)
                for name, value in (
                    ("parent-parameters.json", parameters),
                    ("parent-first.result.json", preflight),
                    ("adoption-identity-baseline.json", baseline),
                    ("adoption-identity-current.json", baseline),
                    ("adoption-parent-api.json", api),
                    (f"adoption-parent-{case}.workflow.json", workflow),
                    (f"adoption-parent-{case}.workers.json", workers),
                ):
                    (path / name).write_text(json.dumps(value), encoding="utf-8")
                (path / "parent-request.json").write_bytes(raw)
                (path / "adoption-parent-worker.log").write_text(log, encoding="utf-8")
                for mode in ("prepare", case):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(FIXTURE / "adoption_identity_evidence.py"),
                            mode,
                            temporary,
                            temporary,
                            case,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=20,
                        env={**os.environ, "PYTHONPATH": str(FIXTURE.parents[2])},
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((path / "adoption-receipt-delete.json").is_file())
                self.assertTrue((path / "adoption-receipt-recreate.json").is_file())
                self.assertEqual(
                    json.loads((path / f"adoption-parent-{case}.outcome.json").read_text()),
                    MODULE.refusal(*args),
                )
                self.assertFalse((path / f"adoption-parent-{case}.result.json").exists())

    def test_destructive_faults_follow_retention_and_use_only_guarded_receipt_delete(self):
        source = (FIXTURE / "adoption-parent.sh").read_text()
        self.assertLess(source.index("adoption_retained retained"), source.index("k delete --raw"))
        self.assertIn(
            '"/api/v1/namespaces/perfeng-release-ledger/configmaps/$adoption_receipt"', source
        )
        self.assertIn('-f "$fixture/adoption-receipt-delete.json"', source)
        self.assertLess(
            source.index("adoption_case missing-receipt"),
            source.index('k create -f "$fixture/adoption-receipt-recreate.json"'),
        )
        self.assertIn('cmp "$fixture/adoption-replaced-before.records.json"', source)
        self.assertNotIn("patch configmap ", source)
