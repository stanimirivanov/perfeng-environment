import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from scripts import control_plane_runtime as runtime
from scripts.control_plane_release import DeploymentLock

RELEASE = DeploymentLock(
    "image", "a" * 40, "b" * 40, "chart", "1.0.0", "bundle/1.0.0", "1.0.0", "0.13.0"
)


class ControlPlaneRuntimeTests(unittest.TestCase):
    def test_configuration_is_complete_and_contains_no_credentials(self) -> None:
        values = runtime.runtime_configuration(RELEASE)
        self.assertEqual(len(values), 33)
        self.assertEqual(values["PERFENG_CONTRACTS_VERSION"], "0.13.0")
        self.assertFalse(any("PASSWORD" in key or "DATABASE_URL" in key for key in values))

    def test_authentication_stores_only_digest_in_mounted_document(self) -> None:
        token = "operator-token-" + "x" * 40
        values = runtime.authentication(token)
        self.assertNotIn(token, values["credentials.json"])
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), values["credentials.json"])
        self.assertEqual(values["operator-token"], token)

    def test_existing_resource_requires_exact_immutable_data(self) -> None:
        expected = runtime.object_document("Secret", "example", {"key": "value"}, RELEASE)
        stored = {
            **expected,
            "data": {"key": base64.b64encode(b"value").decode()},
        }
        stored.pop("stringData")
        runtime.validate_resource(stored, expected)
        stored["immutable"] = False
        with self.assertRaises(ValueError):
            runtime.validate_resource(stored, expected)

    def test_registry_files_come_from_locked_revision(self) -> None:
        documents = {
            "bundle/1.0.0/registry.json": json.dumps(
                {
                    "entries": [
                        {
                            "policy": "policies/example.json",
                            "executionTemplate": "jobs/execution.json",
                        }
                    ]
                }
            ).encode(),
            "bundle/1.0.0/policies/example.json": b"policy",
            "bundle/1.0.0/jobs/execution.json": b"execution",
            "bundle/1.0.0/jobs/analysis.yaml": b"analysis",
            "bundle/1.0.0/jobs/reporting.yaml": b"reporting",
        }
        with patch(
            "scripts.control_plane_runtime.revision_reader",
            return_value=documents.__getitem__,
        ) as reader:
            files = runtime.registry_files(RELEASE)

        reader.assert_called_once_with(
            runtime.DEFAULT_K8S_ROOT,
            runtime.DEPLOYMENT_REPOSITORY,
            RELEASE.deployment_revision,
        )
        self.assertEqual(
            set(files),
            {
                "registry.json",
                "policies--example.json",
                "jobs--execution.json",
                "jobs--analysis.yaml",
                "jobs--reporting.yaml",
            },
        )


if __name__ == "__main__":
    unittest.main()
