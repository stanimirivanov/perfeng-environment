import io
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import control_plane_deployment as deployment
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
)
ITEMS = [
    {"key": "jobs--analysis.yaml", "path": "jobs/analysis.yaml"},
    {"key": "registry.json", "path": "registry.json"},
]


def chart_archive(name: str = "charts/control-plane/Chart.yaml") -> bytes:
    output = io.BytesIO()
    content = b"apiVersion: v2\nname: perfeng-control-plane\n"
    with tarfile.open(fileobj=output, mode="w") as archive:
        ancestor = tarfile.TarInfo("charts")
        ancestor.type = tarfile.DIRTYPE
        archive.addfile(ancestor)
        member = tarfile.TarInfo(name)
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class ControlPlaneDeploymentTests(unittest.TestCase):
    @patch("scripts.control_plane_deployment.control_plane_runtime.registry_paths")
    def test_helm_uses_only_locked_resource_identities(self, paths) -> None:
        paths.return_value = ["jobs/analysis.yaml", "registry.json"]
        with TemporaryDirectory() as temporary:
            command = deployment.helm_command(Path(temporary), RELEASE, deployment.cluster.ROOT)

        arguments = "\n".join(command)
        self.assertIn(f"image={RELEASE.image}", arguments)
        self.assertIn("runtimeConfigMap=perfeng-control-plane-runtime", arguments)
        self.assertIn("authenticationSecret=perfeng-control-plane-authentication", arguments)
        self.assertIn("registryItems[0].path=jobs/analysis.yaml", arguments)
        self.assertNotIn("PASSWORD", arguments)
        self.assertNotIn("operator-token", arguments)

    def test_locked_chart_extracts_only_the_pinned_archive(self) -> None:
        with patch("scripts.control_plane_deployment.git", return_value=chart_archive()) as git:
            with deployment.locked_chart(RELEASE, Path("repository")) as chart:
                self.assertEqual(
                    (chart / "Chart.yaml").read_text(encoding="utf-8"),
                    "apiVersion: v2\nname: perfeng-control-plane\n",
                )

        git.assert_called_once_with(
            Path("repository"),
            "archive",
            "--format=tar",
            RELEASE.deployment_revision,
            RELEASE.chart_path,
        )

    def test_locked_chart_rejects_path_escape(self) -> None:
        with patch(
            "scripts.control_plane_deployment.git",
            return_value=chart_archive("outside/Chart.yaml"),
        ):
            with self.assertRaises(ValueError):
                with deployment.locked_chart(RELEASE, Path("repository")):
                    pass

    @patch("scripts.control_plane_deployment.registry_items", return_value=ITEMS)
    def test_live_resources_require_exact_identity_and_readiness(self, _items) -> None:
        helm_status = {
            "name": "control-plane",
            "namespace": "perf-platform",
            "info": {"status": "deployed"},
            "chart": {"metadata": {"name": "perfeng-control-plane", "version": "1.0.0"}},
        }
        live_deployment = {
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
                                    {"configMapRef": {"name": "perfeng-control-plane-runtime"}},
                                    {"secretRef": {"name": "perfeng-control-plane-runtime"}},
                                ],
                            }
                        ],
                        "imagePullSecrets": [{"name": "perfeng-control-plane-registry"}],
                        "nodeSelector": {"workload": "control-plane"},
                        "volumes": [
                            {
                                "name": "authentication",
                                "secret": {"secretName": "perfeng-control-plane-authentication"},
                            },
                            {
                                "name": "registry",
                                "configMap": {
                                    "name": "perfeng-runtime-registry",
                                    "items": ITEMS,
                                },
                            },
                        ],
                    }
                },
            },
            "status": {"observedGeneration": 2, "updatedReplicas": 1, "availableReplicas": 1},
        }
        service = {
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
        pods = {
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

        deployment.validate_helm_status(helm_status, RELEASE)
        deployment.validate_deployment(live_deployment, RELEASE)
        deployment.validate_service(service)
        deployment.validate_pods(pods, RELEASE)
        live_deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "tagged"
        with self.assertRaises(ValueError):
            deployment.validate_deployment(live_deployment, RELEASE)

        helm_status["chart"]["metadata"]["version"] = "2.0.0"
        with self.assertRaises(ValueError):
            deployment.validate_helm_status(helm_status, RELEASE)

    def test_operational_endpoints_have_bounded_acceptance(self) -> None:
        deployment.validate_endpoint("livez", "ok\n")
        deployment.validate_endpoint("readyz", "ok\n")
        deployment.validate_endpoint("metrics", "perfeng_control_plane_ready 1\n")
        with self.assertRaises(ValueError):
            deployment.validate_endpoint("readyz", '{"status":"ok"}')


if __name__ == "__main__":
    unittest.main()
