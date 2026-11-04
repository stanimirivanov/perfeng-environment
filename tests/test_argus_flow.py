"""The joint acceptance receipt must correlate the real execution boundaries."""

import importlib
import unittest


class JointFlowTests(unittest.TestCase):
    def test_change_receipt_requires_matching_retained_head_and_capability(self):
        flow = importlib.import_module("tests.integration.argus-perfeng.flow")
        expected = {"base": "a" * 40, "head": "b" * 40, "delivery": "delivery-1"}
        impact = {
            "status": "complete",
            "baseRevision": {"digest": expected["base"]},
            "headRevision": {"digest": expected["head"]},
            "trigger": {"deliveryId": expected["delivery"]},
            "documents": [{"operations": [{"capabilities": ["checkout"]}]}],
        }
        self.assertEqual(flow.change_receipt(expected, impact)["head"], expected["head"])
        impact["headRevision"] = {"digest": "c" * 40}
        with self.assertRaisesRegex(ValueError, "change identity"):
            flow.change_receipt(expected, impact)

    def test_change_receipt_rejects_incomplete_impact(self):
        flow = importlib.import_module("tests.integration.argus-perfeng.flow")
        with self.assertRaisesRegex(ValueError, "change identity"):
            flow.change_receipt({}, {"status": "partial"})

    def test_projection_must_match_the_admitted_run_and_report(self):
        acceptance = importlib.import_module("tests.integration.argus-perfeng.acceptance")
        with self.assertRaisesRegex(ValueError, "projection"):
            acceptance.verify_projection(
                {"run": {"id": "foreign"}, "analysis": {"decisions": []}},
                {"id": "expected"},
                "a" * 64,
            )

    def test_projection_requires_the_exact_retained_analysis_digest(self):
        acceptance = importlib.import_module("tests.integration.argus-perfeng.acceptance")
        run = {"issuer": "https://perfeng.fixture.invalid", "id": "admitted"}
        projection = {
            "run": run,
            "analysis": {"decisions": [{"reportSha256": "a" * 64, "rules": [{"id": "latency"}]}]},
        }
        acceptance.verify_projection(projection, run, "a" * 64)
        with self.assertRaisesRegex(ValueError, "projection"):
            acceptance.verify_projection(projection, run, "b" * 64)

    def test_baseline_qualification_uses_only_observed_http_duration_points(self):
        import json

        acceptance = importlib.import_module("tests.integration.argus-perfeng.acceptance")
        point = {"type": "Point", "metric": "http_req_duration", "data": {"value": 10}}
        noise = {"type": "Point", "metric": "checks", "data": {"value": 1}}
        points = "\n".join(json.dumps(item) for item in [point] * 10 + [noise])
        self.assertEqual(
            acceptance.qualification(points),
            {"status": "PASSED", "reasons": [], "sampleCount": 10, "maximumCv": 0},
        )
        with self.assertRaisesRegex(ValueError, "baseline requires"):
            acceptance.qualification(json.dumps(point))

    def test_catalog_reference_is_encoded_in_argus_request_field_order(self):
        acceptance = importlib.import_module("tests.integration.argus-perfeng.acceptance")
        catalog = {
            "id": "checkout",
            "issuer": "https://perfeng.fixture.invalid",
            "revision": "1.0.0",
            "sha256": "a" * 64,
        }
        wire = acceptance.wire_reference(catalog)
        self.assertEqual(wire, catalog)
        self.assertEqual(list(wire), ["issuer", "id", "revision", "sha256"])

    def test_deadline_compatible_profile_pins_the_job_and_raw_manifest_to_new_workload(self):
        import json

        acceptance = importlib.import_module("tests.integration.argus-perfeng.acceptance")
        original = {"id": "checkout-smoke", "version": "1.0.0", "sha256": "a" * 64}
        entry = {"workload": original, "executionTemplate": "jobs/checkout.json"}
        job = {
            "spec": {
                "template": {
                    "spec": {
                        "initContainers": [
                            {
                                "name": "k6",
                                "args": ["k6 run --config workloads/smoke/checkout.json"],
                            }
                        ],
                        "containers": [{"name": "publish-raw", "args": [json.dumps(original)]}],
                    }
                }
            }
        }
        files = {"jobs--checkout.json": json.dumps(job)}
        acceptance.Acceptance.shorten_workload(entry, files)
        self.assertNotEqual(entry["workload"]["sha256"], original["sha256"])
        updated = json.loads(files["jobs--checkout.json"])["spec"]["template"]["spec"]
        self.assertIn('"duration":"7s"', updated["initContainers"][0]["args"][0])
        self.assertEqual(json.loads(updated["containers"][0]["args"][0]), entry["workload"])
