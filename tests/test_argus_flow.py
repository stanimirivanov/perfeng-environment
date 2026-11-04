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
