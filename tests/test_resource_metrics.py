from __future__ import annotations

import base64
import copy
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import yaml

from scripts import (
    infrastructure_lock,
)
from scripts import (
    resource_metrics_health as health,
)
from scripts import (
    resource_metrics_release as release,
)


def rendered(values: dict) -> list[dict]:
    """Minimal render fixture for the consumer contract, not a second deployable chart."""
    objects = [
        {
            "kind": kind,
            "metadata": {"name": name, **({"namespace": namespace} if namespace else {})},
        }
        for kind, name, namespace in (
            ("ServiceAccount", "metrics-server", "monitoring"),
            ("Deployment", "metrics-server", "monitoring"),
            ("Service", "metrics-server", "monitoring"),
            ("APIService", "v1beta1.metrics.k8s.io", None),
            ("ClusterRole", "substrate-metrics-server", None),
            ("ClusterRoleBinding", "substrate-metrics-server", None),
            ("ClusterRoleBinding", "substrate-metrics-server-auth-delegator", None),
            ("RoleBinding", "substrate-metrics-server-auth-reader", "kube-system"),
        )
    ]
    objects[1]["spec"] = {
        "template": {
            "spec": {
                "nodeSelector": values["nodeSelector"],
                "tolerations": values["tolerations"],
                "volumes": [{"name": "serving", "secret": {"secretName": release.SECRET}}],
                "containers": [
                    {
                        "image": release.IMAGE,
                        "args": [
                            "--kubelet-certificate-authority=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
                        ],
                    }
                ],
            }
        }
    }
    objects[3]["spec"] = {
        "caBundle": values["tls"]["caBundle"],
        "insecureSkipTLSVerify": False,
        "service": {"name": "metrics-server", "namespace": "monitoring", "port": 443},
    }
    return objects


class ResourceMetricsSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application = release.read_yaml(
            release.ROOT / release.DIRECTORY / "resource-metrics-application.yaml"
        )
        self.project = release.read_yaml(
            release.ROOT / release.DIRECTORY / "resource-metrics-project.yaml"
        )
        self.values = self.application["spec"]["source"]["helm"]["valuesObject"]

    def test_checked_in_configuration_is_bound_to_the_infrastructure_lock(self) -> None:
        self.assertEqual(release.validate_configuration(), self.application)
        self.assertEqual(
            self.application["spec"]["source"]["targetRevision"],
            infrastructure_lock.load_lock().revision,
        )
        self.assertEqual(set(self.values["tls"]), {"secretName", "caBundle"})

    def test_application_rejects_source_trust_placement_and_reconciliation_drift(self) -> None:
        for failure in (
            "branch",
            "path",
            "repository",
            "project",
            "secret",
            "extra-args",
            "node",
            "toleration",
            "prune",
            "self-heal",
            "finalizer",
        ):
            application = copy.deepcopy(self.application)
            source = application["spec"]["source"]
            values = source["helm"]["valuesObject"]
            if failure == "branch":
                source["targetRevision"] = "main"
            elif failure == "path":
                source["path"] = "clusters/local-kind"
            elif failure == "repository":
                source["repoURL"] = "https://github.com/foreign/source.git"
            elif failure == "project":
                application["spec"]["project"] = "perfeng-local"
            elif failure == "secret":
                values["tls"]["secretName"] = "foreign"
            elif failure == "extra-args":
                values["args"] = ["--kubelet-insecure-tls"]
            elif failure == "node":
                values["nodeSelector"]["workload"] = "sut"
            elif failure == "toleration":
                values["tolerations"] = []
            elif failure == "finalizer":
                application["metadata"]["finalizers"] = ["resources-finalizer.argocd.argoproj.io"]
            else:
                application["spec"]["syncPolicy"]["automated"][
                    "prune" if failure == "prune" else "selfHeal"
                ] = True
            with (
                self.subTest(failure=failure),
                patch("scripts.resource_metrics_release.read_yaml", return_value=application),
                self.assertRaisesRegex(ValueError, "Application"),
            ):
                release.validate_configuration()

    def test_metrics_project_cannot_broaden_perfeng_authority(self) -> None:
        for field, value in (
            ("sourceRepos", ["*"]),
            ("destinations", [{"server": "*", "namespace": "*"}]),
            ("clusterResourceWhitelist", [{"group": "*", "kind": "*"}]),
            ("namespaceResourceWhitelist", [{"group": "", "kind": "Secret"}]),
        ):
            project = copy.deepcopy(self.project)
            project["spec"][field] = value
            with (
                self.subTest(field=field),
                patch(
                    "scripts.resource_metrics_release.read_yaml",
                    side_effect=[self.application, project],
                ),
                self.assertRaisesRegex(ValueError, "AppProject"),
            ):
                release.validate_configuration()
        workload = release.read_yaml(release.ROOT / release.DIRECTORY / "project.yaml")
        self.assertNotIn(
            infrastructure_lock.INFRASTRUCTURE_REPOSITORY, workload["spec"]["sourceRepos"]
        )
        self.assertNotIn("clusterResourceWhitelist", workload["spec"])

    def test_ca_rejects_synthetic_private_or_ambiguous_material(self) -> None:
        for value in (
            None,
            "",
            "not-a-ca",
            base64.b64encode(
                b"-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n"
            ).decode(),
            self.values["tls"]["caBundle"] + "\n",
            base64.b64encode(base64.b64decode(self.values["tls"]["caBundle"]) * 2).decode(),
        ):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                release.public_ca(value)
        self.assertEqual(
            release.public_ca(self.values["tls"]["caBundle"]), self.values["tls"]["caBundle"]
        )

    def test_render_contract_rejects_namespace_tls_image_and_scope_drift(self) -> None:
        release.validate_rendered(rendered(self.values), self.values)
        for failure in (
            "extra-secret",
            "namespace",
            "ca",
            "aggregation-bypass",
            "scraper-bypass",
            "image",
            "node",
        ):
            objects = rendered(copy.deepcopy(self.values))
            pod = objects[1]["spec"]["template"]["spec"]
            if failure == "extra-secret":
                objects.append({"kind": "Secret", "metadata": {"name": "forbidden"}})
            elif failure == "namespace":
                objects[-1]["metadata"]["namespace"] = "foreign"
            elif failure == "ca":
                objects[3]["spec"]["caBundle"] = "foreign"
            elif failure == "aggregation-bypass":
                objects[3]["spec"]["insecureSkipTLSVerify"] = True
            elif failure == "scraper-bypass":
                pod["containers"][0]["args"].append("--kubelet-insecure-tls")
            elif failure == "image":
                pod["containers"][0]["image"] = "metrics-server:latest"
            else:
                pod["nodeSelector"] = {"workload": "sut"}
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                release.validate_rendered(objects, self.values)

    def test_pinned_validation_reads_git_objects_and_passes_values_only_through_stdin(self) -> None:
        lock = infrastructure_lock.load_lock()
        reads = []

        def read(path: str) -> bytes:
            reads.append(path)
            if path.endswith("release.json"):
                return json.dumps(
                    {"image": release.IMAGE, "version": "0.9.0", "chart": release.CHART}
                ).encode()
            if path.endswith("Chart.yaml"):
                return b'version: "1.0.0"\nappVersion: "0.9.0"\n'
            return b"# selected Git bytes only\n"

        with (
            patch("scripts.infrastructure_release.validate", return_value=lock),
            patch("scripts.infrastructure_release.revision_reader", return_value=read) as reader,
            patch(
                "scripts.resource_metrics_release.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 0, yaml.safe_dump_all(rendered(self.values))
                ),
            ) as run,
            patch("scripts.resource_metrics_release.validate_rendered") as rendered_check,
        ):
            release.validate()
        self.assertEqual(reader.call_args.args[1], lock.revision)
        self.assertEqual(len(reads), 6)
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertNotIn(self.values["tls"]["caBundle"], call.args[0])
            self.assertEqual(json.loads(call.kwargs["input"]), self.values)
            self.assertEqual(call.kwargs["timeout"], 60)
        rendered_check.assert_called_once()

    def test_provider_failure_or_live_selection_drift_never_repairs_resources(self) -> None:
        spec = rendered(self.values)[3]
        for failure in ("application", "project", "ca", "provider"):
            application = copy.deepcopy(self.application)
            project = copy.deepcopy(self.project)
            api = copy.deepcopy(spec)
            if failure == "application":
                application["spec"]["source"]["targetRevision"] = "main"
            elif failure == "project":
                project["spec"]["sourceRepos"] = ["*"]
            elif failure == "ca":
                api["spec"]["caBundle"] = "foreign"
            with (
                self.subTest(failure=failure),
                patch("scripts.cluster.verify_context"),
                patch(
                    "scripts.cluster.run",
                    side_effect=[json.dumps(application), json.dumps(project), json.dumps(api)],
                ) as run,
                patch("scripts.infrastructure_runtime.invoke_provider", return_value=1) as provider,
                self.assertRaises(ValueError),
            ):
                health.verify()
            if failure == "provider":
                provider.assert_called_once_with("metrics", "health", execute=True)
            else:
                provider.assert_not_called()
            self.assertTrue(all("get" in call.args[0] for call in run.call_args_list))
            self.assertTrue(
                all("--request-timeout=10s" in call.args[0] for call in run.call_args_list)
            )

    def test_live_success_delegates_only_provider_health_and_reads_no_secret(self) -> None:
        with (
            patch("scripts.cluster.verify_context"),
            patch(
                "scripts.cluster.run",
                side_effect=[
                    json.dumps(self.application),
                    json.dumps(self.project),
                    json.dumps(rendered(self.values)[3]),
                ],
            ) as run,
            patch("scripts.infrastructure_runtime.invoke_provider", return_value=0) as provider,
        ):
            health.verify()
        provider.assert_called_once_with("metrics", "health", execute=True)
        self.assertFalse(any("secret" in call.args[0] for call in run.call_args_list))

    def test_preview_cannot_access_provider_or_cluster(self) -> None:
        with (
            patch("sys.argv", ["resource_metrics_health"]),
            patch("scripts.cluster.run") as run,
            patch("scripts.infrastructure_runtime.invoke_provider") as provider,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(health.main(), 0)
        run.assert_not_called()
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
