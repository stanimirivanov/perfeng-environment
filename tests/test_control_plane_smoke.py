import copy
import json
import unittest
from unittest.mock import patch

from scripts import control_plane_smoke as smoke
from scripts.control_plane_release import DeploymentLock

RELEASE = DeploymentLock(
    "image", "a" * 40, "b" * 40, "chart", "1.0.0", "bundle/1.0.0", "1.0.0", "0.13.0"
)
POLICY = b'{"metadata":{"name":"checkout-observe","version":"1.0.0"}}'
ENTRY = {
    "testId": "checkout-api",
    "profile": "smoke",
    "catalogue": {"id": "platform-tests", "version": "1.1.0", "sha256": "c" * 64},
    "environment": {
        "id": "local-kind-windows",
        "version": "1.0.0",
        "sha256": "d" * 64,
        "fingerprint": "e" * 64,
    },
    "policy": "policies/checkout.json",
    "candidateImages": [smoke.CANDIDATE_IMAGE],
}


class ControlPlaneSmokeTests(unittest.TestCase):
    def test_request_comes_from_locked_registry_and_policy_bytes(self) -> None:
        documents = {
            "bundle/1.0.0/registry.json": json.dumps({"entries": [ENTRY]}).encode(),
            "bundle/1.0.0/policies/checkout.json": POLICY,
        }
        with patch(
            "scripts.control_plane_smoke.revision_reader",
            return_value=documents.__getitem__,
        ) as reader:
            request = smoke.smoke_request(RELEASE, "f" * 40)

        reader.assert_called_once_with(
            smoke.DEFAULT_K8S_ROOT,
            smoke.DEPLOYMENT_REPOSITORY,
            RELEASE.deployment_revision,
        )
        self.assertEqual(request["testSuite"], "checkout-api")
        self.assertEqual(request["candidate"]["image"], smoke.CANDIDATE_IMAGE)
        self.assertEqual(
            request["environment"],
            {"id": "local-kind-windows", "version": "1.0.0", "sha256": "d" * 64},
        )
        self.assertEqual(request["policy"]["id"], "checkout-observe")

    def test_client_is_restricted_and_references_the_token_secret(self) -> None:
        compile(smoke.CLIENT_PROGRAM, "control-plane-smoke-client", "exec")
        document = smoke.client_pod(
            "control-plane-smoke-0123456789abcdef", {"request": "value"}, "smoke-" + "a" * 32
        )
        pod = document["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["nodeSelector"], {"workload": "control-plane"})
        self.assertEqual(pod["activeDeadlineSeconds"], 1200)
        container = pod["containers"][0]
        self.assertNotEqual(container["image"], smoke.CANDIDATE_IMAGE)
        self.assertEqual(container["image"], smoke.CLIENT_IMAGE)
        self.assertIn("@sha256:", container["image"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        token = next(item for item in container["env"] if item["name"] == "PERFENG_OPERATOR_TOKEN")
        self.assertEqual(
            token["valueFrom"]["secretKeyRef"],
            {"name": "perfeng-control-plane-authentication", "key": "operator-token"},
        )
        self.assertNotIn("delete", smoke.CLIENT_PROGRAM.lower())

    def test_receipt_requires_complete_terminal_artifact_set(self) -> None:
        receipt = {
            "runId": "perf-20260912-120000-abcdef12",
            "state": "COMPLETED",
            "revision": 9,
            "toolExitCode": 0,
            "artifactFormats": smoke.EXPECTED_FORMATS,
        }
        self.assertEqual(smoke.validate_result(json.dumps(receipt)), receipt)
        receipt["artifactFormats"] = receipt["artifactFormats"][:-1]
        with self.assertRaises(ValueError):
            smoke.validate_result(json.dumps(receipt))

    def test_failed_or_foreign_client_pod_is_rejected(self) -> None:
        expected = smoke.client_pod(
            "control-plane-smoke-0123456789abcdef", {"request": "value"}, "smoke-" + "a" * 32
        )
        live_spec = copy.deepcopy(expected["spec"])
        live_spec["tolerations"].extend(
            [
                {
                    "key": "node.kubernetes.io/not-ready",
                    "operator": "Exists",
                    "effect": "NoExecute",
                    "tolerationSeconds": 300,
                },
                {
                    "key": "node.kubernetes.io/unreachable",
                    "operator": "Exists",
                    "effect": "NoExecute",
                    "tolerationSeconds": 300,
                },
            ]
        )
        live = {
            "metadata": {"name": expected["metadata"]["name"], "uid": "pod-uid"},
            "spec": live_spec,
            "status": {
                "phase": "Succeeded",
                "containerStatuses": [{"state": {"terminated": {"exitCode": 0}}}],
            },
        }
        smoke.validate_client_pod(live, expected)
        live["status"]["containerStatuses"][0]["state"]["terminated"]["exitCode"] = 1
        with self.assertRaises(ValueError):
            smoke.validate_client_pod(live, expected)


if __name__ == "__main__":
    unittest.main()
