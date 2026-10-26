"""Changed retry identity and upgrade intent must not cross the adoption boundary."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_adoption_identity_refusal import MODULE, REFUSAL
from test_adoption_parent_integration import FIXTURE, READINESS, evidence


def request_fault(case):
    workflow, parameters, preflight, original, raw_lock, api, _ = evidence(False)
    baseline = sorted(
        copy.deepcopy([api["receipt"], api["pointer"]]), key=lambda o: o["metadata"]["name"]
    )
    request = json.loads(original)
    if case == "changed-request":
        raw = original + b"\n"
    else:
        request["promotion"]["expectedCurrent"] = "sha256:" + hashlib.sha256(original).hexdigest()
        raw = (json.dumps(request, indent=2) + "\n").encode()
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    parameters["request-sha256"] = preflight["requestDigest"] = digest
    parameters["request-path"] = preflight["requestPath"] = (
        f"deployments/release-requests/ci-{case}-v1.json"
    )
    workflow["spec"]["arguments"]["parameters"] = [
        {"name": k, "value": v} for k, v in parameters.items()
    ]
    nodes = workflow["status"]["nodes"]
    for name in ("preflight", "readiness", "verify"):
        value = READINESS.outputs(nodes[name])["result"]
        value["requestDigest"] = digest
        if name == "preflight":
            value["requestPath"] = parameters["request-path"]
        nodes[name]["outputs"]["parameters"][0]["value"] = json.dumps(value)
    workflow["status"]["phase"] = "Failed"
    nodes["adoption"].update(phase="Failed", outputs={})
    nodes["plan"].update(phase="Failed", outputs={})
    nodes["existing-receipt"]["outputs"]["parameters"][0]["value"] = "null"
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
    api["requestedReceipt"] = None
    return [
        case,
        workflow,
        parameters,
        raw,
        original,
        raw_lock,
        api,
        [{"nodeID": "plan", "exitCode": 2}],
        REFUSAL,
        baseline,
    ]


class AdoptionRequestRefusalTests(unittest.TestCase):
    def test_exact_fixture_relations_and_fresh_readiness_are_required(self):
        for case in ("changed-request", "upgrade-request"):
            args = request_fault(case)
            report = MODULE.request_refusal(*args)
            self.assertEqual(report["decision"], case + "-refused")
            self.assertNotEqual(report["requestDigest"], report["acceptedDigest"])
            for change in (
                "bytes",
                "environment",
                "path",
                "digest",
                "readiness",
                "pointer",
                "receipt",
                "candidate",
                "log",
                "worker",
                "exit",
                "later",
                "result",
            ):
                values = copy.deepcopy(args)
                nodes = values[1]["status"]["nodes"]
                if change == "environment":
                    value = READINESS.outputs(nodes["preflight"])["result"]
                    value["environment"] = "foreign"
                    nodes["preflight"]["outputs"]["parameters"][0]["value"] = json.dumps(value)
                elif change == "bytes":
                    values[3] = values[4]
                elif change == "path":
                    values[2]["request-path"] = "wrong.json"
                    values[1]["spec"]["arguments"]["parameters"] = [
                        {"name": k, "value": v} for k, v in values[2].items()
                    ]
                elif change == "digest":
                    values[2]["request-sha256"] = "sha256:" + "f" * 64
                elif change == "readiness":
                    nodes["readiness"]["phase"] = "Failed"
                elif change == "pointer":
                    values[6]["pointer"]["metadata"]["uid"] = "replaced"
                elif change == "receipt":
                    values[6]["receipt"]["metadata"]["uid"] = "replaced"
                elif change == "candidate":
                    values[6]["requestedReceipt"] = {"metadata": {"uid": "created"}}
                elif change == "log":
                    values[8] = "connection refused"
                elif change == "worker":
                    values[7][0]["nodeID"] = "preflight"
                elif change == "exit":
                    values[7][0]["exitCode"] = 1
                elif change == "later":
                    nodes["create-receipt"] = {"displayName": "create-receipt", "type": "Pod"}
                else:
                    nodes["adoption"]["outputs"] = {
                        "parameters": [{"name": "result", "value": "{}"}]
                    }
                with (
                    self.subTest(case=case, change=change),
                    self.assertRaises((AssertionError, KeyError, ValueError)),
                ):
                    MODULE.request_refusal(*values)

    def test_cli_reports_only_request_refusal_and_does_not_publish_adoption_result(self):
        for case in ("changed-request", "upgrade-request"):
            args = request_fault(case)
            _, workflow, parameters, raw, original, _, api, workers, log, baseline = args
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary)
                for name, value in (
                    (f"{case}-parameters.json", parameters),
                    ("adoption-identity-baseline.json", baseline),
                    ("adoption-parent-api.json", api),
                    (f"adoption-parent-{case}.workflow.json", workflow),
                    (f"adoption-parent-{case}.workers.json", workers),
                ):
                    (path / name).write_text(json.dumps(value), encoding="utf-8")
                (path / f"{case}-request.json").write_bytes(raw)
                (path / "parent-request.json").write_bytes(original)
                (path / "adoption-parent-worker.log").write_text(log, encoding="utf-8")
                result = subprocess.run(
                    [
                        sys.executable,
                        str(FIXTURE / "adoption_identity_evidence.py"),
                        case,
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
                self.assertEqual(
                    json.loads((path / f"adoption-parent-{case}.outcome.json").read_text()),
                    MODULE.request_refusal(*args),
                )
                self.assertFalse((path / f"adoption-parent-{case}.result.json").exists())
