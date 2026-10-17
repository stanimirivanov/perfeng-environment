import base64
import hashlib
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import yaml

from scripts import control_plane_runtime as runtime
from scripts.control_plane_release import DeploymentLock

RELEASE = DeploymentLock(
    "image",
    "a" * 40,
    "b" * 40,
    "chart",
    "1.0.0",
    "bundle/1.0.0",
    "1.0.0",
    "0.13.0",
    "perfeng-control-plane-runtime-" + "1" * 12,
    "perfeng-runtime-registry-" + "2" * 12,
)


class ControlPlaneRuntimeTests(unittest.TestCase):
    def test_pinned_package_matches_reviewed_bundle_and_policy(self) -> None:
        manifest = '{"entries":[]}\n'
        files = {
            "registry.json": manifest,
            "jobs/analysis.yaml": "analysis\n",
            "jobs/reporting.yaml": "reporting\n",
        }
        selected = replace(
            RELEASE,
            package_path="packages/runtime-inputs/test-1.0.0",
            package_provenance_revision="a" * 40,
            runtime_config_map=runtime.content_addressed_name(
                runtime.RUNTIME_CONFIG_PREFIX, runtime.runtime_configuration(RELEASE)
            ),
            registry_config_map=runtime.content_addressed_name(
                runtime.REGISTRY_CONFIG_PREFIX,
                {path.replace("/", "--"): content for path, content in files.items()},
            ),
        )
        kustomization = {
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "namespace": "perf-platform",
            "generatorOptions": {
                "disableNameSuffixHash": True,
                "labels": {"app.kubernetes.io/managed-by": "perfeng-environment"},
                "annotations": {"perfeng.io/deployment-revision": "a" * 40},
            },
            "configMapGenerator": [
                {"name": selected.runtime_config_map, "envs": ["runtime.env"]},
                {
                    "name": selected.registry_config_map,
                    "files": [f"{path.replace('/', '--')}=files/{path}" for path in sorted(files)],
                },
            ],
            "patches": [
                {
                    "target": {"kind": "ConfigMap"},
                    "patch": "- op: add\n  path: /immutable\n  value: true",
                }
            ],
        }
        documents = {
            f"{selected.bundle_path}/{path}": content.encode() for path, content in files.items()
        }
        documents[f"{selected.package_path}/kustomization.yaml"] = yaml.safe_dump(
            kustomization
        ).encode()
        documents[f"{selected.package_path}/runtime.env"] = (
            "\n".join(
                f"{key}={value}" for key, value in runtime.runtime_configuration(selected).items()
            )
            + "\n"
        ).encode()
        documents.update(
            {
                f"{selected.package_path}/files/{path}": content.encode()
                for path, content in files.items()
            }
        )
        runtime.validate_package_source(selected, documents.__getitem__)
        documents[f"{selected.package_path}/files/jobs/analysis.yaml"] = b"changed\n"
        with self.assertRaisesRegex(ValueError, "registry bytes"):
            runtime.validate_package_source(selected, documents.__getitem__)
        documents[f"{selected.package_path}/files/jobs/analysis.yaml"] = b"analysis\n"
        kustomization["secretGenerator"] = [{"name": "unsafe"}]
        documents[f"{selected.package_path}/kustomization.yaml"] = yaml.safe_dump(
            kustomization
        ).encode()
        with self.assertRaisesRegex(ValueError, "Kustomize definition"):
            runtime.validate_package_source(selected, documents.__getitem__)

    def test_registry_mode_mounts_exact_benchmark_environment_bytes(self) -> None:
        manifest = json.dumps(
            {
                "entries": [
                    {
                        "policy": "policies/pod.json",
                        "executionTemplate": "jobs/benchmark.json",
                        "benchmark": {"environmentFile": "identities/environment.json"},
                    }
                ]
            }
        ).encode()
        descriptor = b'{"cluster":"perfeng-local"}\n'

        def read(path: str) -> bytes:
            if path.endswith("registry.json"):
                return manifest
            if path.endswith("identities/environment.json"):
                return descriptor
            return b"fixture"

        with patch("scripts.control_plane_runtime.revision_reader", return_value=read):
            data = runtime.registry_files(RELEASE)
        self.assertEqual(data["identities--environment.json"].encode(), descriptor)
        selected = replace(
            RELEASE, contracts_version="registry", entry_contracts_versions=("0.13.0", "0.22.0")
        )
        self.assertEqual(
            runtime.runtime_configuration(selected)["PERFENG_CONTRACTS_VERSION"], "registry"
        )

    def test_content_address_is_order_independent_and_changes_with_data(self) -> None:
        first = runtime.content_addressed_name("configuration", {"a": "1", "b": "2"})
        reordered = runtime.content_addressed_name("configuration", {"b": "2", "a": "1"})
        changed = runtime.content_addressed_name("configuration", {"a": "1", "b": "3"})

        self.assertEqual(first, reordered)
        self.assertNotEqual(first, changed)
        self.assertRegex(first, r"^configuration-[0-9a-f]{12}$")

    def test_lock_must_select_names_derived_from_reviewed_bytes(self) -> None:
        runtime_data = {"runtime": "value"}
        registry_data = {"registry": "value"}
        release = replace(
            RELEASE,
            runtime_config_map=runtime.content_addressed_name(
                runtime.RUNTIME_CONFIG_PREFIX, runtime_data
            ),
            registry_config_map=runtime.content_addressed_name(
                runtime.REGISTRY_CONFIG_PREFIX, registry_data
            ),
        )

        runtime.validate_config_map_selection(release, runtime_data, registry_data)
        with self.assertRaisesRegex(ValueError, "runtime ConfigMap identity"):
            runtime.validate_config_map_selection(release, {"runtime": "changed"}, registry_data)
        with self.assertRaisesRegex(ValueError, "registry ConfigMap identity"):
            runtime.validate_config_map_selection(release, runtime_data, {"registry": "changed"})

    def test_configuration_is_complete_and_contains_no_credentials(self) -> None:
        values = runtime.runtime_configuration(RELEASE)
        self.assertEqual(len(values), 34)
        self.assertEqual(values["PERFENG_HTTP_ADDRESS"], ":8080")
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

    def test_argocd_tracking_label_is_allowed_only_on_selected_configmaps(self) -> None:
        expected = runtime.object_document("ConfigMap", "example", {"key": "value"}, RELEASE)
        stored = json.loads(json.dumps(expected))
        stored["metadata"]["labels"]["app.kubernetes.io/instance"] = "perfeng-local-control-plane"
        runtime.validate_resource(stored, expected)
        stored["metadata"]["labels"]["app.kubernetes.io/instance"] = "foreign"
        with self.assertRaises(ValueError):
            runtime.validate_resource(stored, expected)

    def test_unchanged_resource_retains_its_creation_revision(self) -> None:
        expected = runtime.object_document("ConfigMap", "example", {"key": "value"}, RELEASE)
        stored = {
            **expected,
            "metadata": {
                **expected["metadata"],
                "annotations": {"perfeng.io/deployment-revision": "c" * 40},
            },
        }

        runtime.validate_resource(stored, expected)
        stored["metadata"]["annotations"]["perfeng.io/deployment-revision"] = "main"
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

    def test_registry_paths_preserve_mount_layout(self) -> None:
        manifest = json.dumps(
            {
                "entries": [
                    {
                        "policy": "policies/example.json",
                        "executionTemplate": "jobs/execution.json",
                    }
                ]
            }
        ).encode()
        with patch(
            "scripts.control_plane_runtime.revision_reader",
            return_value=lambda _path: manifest,
        ):
            paths = runtime.registry_paths(RELEASE)

        self.assertEqual(
            paths,
            [
                "jobs/analysis.yaml",
                "jobs/execution.json",
                "jobs/reporting.yaml",
                "policies/example.json",
                "registry.json",
            ],
        )


if __name__ == "__main__":
    unittest.main()
