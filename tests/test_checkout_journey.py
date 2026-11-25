"""Exercise the operator's submit and resume commands with pinned checkout inputs."""

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from scripts import checkout_operator
from tests.test_checkout_report import RUN, claim


@unittest.skipUnless(
    os.environ.get("PERFENG_TEST_CHECKOUT_CONTRACTS") == "1", "requires pinned siblings"
)
class CheckoutJourneyTests(unittest.TestCase):
    def test_denied_registration_then_delivered_report_resumes_one_original_run(self):
        selection = checkout_operator.select(checkout_operator.cluster.ROOT, None, None)
        approvals = [
            "--lock-sha256",
            selection.identity["lockSha256"],
            "--cluster-uid",
            selection.identity["clusterUid"],
        ]
        candidate = claim(
            "normalized-result.json", "normalized-result/v1", "aaaa0000-1111-4222-8333-000000000001"
        )
        registration = claim(
            "analysis-result.json", "analysis-result/v1", "bbbb0000-1111-4222-8333-000000000001"
        )
        policy = selection.request["policy"]
        content = json.dumps(
            {
                "kind": "AnalysisResult",
                "schemaVersion": 1,
                "contractsVersion": selection.entry["contractsVersion"],
                "runId": RUN,
                "testId": "checkout-api",
                "createdAt": "2026-11-25T19:28:00Z",
                "producer": selection.entry["reportProducer"],
                "policy": {**policy, "mode": "observe"},
                "blocking": False,
                "candidateArtifact": candidate,
                "referenceArtifacts": [],
                "evaluations": [
                    {
                        "ruleId": "checkout-latency",
                        "metric": {"name": "api.http.duration", "statistic": "p95", "unit": "ms"},
                        "quality": {"status": "PASS", "reasons": []},
                        "slo": {
                            "status": "FAIL",
                            "value": 501,
                            "reasons": ["Above the policy maximum."],
                        },
                        "regression": {
                            "status": "NOT_EVALUATED",
                            "reasons": ["No regression rule."],
                        },
                    }
                ],
            }
        ).encode()
        registration.update(sizeBytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        accepted = {"id": RUN, "request": selection.request, "state": "CREATED", "revision": 1}
        posted_keys = []
        denied = True

        def api(path, *, request=None, key=None):
            if request is not None:
                self.assertEqual(path, "/v1/runs")
                self.assertEqual(request, selection.request)
                posted_keys.append(key)
                return accepted
            if path == "/v1/runs/" + RUN:
                return {**accepted, "revision": 8, "state": "COMPLETED"}
            self.assertEqual(path, "/v1/runs/" + RUN + "/artifacts")
            if denied:
                raise ValueError("Authenticated GET returned HTTP 403")
            return {"artifacts": [candidate, registration]}

        with (
            tempfile.TemporaryDirectory() as private,
            patch.object(checkout_operator, "verify_selection", return_value=selection),
            patch.object(checkout_operator, "require_active"),
            patch.object(
                checkout_operator.checkout_api,
                "session",
                side_effect=lambda *args: nullcontext(api),
            ),
        ):
            checkpoint = Path(private) / "attempt"
            args = [
                *approvals,
                "--execute",
                "--checkpoint-dir",
                str(checkpoint),
                "--token-file",
                str(Path(private) / "token"),
            ]
            self.assertEqual(checkout_operator.main(["submit", *args]), 1)
            state = checkpoint / "checkpoint.json"
            self.assertEqual(json.loads(state.read_bytes())["runId"], RUN)
            denied = False
            self.assertEqual(checkout_operator.main(["resume", *args]), 0)
            self.assertNotIn("decision", json.loads(state.read_bytes()))
            report = Path(private) / "delivered.json"
            report.write_bytes(content)
            report.chmod(0o600)
            self.assertEqual(
                checkout_operator.main(
                    [
                        "resume",
                        *args,
                        "--report-file",
                        str(report),
                        "--fulfilled-by",
                        "fixture owner",
                        "--fulfilled-at",
                        "2026-11-25T19:29:00Z",
                    ]
                ),
                0,
            )
            proof = json.loads(state.read_bytes())
            self.assertEqual(posted_keys, [proof["requestKey"]])
            self.assertEqual(proof["decision"]["verdict"], "EXCEEDED")
            self.assertEqual(proof["reportRegistration"]["sha256"], registration["sha256"])
            self.assertEqual((checkpoint / "report.json").read_bytes(), content)
            self.assertEqual(checkout_operator.main(["resume", *args]), 0)
            self.assertEqual(posted_keys, [proof["requestKey"]])
