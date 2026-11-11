"""Require complete exact-revision evidence before campaign admission."""

import hashlib
import json
import unittest
from typing import Any

from scripts.contracts_campaign import CONSUMERS, admit, command


class ContractCampaignTests(unittest.TestCase):
    def evidence(self):
        revisions = {name: "a" * 40 for name in CONSUMERS}
        receipts: dict[str, Any] = {
            name: {
                "revision": revisions[name],
                **{
                    stage: {
                        "namespace": "net:[different]",
                        "receipt": {"status": "passed"},
                    }
                    for stage in ("current", "rollback")
                },
            }
            for name in CONSUMERS
        }
        receipts["environment"]["current"]["receipt"]["status"] = "unchanged"
        receipts["analysis"]["current"]["receipt"]["status"] = "preview"
        for name in ("k6", "playwright", "control-plane", "console"):
            receipts[name]["current"]["receipt"]["status"] = "prepared"
        for name in ("control-plane", "console"):
            receipts[name]["rollback"]["receipt"]["status"] = "prepared"
        for stage in ("current", "rollback"):
            receipts["k8s"][stage]["receipt"]["prepared"] = True
        for row in receipts.values():
            for stage in ("current", "rollback"):
                raw = json.dumps(row[stage]["receipt"])
                row[stage]["rawReceipt"] = raw
                row[stage]["receiptSha256"] = hashlib.sha256(raw.encode()).hexdigest()
        return revisions, receipts

    def test_requires_every_current_and_rollback_receipt(self):
        revisions, rows = self.evidence()
        self.assertEqual(admit(revisions, rows, "net:[host]"), [])
        del rows["k8s"]["rollback"]
        self.assertTrue(admit(revisions, rows, "net:[host]"))

    def test_refuses_stale_failed_and_host_namespace_evidence(self):
        for tamper in ("revision", "failed", "namespace", "receipt", "hash", "unexpected-preview"):
            with self.subTest(tamper=tamper):
                revisions, rows = self.evidence()
                if tamper == "revision":
                    rows["analysis"]["revision"] = "b" * 40
                elif tamper == "failed":
                    rows["analysis"]["current"]["receipt"]["status"] = "failed"
                elif tamper == "namespace":
                    rows["analysis"]["current"]["namespace"] = "net:[host]"
                elif tamper == "unexpected-preview":
                    rows["analysis"]["rollback"]["receipt"]["status"] = "preview"
                elif tamper == "hash":
                    rows["analysis"]["current"]["receiptSha256"] = "0" * 64
                else:
                    del rows["analysis"]["current"]["receipt"]
                if tamper in ("failed", "unexpected-preview"):
                    stage = "rollback" if tamper == "unexpected-preview" else "current"
                    evidence = rows["analysis"][stage]
                    raw = json.dumps(evidence["receipt"])
                    evidence["rawReceipt"] = raw
                    evidence["receiptSha256"] = hashlib.sha256(raw.encode()).hexdigest()
                self.assertTrue(admit(revisions, rows, "net:[host]"))

    def test_rejects_receipt_with_a_different_embedded_consumer_revision(self):
        revisions, rows = self.evidence()
        rows["environment"]["rollback"]["receipt"]["consumerRevision"] = "b" * 40
        evidence = rows["environment"]["rollback"]
        raw = json.dumps(evidence["receipt"])
        evidence["rawReceipt"] = raw
        evidence["receiptSha256"] = hashlib.sha256(raw.encode()).hexdigest()
        self.assertTrue(admit(revisions, rows, "net:[host]"))

    def test_fixed_gate_rejects_missing_variables_and_make_expansions(self):
        with self.assertRaisesRegex(ValueError, "variables"):
            command("environment", "current", {})
        with self.assertRaisesRegex(ValueError, "unsafe"):
            command(
                "environment",
                "current",
                {
                    "CONTRACTS_UPDATE_ASSETS": "/tmp/$(shell forbidden)",
                    "CONTRACTS_VERIFIER": "/tmp/verifier",
                },
            )
