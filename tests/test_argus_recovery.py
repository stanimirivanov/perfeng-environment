"""Regression checks for the recovery fixture's proof boundaries."""

import importlib
import unittest


class RecoveryTests(unittest.TestCase):
    def test_post_deadline_delivery_preserves_original_publication_lateness(self):
        recovery = importlib.import_module("tests.integration.argus-perfeng.recovery")
        deadline = "2026-11-04T16:00:00Z"
        publication = "2026-11-04T15:59:59Z"
        recovered = "2026-11-04T16:00:01Z"
        self.assertFalse(recovery.publication_lateness(publication, deadline, recovered))
        self.assertTrue(recovery.publication_lateness(deadline, deadline, recovered))
        with self.assertRaisesRegex(ValueError, "deadline"):
            recovery.publication_lateness(publication, deadline, publication)
