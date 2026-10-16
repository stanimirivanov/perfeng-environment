import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import control_plane_health as health
from scripts.control_plane_release import DeploymentLock

RELEASE = DeploymentLock(
    "ghcr.io/stanimirivanov/perfeng-control-plane@sha256:" + "1" * 64,
    "a" * 40,
    "b" * 40,
    "charts/control-plane",
    "1.0.0",
    "bundle/1.0.0",
    "1.0.0",
    "0.13.0",
    "perfeng-control-plane-runtime-" + "1" * 12,
    "perfeng-runtime-registry-" + "2" * 12,
)
ITEMS = [
    {"key": "jobs--analysis.yaml", "path": "jobs/analysis.yaml"},
    {"key": "registry.json", "path": "registry.json"},
]


def live_resources() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    deployment: dict[str, Any] = {
        "metadata": {"name": "control-plane", "namespace": "perf-platform", "generation": 2},
        "spec": {
            "replicas": 1,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "control-plane",
                            "image": RELEASE.image,
                            "envFrom": [
                                {"configMapRef": {"name": RELEASE.runtime_config_map}},
                                {"secretRef": {"name": "perfeng-control-plane-runtime"}},
                            ],
                        }
                    ],
                    "imagePullSecrets": [{"name": "perfeng-control-plane-registry"}],
                    "nodeSelector": {"workload": "control-plane"},
                    "tolerations": [
                        {
                            "key": "node-role.kubernetes.io/control-plane",
                            "operator": "Exists",
                            "effect": "NoSchedule",
                        }
                    ],
                    "volumes": [
                        {
                            "name": "authentication",
                            "secret": {"secretName": "perfeng-control-plane-authentication"},
                        },
                        {
                            "name": "registry",
                            "configMap": {"name": RELEASE.registry_config_map, "items": ITEMS},
                        },
                    ],
                }
            },
        },
        "status": {"observedGeneration": 2, "updatedReplicas": 1, "availableReplicas": 1},
    }
    service: dict[str, Any] = {
        "metadata": {"name": "control-plane", "namespace": "perf-platform"},
        "spec": {
            "type": "ClusterIP",
            "selector": {
                "app.kubernetes.io/name": "perfeng-control-plane",
                "app.kubernetes.io/instance": "control-plane",
            },
            "ports": [{"name": "http", "port": 8080, "protocol": "TCP", "targetPort": "http"}],
        },
    }
    pods: dict[str, Any] = {
        "items": [
            {
                "spec": {"containers": [{"image": RELEASE.image}]},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [
                        {
                            "ready": True,
                            "imageID": "ghcr.io/stanimirivanov/perfeng-control-plane@sha256:"
                            + "2" * 64,
                        }
                    ],
                },
            }
        ]
    }
    return deployment, service, pods


class ControlPlaneHealthTests(unittest.TestCase):
    @patch("scripts.control_plane_health.control_plane_runtime.registry_paths")
    def test_live_resources_require_exact_identity_and_readiness(self, paths) -> None:
        paths.return_value = ["jobs/analysis.yaml", "registry.json"]
        deployment, service, pods = live_resources()

        health.validate_deployment(deployment, RELEASE)
        health.validate_service(service)
        health.validate_pods(pods, RELEASE)
        responses = [
            "",
            json.dumps(deployment),
            json.dumps(service),
            json.dumps(pods),
            "ok\n",
            "ok\n",
            "perfeng_control_plane_ready 1\n",
        ]
        with patch("scripts.control_plane_health.cluster.run", side_effect=responses) as run:
            health.verify_resources(["kubectl"], RELEASE)
        self.assertNotIn("helm", " ".join(str(call) for call in run.call_args_list))

        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "tagged"
        with self.assertRaises(ValueError):
            health.validate_deployment(deployment, RELEASE)

    def test_operational_endpoints_have_bounded_acceptance(self) -> None:
        health.validate_endpoint("livez", "ok\n")
        health.validate_endpoint("readyz", "ok\n")
        health.validate_endpoint("metrics", "perfeng_control_plane_ready 1\n")
        with self.assertRaises(ValueError):
            health.validate_endpoint("readyz", '{"status":"ok"}')

    @patch("scripts.control_plane_health.verify_resources")
    @patch("scripts.control_plane_health.postgres.kubectl", return_value=["kubectl"])
    @patch("scripts.control_plane_health.control_plane_runtime.execute")
    @patch("scripts.control_plane_health.control_plane_migration.execute")
    @patch("scripts.control_plane_health.validate_release", return_value=RELEASE)
    def test_verification_uses_gitops_resources_without_helm_history(
        self,
        validate_release,
        migration_execute,
        runtime_execute,
        kubectl,
        verify_resources,
    ) -> None:
        root = Path("workspace/perfeng-environment")

        with (
            patch(
                "scripts.control_plane_health.execution_registry.selected_images",
                return_value=("image",),
            ),
            patch("scripts.control_plane_health.execution_registry.ensure_secret") as execution,
            patch("scripts.control_plane_health.benchmark_authority.verify") as authority,
        ):
            health.verify(root)
        execution.assert_called_once_with(root, ("image",), create=False)
        authority.assert_called_once_with(root, RELEASE)

        validate_release.assert_called_once_with(
            root / "deployments/local-control-plane.json",
            root.parent / "perfeng-k8s",
            root.parent / "perfeng-control-plane",
        )
        migration_execute.assert_called_once_with("health", root)
        runtime_execute.assert_called_once_with("health", root)
        kubectl.assert_called_once_with(root)
        verify_resources.assert_called_once_with(["kubectl"], RELEASE)


if __name__ == "__main__":
    unittest.main()
