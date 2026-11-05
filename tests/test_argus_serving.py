"""Readiness evidence requires both products and their disposable dependencies."""

import importlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def serving_module():
    return importlib.import_module("tests.integration.argus-perfeng.serving")


class ServingReadinessTests(unittest.TestCase):
    def test_failed_product_probe_never_records_readiness(self):
        serving = serving_module()
        evidence = {"productAcceptance": "not-run"}
        probe = Mock(side_effect=[None, None, None, None, ValueError("Perfeng unavailable")])
        with self.assertRaisesRegex(ValueError, "Perfeng unavailable"):
            serving.prove_readiness(evidence, probe)
        self.assertNotIn("servingReady", evidence)

    def test_success_records_only_readiness(self):
        serving = serving_module()
        evidence = {"productAcceptance": "not-run"}
        checked = set()
        serving.prove_readiness(evidence, checked.add)
        self.assertEqual(checked, {"sample-sut", "seaweedfs", "argus", "perfeng", "product-http"})
        self.assertTrue(evidence["servingReady"])
        self.assertEqual(evidence["productAcceptance"], "not-run")

    def test_cleanup_refuses_kind_nodes_outside_the_owned_network(self):
        serving = serving_module()
        prepared = Mock(root=Path("/tmp/private-fixture"), run_id="joint-test")
        driver = serving.Serving(prepared)
        driver.network_attempted = True
        driver.network_id = "owned-id"
        prepared.inspect.return_value = {"Id": "owned-id"}
        prepared.docker.side_effect = [
            SimpleNamespace(stdout="node-id"),
            SimpleNamespace(
                stdout=json.dumps(
                    [
                        {
                            "Name": "/joint-test-control-plane",
                            "NetworkSettings": {
                                "Networks": {"joint-test-kind": {"NetworkID": "foreign-id"}}
                            },
                        }
                    ]
                )
            ),
        ]
        with self.assertRaisesRegex(ValueError, "foreign kind node"):
            driver.cleanup()
        prepared.command.assert_not_called()
        self.assertEqual(prepared.docker.call_count, 2)

    def test_cold_storage_startup_can_finish_after_the_old_readiness_window(self):
        serving = serving_module()
        prepared = Mock(root=Path("/tmp/private-fixture"), run_id="joint-test")
        driver = serving.Serving(prepared)

        def eventual_readiness(args, **kwargs):
            # Model an image pull and startup taking 120 seconds. A real
            # ready condition is still required within the finite budget.
            ready_window = next(arg for arg in args if arg.startswith("--timeout="))
            if int(ready_window.removeprefix("--timeout=").removesuffix("s")) < 120:
                raise ValueError("pod readiness timed out")
            self.assertGreater(kwargs["timeout"], 120)
            return SimpleNamespace(stdout="pod/seaweedfs condition met")

        prepared.command.side_effect = eventual_readiness
        driver.wait("seaweedfs", serving.PLATFORM)

    def test_failed_storage_readiness_reports_only_a_fixed_state_signal(self):
        import contextlib
        import io

        serving = serving_module()
        prepared = Mock(root=Path("/tmp/private-fixture"), run_id="joint-test")
        driver = serving.Serving(prepared)
        prepared.command.side_effect = [
            ValueError("vendor output contains private credentials"),
            SimpleNamespace(
                stdout=json.dumps(
                    {
                        "status": {
                            "containerStatuses": [
                                {
                                    "state": {
                                        "waiting": {
                                            "reason": "ImagePullBackOff",
                                            "message": "private-token",
                                        }
                                    }
                                }
                            ]
                        }
                    }
                ),
                returncode=0,
            ),
        ]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(ValueError):
            driver.wait("seaweedfs", serving.PLATFORM)
        self.assertIn("ImagePullBackOff", output.getvalue())
        self.assertNotIn("private", output.getvalue())
