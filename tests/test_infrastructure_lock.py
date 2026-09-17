from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from scripts import infrastructure_lock


class InfrastructureLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "infrastructure.json"
        self.value: dict[str, Any] = json.loads(
            infrastructure_lock.DEFAULT_LOCK.read_text(encoding="utf-8")
        )
        self.lock = infrastructure_lock.load_lock()
        cluster = {
            "apiVersion": "kind.x-k8s.io/v1alpha4",
            "kind": "Cluster",
            "name": self.lock.cluster_name,
            "networking": {"apiServerAddress": "127.0.0.1"},
            "nodes": [
                {
                    "role": "control-plane",
                    "image": self.lock.node_image,
                    "labels": {"workload": "control-plane"},
                },
                {
                    "role": "worker",
                    "image": self.lock.node_image,
                    "labels": {
                        "workload": "performance-generator",
                        "perfeng.io/node-type": "generator",
                    },
                },
                {
                    "role": "worker",
                    "image": self.lock.node_image,
                    "labels": {
                        "workload": "sut",
                        "perfeng.io/node-type": "sut",
                    },
                },
            ],
        }
        kustomization = {
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "namespace": self.lock.argocd_namespace,
            "resources": [
                "namespace.yaml",
                "https://raw.githubusercontent.com/argoproj/argo-cd/"
                f"{self.lock.argocd_version}/manifests/install.yaml",
            ],
        }
        namespace = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": self.lock.argocd_namespace,
                "labels": {
                    "app.kubernetes.io/name": self.lock.argocd_namespace,
                    "app.kubernetes.io/managed-by": "k8s-infrastructure",
                    "app.kubernetes.io/part-of": "kubernetes-substrate",
                },
            },
        }
        namespace_path = str(Path(self.lock.argocd_bootstrap).with_name("namespace.yaml")).replace(
            "\\", "/"
        )
        self.files = {
            self.lock.cluster_configuration: yaml.safe_dump(cluster).encode(),
            self.lock.cluster_lifecycle: (
                f'NAME = "{self.lock.cluster_name}"\n'
                f'NODE_IMAGE = "{self.lock.node_image}"\n'
                'ACTIONS = ("up", "ensure", "health", "down")\n'
                f"COMMAND_TIMEOUT = {infrastructure_lock.COMMAND_TIMEOUT}\n"
            ).encode(),
            self.lock.argocd_bootstrap: yaml.safe_dump(kustomization).encode(),
            namespace_path: yaml.safe_dump(namespace).encode(),
            self.lock.argocd_lifecycle: (
                f'NAMESPACE = "{self.lock.argocd_namespace}"\n'
                'OWNER = "k8s-infrastructure"\n'
                'LEGACY_OWNER = "perfeng-environment"\n'
                'ACTIONS = ("validate", "bootstrap", "health")\n'
            ).encode(),
        }

    def load(self) -> infrastructure_lock.InfrastructureLock:
        self.path.write_text(json.dumps(self.value), encoding="utf-8")
        return infrastructure_lock.load_lock(self.path)

    def read(self, path: str) -> bytes:
        try:
            return self.files[path]
        except KeyError as error:
            raise ValueError("missing pinned source file") from error

    def test_loads_complete_immutable_selection(self) -> None:
        lock = self.load()

        self.assertEqual(lock.revision, "65af7eaa655a4032cdcd9aab7db35f1ec36c1781")
        self.assertEqual(lock.profile, "local-kind/perfeng-local")
        self.assertEqual(lock.context, "kind-perfeng-local")
        self.assertEqual(lock.argocd_version, "v3.5.2")

    def test_rejects_mutable_or_ambiguous_identity(self) -> None:
        cases = [
            ("revision", "main"),
            ("profile", "../perfeng-local"),
            ("clusterConfiguration", "../cluster-config.yaml"),
            (
                "clusterConfiguration",
                "clusters/local-kind/perfeng-local/another-config.yaml",
            ),
            ("context", "another-context"),
            ("kubeconfig", ".local/another.kubeconfig"),
            ("nodeImage", "kindest/node:latest"),
            ("argoCDVersion", "stable"),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.value)
                if field == "revision":
                    candidate["source"][field] = value
                elif field in {"profile", "clusterConfiguration"}:
                    key = "name" if field == "profile" else field
                    candidate["profile"][key] = value
                else:
                    candidate["compatibility"][field] = value
                self.path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(ValueError):
                    infrastructure_lock.load_lock(self.path)

    def test_validates_executable_profile_and_gitops_compatibility(self) -> None:
        infrastructure_lock.validate_source(self.lock, self.read)

    def test_rejects_cluster_topology_drift(self) -> None:
        cluster = yaml.safe_load(self.files[self.lock.cluster_configuration])
        cluster["nodes"][1]["labels"]["workload"] = "general"
        self.files[self.lock.cluster_configuration] = yaml.safe_dump(cluster).encode()

        with self.assertRaisesRegex(ValueError, "local-kind profile"):
            infrastructure_lock.validate_source(self.lock, self.read)

    def test_rejects_a_dormant_or_incompatible_lifecycle(self) -> None:
        self.files[self.lock.argocd_lifecycle] = b'NAMESPACE = "argocd"\nOWNER = "other"\n'

        with self.assertRaisesRegex(ValueError, "executable environment contract"):
            infrastructure_lock.validate_source(self.lock, self.read)


if __name__ == "__main__":
    unittest.main()
