"""Registry content, ready Pod identity and controller ownership are separate acceptance gates."""

import copy
import hashlib
import io
import json
import unittest
from contextlib import redirect_stdout
from typing import Any
from unittest.mock import patch

from scripts import native_target_health as health
from scripts import native_target_release as release
from tests.test_control_plane_registry import stored_secret


class NativeTargetHealthTests(unittest.TestCase):
    def setUp(self):
        self.manifest: dict[str, Any] = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + "b" * 64,
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ],
        }
        self.select_manifest(self.manifest)

    def select_manifest(self, manifest):
        self.content = json.dumps(manifest).encode()
        candidate = dict(release.load_selection().candidate)
        candidate["image"] = (
            release.REPOSITORY + "@sha256:" + hashlib.sha256(self.content).hexdigest()
        )
        self.selection = release.TargetSelection(
            candidate, release.load_selection().deployment_revision
        )

    def resources(self):
        container = {
            "name": "search",
            "image": self.selection.image,
            "imagePullPolicy": "IfNotPresent",
            "ports": [{"name": "http", "containerPort": 8080, "protocol": "TCP"}],
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]},
            },
            "volumeMounts": [{"name": "temporary", "mountPath": "/tmp"}],
            "resources": {
                "requests": {"cpu": "50m", "memory": "32Mi", "ephemeral-storage": "16Mi"},
                "limits": {"cpu": "500m", "memory": "64Mi", "ephemeral-storage": "32Mi"},
            },
        }
        for name, period, failures in (
            ("startupProbe", 2, 30),
            ("readinessProbe", 5, 2),
            ("livenessProbe", 10, 3),
        ):
            container[name] = {
                "httpGet": {"path": "/healthz", "port": "http", "scheme": "HTTP"},
                "periodSeconds": period,
                "timeoutSeconds": 1,
                "failureThreshold": failures,
                "successThreshold": 1,
            }
        spec = {
            "serviceAccountName": "native-search",
            "automountServiceAccountToken": False,
            "imagePullSecrets": [{"name": release.PULL_SECRET}],
            "nodeSelector": {"kubernetes.io/os": "linux", "kubernetes.io/arch": "amd64"},
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 101,
                "runAsGroup": 101,
                "fsGroup": 101,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [container],
            "volumes": [
                {"name": "temporary", "emptyDir": {"medium": "Memory", "sizeLimit": "16Mi"}}
            ],
        }
        deployment = {
            "metadata": {
                "name": "native-search",
                "namespace": release.NAMESPACE,
                "labels": health.LABELS,
                "generation": 1,
                "uid": "owned-deployment",
            },
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": health.LABELS},
                "template": {"metadata": {"labels": health.LABELS}, "spec": spec},
            },
            "status": {
                "observedGeneration": 1,
                "updatedReplicas": 1,
                "availableReplicas": 1,
                "replicas": 1,
            },
        }
        pods = {
            "items": [
                {
                    "metadata": {
                        "name": "native-search-fixture-abc",
                        "namespace": release.NAMESPACE,
                        "labels": health.LABELS,
                        "ownerReferences": [
                            {
                                "kind": "ReplicaSet",
                                "name": "native-search-fixture",
                                "uid": "owned-replica",
                                "controller": True,
                            }
                        ],
                    },
                    "spec": copy.deepcopy(spec),
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "containerStatuses": [
                            {
                                "name": "search",
                                "ready": True,
                                "state": {"running": {"startedAt": "2026-10-12T00:00:00Z"}},
                                "imageID": release.REPOSITORY + "@sha256:" + "b" * 64,
                            }
                        ],
                    },
                }
            ]
        }
        service = {
            "metadata": {
                "name": release.RELEASE,
                "namespace": release.NAMESPACE,
                "labels": health.LABELS,
            },
            "spec": {
                "type": "ClusterIP",
                "clusterIP": "10.0.0.1",
                "selector": health.LABELS,
                "ports": [{"name": "http", "port": 8080, "targetPort": "http", "protocol": "TCP"}],
            },
        }
        return deployment, pods, service

    def test_oci_and_docker_indices_resolve_the_child_not_the_index_digest(self):
        for index, manifest in (
            (
                "application/vnd.oci.image.index.v1+json",
                "application/vnd.oci.image.manifest.v1+json",
            ),
            (
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            ),
        ):
            value = copy.deepcopy(self.manifest)
            value["mediaType"] = index
            value["manifests"][0]["mediaType"] = manifest
            self.select_manifest(value)
            self.assertEqual(
                health.platform_digest(self.selection, self.content), "sha256:" + "b" * 64
            )

    def test_single_manifest_resolves_its_own_digest(self):
        for media_type in health.MANIFEST_TYPES:
            self.select_manifest(
                {"schemaVersion": 2, "mediaType": media_type, "config": {}, "layers": []}
            )
            self.assertEqual(
                health.platform_digest(self.selection, self.content),
                self.selection.image.split("@")[1],
            )

    def test_digest_mismatch_or_ambiguous_wrong_platform_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "manifest bytes differ"):
            health.platform_digest(self.selection, self.content + b" ")
        for manifests in (
            [],
            self.manifest["manifests"] * 2,
            [{"platform": {"os": "linux", "architecture": "arm64"}}],
        ):
            self.select_manifest({**self.manifest, "manifests": manifests})
            with self.assertRaisesRegex(ValueError, "exactly one"):
                health.platform_digest(self.selection, self.content)

    def test_ready_resources_require_the_platform_digest(self):
        deployment, pods, service = self.resources()
        health.validate_service(service)
        self.assertEqual(
            health.validate_resources(deployment, pods, self.selection, "sha256:" + "b" * 64),
            "native-search-fixture-abc",
        )
        pods["items"][0]["status"]["containerStatuses"][0]["imageID"] = self.selection.image
        self.assertEqual(
            health.validate_resources(deployment, pods, self.selection, "sha256:" + "b" * 64),
            "native-search-fixture-abc",
        )
        pods["items"][0]["status"]["containerStatuses"][0]["imageID"] = (
            release.REPOSITORY + "@sha256:" + "c" * 64
        )
        with self.assertRaisesRegex(ValueError, "platform image digest"):
            health.validate_resources(deployment, pods, self.selection, "sha256:" + "b" * 64)

    def test_unavailable_extra_terminating_or_wrong_image_pod_rejects(self):
        for change in ("generation", "extra", "terminating", "image"):
            deployment, pods, _ = self.resources()
            if change == "generation":
                deployment["status"]["observedGeneration"] = 0
            elif change == "extra":
                pods["items"] *= 2
            elif change == "terminating":
                pods["items"][0]["metadata"]["deletionTimestamp"] = "now"
            else:
                pods["items"][0]["spec"]["containers"][0]["image"] = "other"
            with self.subTest(change=change), self.assertRaises(ValueError):
                health.validate_resources(deployment, pods, self.selection, "sha256:" + "b" * 64)

    def test_public_service_or_injected_credentials_rejects(self):
        deployment, _, service = self.resources()
        service["spec"]["externalIPs"] = ["192.0.2.1"]
        with self.assertRaisesRegex(ValueError, "internal release"):
            health.validate_service(service)
        pod = deployment["spec"]["template"]["spec"]
        pod["containers"][0]["envFrom"] = [{"secretRef": {"name": "credentials"}}]
        with self.assertRaisesRegex(ValueError, "content boundary"):
            health.validate_pod_spec(pod, self.selection)

    def test_malformed_nested_api_input_fails_with_classified_errors(self):
        for value in ({"metadata": []}, {"metadata": {}, "spec": []}):
            with self.assertRaises(ValueError):
                health.validate_service(value)
        with self.assertRaises(ValueError):
            health.validate_pod_spec([], self.selection)

    def test_live_verification_is_read_only_and_checks_controller_ownership(self):
        deployment, pods, service = self.resources()
        application = release.application_document(self.selection)
        application["status"] = {
            "sync": {"status": "Synced", "revision": self.selection.deployment_revision},
            "health": {"status": "Healthy"},
        }
        replica = {
            "metadata": {
                "uid": "owned-replica",
                "namespace": release.NAMESPACE,
                "ownerReferences": [
                    {"kind": "Deployment", "uid": "owned-deployment", "controller": True}
                ],
            }
        }
        responses = [application, stored_secret(), deployment, service, pods, replica]
        with (
            patch.object(release, "validate", return_value=self.selection),
            patch.object(health.cluster, "verify_local_docker"),
            patch.object(health.cluster, "verify_context"),
            patch.object(
                health.registry, "verify_registry_credentials", return_value=self.content
            ) as registry,
            patch.object(
                health.cluster, "run", side_effect=[json.dumps(value) for value in responses]
            ) as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(health.verify(), "native-search-fixture-abc")
            self.assertEqual(run.call_count, 6)
            self.assertTrue(all("get" in call.args[0] for call in run.call_args_list))
            registry.assert_called_once()
        replica["metadata"]["ownerReferences"][0]["uid"] = "foreign-deployment"
        with (
            patch.object(release, "validate", return_value=self.selection),
            patch.object(health.cluster, "verify_local_docker"),
            patch.object(health.cluster, "verify_context"),
            patch.object(health.registry, "verify_registry_credentials", return_value=self.content),
            patch.object(
                health.cluster, "run", side_effect=[json.dumps(value) for value in responses]
            ),
            self.assertRaisesRegex(ValueError, "not controlled"),
        ):
            health.verify()

    def test_foreign_cluster_stops_before_registry_or_resource_reads(self):
        with (
            patch.object(release, "validate", return_value=self.selection),
            patch.object(health.cluster, "verify_local_docker"),
            patch.object(
                health.cluster, "verify_context", side_effect=ValueError("foreign cluster")
            ),
            patch.object(health.cluster, "run") as run,
            self.assertRaisesRegex(ValueError, "foreign cluster"),
        ):
            health.verify()
        run.assert_not_called()

    def test_health_preview_never_contacts_registry_or_cluster(self):
        with patch.object(health, "verify") as verify, redirect_stdout(io.StringIO()):
            self.assertEqual(health.main([]), 0)
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
