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

    def test_colocated_helper_requests_fit_the_hosted_node_budget(self):
        serving = serving_module()
        names = ("sample-sut", "seaweedfs", "baseline-reader", "fixture-github", "argus", "perfeng")
        reserved_millicpu = 0
        for name in names:
            document = serving.pod(name, serving.PLATFORM, "reviewed-image", ["run"])
            resources = document["spec"]["containers"][0]["resources"]
            self.assertEqual(resources["limits"], {"cpu": "1", "memory": "512Mi"})
            request = resources.get("requests", {}).get("cpu", resources["limits"]["cpu"])
            reserved_millicpu += int(request[:-1]) if request.endswith("m") else int(request) * 1000
        # Leave room for Kubernetes and the real execution/analysis Jobs on
        # the standard four-core runner instead of reserving six whole cores.
        self.assertLessEqual(reserved_millicpu, 1000)

    def test_unschedulable_pod_reports_condition_without_vendor_message(self):
        import contextlib
        import io

        serving = serving_module()
        prepared = Mock(root=Path("/tmp/private-fixture"), run_id="joint-test")
        driver = serving.Serving(prepared)
        prepared.command.side_effect = [
            ValueError("vendor command failed"),
            SimpleNamespace(
                stdout=json.dumps(
                    {
                        "status": {
                            "phase": "Pending",
                            "conditions": [
                                {
                                    "type": "PodScheduled",
                                    "status": "False",
                                    "reason": "Unschedulable",
                                    "message": "untrusted private scheduler message",
                                }
                            ],
                        }
                    }
                ),
                returncode=0,
            ),
        ]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(ValueError):
            driver.wait("baseline-reader", serving.PLATFORM)
        self.assertIn("Unschedulable", output.getvalue())
        self.assertNotIn("private", output.getvalue())
