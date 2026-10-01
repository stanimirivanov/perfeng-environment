from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from scripts.control_plane_release import (
    CONTROL_PLANE_HTTP_PORT,
    DEPLOYMENT_REPOSITORY,
    load_lock,
    revision_reader,
    validate_deployment_source,
)


class ControlPlaneReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "control-plane.json"
        self.value: dict[str, Any] = {
            "schemaVersion": 1,
            "controlPlane": {
                "sourceRepository": ("https://github.com/stanimirivanov/perfeng-control-plane.git"),
                "sourceRevision": "1" * 40,
                "image": ("ghcr.io/stanimirivanov/perfeng-control-plane@sha256:" + "2" * 64),
            },
            "deployment": {
                "repository": "https://github.com/stanimirivanov/perfeng-k8s.git",
                "revision": "3" * 40,
                "chart": {"path": "charts/control-plane", "version": "0.2.0"},
                "runtimeBundle": {
                    "path": "bundles/local-kind-windows/1.1.0",
                    "version": "1.1.0",
                    "contractsVersion": "0.13.0",
                },
                "runtimeResources": {
                    "runtimeConfigMap": "perfeng-control-plane-runtime-" + "4" * 12,
                    "registryConfigMap": "perfeng-runtime-registry-" + "5" * 12,
                },
            },
        }

    def load(self):
        self.path.write_text(json.dumps(self.value), encoding="utf-8")
        return load_lock(self.path)

    def test_loads_complete_immutable_selection(self) -> None:
        lock = self.load()
        self.assertEqual(lock.chart_version, "0.2.0")
        self.assertIn("@sha256:", lock.image)
        self.assertEqual(lock.runtime_config_map, "perfeng-control-plane-runtime-" + "4" * 12)

    def test_ci_checkouts_match_the_deployment_lock(self) -> None:
        root = Path(__file__).resolve().parents[1]
        lock = load_lock(root / "deployments/local-control-plane.json")
        workflow = yaml.safe_load((root / ".github/workflows/validate.yml").read_text())
        steps = workflow["jobs"]["validate"]["steps"]
        checkouts = {step.get("name"): step.get("with") for step in steps if "name" in step}
        token = "${{ secrets.PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN }}"

        self.assertEqual(
            checkouts["Check out pinned control-plane deployment source"],
            {
                "repository": "stanimirivanov/perfeng-k8s",
                "ref": lock.deployment_revision,
                "token": token,
                "path": "perfeng-k8s",
                "persist-credentials": False,
            },
        )
        self.assertEqual(
            checkouts["Check out pinned control-plane source"],
            {
                "repository": "stanimirivanov/perfeng-control-plane",
                "ref": lock.source_revision,
                "token": token,
                "path": "perfeng-control-plane",
                "persist-credentials": False,
            },
        )
        makefile = (root / "Makefile").read_text(encoding="utf-8")
        self.assertIn("scripts/control_plane_release.py validate", makefile)
        self.assertIn('--k8s-root "$(K8S_ROOT)"', makefile)
        self.assertIn('--control-plane-root "$(CONTROL_PLANE_ROOT)"', makefile)

    def test_rejects_mutable_or_ambiguous_identity(self) -> None:
        cases = [
            ("image", "ghcr.io/stanimirivanov/perfeng-control-plane:latest"),
            ("sourceRevision", "main"),
            ("chartPath", "../control-plane"),
            ("bundleVersion", "1.2.0"),
            ("runtimeConfigMap", "perfeng-control-plane-runtime"),
            ("registryConfigMap", "perfeng-runtime-registry"),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.value)
                if field in {"image", "sourceRevision"}:
                    candidate["controlPlane"][field] = value
                elif field == "chartPath":
                    candidate["deployment"]["chart"]["path"] = value
                elif field in {"runtimeConfigMap", "registryConfigMap"}:
                    candidate["deployment"]["runtimeResources"][field] = value
                else:
                    candidate["deployment"]["runtimeBundle"]["version"] = value
                self.path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_lock(self.path)

    def test_validates_chart_bundle_and_referenced_files(self) -> None:
        lock = self.load()
        files = {
            "charts/control-plane/Chart.yaml": yaml.safe_dump(
                {"name": "perfeng-control-plane", "version": "0.2.0"}
            ).encode(),
            "charts/control-plane/values.yaml": yaml.safe_dump(
                {"httpPort": CONTROL_PLANE_HTTP_PORT}
            ).encode(),
            "bundles/local-kind-windows/1.1.0/registry.json": json.dumps(
                {
                    "schemaVersion": 1,
                    "entries": [
                        {
                            "contractsVersion": "0.13.0",
                            "policy": "policies/checkout.json",
                            "executionTemplate": "jobs/execution.json",
                        }
                    ],
                }
            ).encode(),
        }
        for relative in [
            "jobs/analysis.yaml",
            "jobs/reporting.yaml",
            "jobs/execution.json",
            "policies/checkout.json",
        ]:
            files[f"bundles/local-kind-windows/1.1.0/{relative}"] = b"present"

        def read(path: str) -> bytes:
            try:
                return files[path]
            except KeyError as error:
                raise ValueError("missing revision file") from error

        validate_deployment_source(lock, read)

        files["charts/control-plane/values.yaml"] = yaml.safe_dump({"httpPort": 8081}).encode()
        with self.assertRaisesRegex(ValueError, "HTTP port"):
            validate_deployment_source(lock, read)
        files["charts/control-plane/values.yaml"] = yaml.safe_dump(
            {"httpPort": CONTROL_PLANE_HTTP_PORT}
        ).encode()

        del files["bundles/local-kind-windows/1.1.0/jobs/analysis.yaml"]
        with self.assertRaises(ValueError):
            validate_deployment_source(lock, read)

    def test_revision_reader_accepts_parent_relative_checkout_path(self) -> None:
        environment = self.root / "perfeng-environment"
        repository = self.root / "perfeng-k8s"
        environment.mkdir()
        repository.mkdir()
        sibling_path = environment / ".." / "perfeng-k8s"

        with patch(
            "scripts.control_plane_release.git",
            side_effect=[DEPLOYMENT_REPOSITORY.encode(), b"commit\n"],
        ) as git:
            read = revision_reader(sibling_path, DEPLOYMENT_REPOSITORY, "3" * 40)

        self.assertTrue(callable(read))
        self.assertEqual(git.call_args_list[0].args[0], repository.resolve())

    def test_rejects_contract_drift_and_traversing_entry_paths(self) -> None:
        lock = self.load()
        chart = yaml.safe_dump({"name": "perfeng-control-plane", "version": "0.2.0"}).encode()
        chart_values = yaml.safe_dump({"httpPort": CONTROL_PLANE_HTTP_PORT}).encode()
        for entry in [
            {"contractsVersion": "0.14.0", "policy": "policies/checkout.json"},
            {"contractsVersion": "0.13.0", "policy": "../outside.json"},
        ]:
            manifest = json.dumps({"schemaVersion": 1, "entries": [entry]}).encode()

            def read(path: str, registry: bytes = manifest) -> bytes:
                if path.endswith("Chart.yaml"):
                    return chart
                if path.endswith("values.yaml"):
                    return chart_values
                if path.endswith("registry.json"):
                    return registry
                return b"present"

            with self.assertRaises(ValueError):
                validate_deployment_source(lock, read)


if __name__ == "__main__":
    unittest.main()
