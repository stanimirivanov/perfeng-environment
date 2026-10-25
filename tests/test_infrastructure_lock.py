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

        self.assertEqual(lock.revision, "4017bdda1e6d1f14592e2112ab78c1e67f710981")
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

    def select_redis_overlay(self) -> None:
        configuration = yaml.safe_load(self.files[self.lock.argocd_bootstrap])
        configuration["images"] = [
            {
                "name": "public.ecr.aws/docker/library/redis",
                "newName": "docker.io/library/redis",
                "digest": "sha256:08ad0b1d280850169a790dba1393ff7a90aef951fc19632cf4d3ce4f78e679ba",
            }
        ]
        configuration["patches"] = [{"path": "redis-pull-policy.yaml"}]
        self.files[self.lock.argocd_bootstrap] = yaml.safe_dump(configuration).encode()
        path = str(Path(self.lock.argocd_bootstrap).with_name("redis-pull-policy.yaml")).replace(
            "\\", "/"
        )
        self.files[path] = yaml.safe_dump(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "argocd-redis"},
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"name": "redis", "imagePullPolicy": "IfNotPresent"}]
                        }
                    }
                },
            }
        ).encode()

    def test_validates_reviewed_redis_overlay_from_pinned_source(self) -> None:
        self.select_redis_overlay()
        infrastructure_lock.validate_source(self.lock, self.read)

    def test_rejects_partial_or_unreviewed_redis_transforms(self) -> None:
        self.select_redis_overlay()
        original = copy.deepcopy(self.files)
        for change in (
            "registry",
            "digest",
            "image-target",
            "patch-path",
            "missing-images",
            "missing-patches",
            "extra",
        ):
            with self.subTest(change=change):
                self.files = copy.deepcopy(original)
                configuration = yaml.safe_load(self.files[self.lock.argocd_bootstrap])
                if change == "registry":
                    configuration["images"][0]["newName"] = "example.test/redis"
                elif change == "digest":
                    configuration["images"][0]["digest"] = "sha256:" + "0" * 64
                elif change == "image-target":
                    configuration["images"][0]["name"] = "other/redis"
                elif change == "patch-path":
                    configuration["patches"][0]["path"] = "../redirect.yaml"
                elif change.startswith("missing-"):
                    del configuration[change.removeprefix("missing-")]
                else:
                    configuration["labels"] = []
                self.files[self.lock.argocd_bootstrap] = yaml.safe_dump(configuration).encode()
                with self.assertRaisesRegex(ValueError, "Argo CD bootstrap"):
                    infrastructure_lock.validate_source(self.lock, self.read)

    def test_rejects_missing_or_changed_pinned_pull_policy_patch(self) -> None:
        self.select_redis_overlay()
        original = copy.deepcopy(self.files)
        path = str(Path(self.lock.argocd_bootstrap).with_name("redis-pull-policy.yaml")).replace(
            "\\", "/"
        )
        for change in (
            "missing",
            "name",
            "namespace",
            "container",
            "always",
            "never",
            "image",
            "extra",
        ):
            with self.subTest(change=change):
                self.files = copy.deepcopy(original)
                if change == "missing":
                    del self.files[path]
                else:
                    document = yaml.safe_load(self.files[path])
                    container = document["spec"]["template"]["spec"]["containers"][0]
                    if change == "name":
                        document["metadata"]["name"] = "other"
                    elif change == "namespace":
                        document["metadata"]["namespace"] = "argocd"
                    elif change == "container":
                        container["name"] = "secret-init"
                    elif change in {"always", "never"}:
                        container["imagePullPolicy"] = change.title()
                    elif change == "image":
                        container["image"] = "example.test/redis"
                    else:
                        document["spec"]["replicas"] = 2
                    self.files[path] = yaml.safe_dump(document).encode()
                with self.assertRaises(ValueError):
                    infrastructure_lock.validate_source(self.lock, self.read)

    def test_rejects_cluster_topology_drift(self) -> None:
        cluster = yaml.safe_load(self.files[self.lock.cluster_configuration])
        cluster["nodes"][1]["labels"]["workload"] = "general"
        self.files[self.lock.cluster_configuration] = yaml.safe_dump(cluster).encode()

        with self.assertRaisesRegex(ValueError, "local-kind profile"):
            infrastructure_lock.validate_source(self.lock, self.read)

    def test_rejects_an_incompatible_lifecycle(self) -> None:
        self.files[self.lock.argocd_lifecycle] = b'NAMESPACE = "argocd"\nOWNER = "other"\n'

        with self.assertRaisesRegex(ValueError, "executable environment contract"):
            infrastructure_lock.validate_source(self.lock, self.read)


if __name__ == "__main__":
    unittest.main()
