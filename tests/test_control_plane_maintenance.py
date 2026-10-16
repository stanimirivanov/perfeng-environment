import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from scripts import cluster
from scripts import control_plane_maintenance as maintenance


def application(name: str, *, enabled: bool = False) -> dict:
    repository, path = maintenance.REPOSITORIES[name]
    return {
        "metadata": {
            "uid": name,
            "resourceVersion": "1",
            "name": name,
            "namespace": "argocd",
            "labels": {"app.kubernetes.io/managed-by": "perfeng-environment"},
        },
        "spec": {
            "source": {"repoURL": repository, "path": path},
            "syncPolicy": {"automated": {"enabled": enabled}},
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": "argocd" if name == maintenance.APPLICATIONS[0] else "perf-platform",
            },
        },
    }


def deployment() -> dict:
    return {
        "metadata": {
            "name": "control-plane",
            "namespace": "perf-platform",
            "uid": "deployment-uid",
            "resourceVersion": "3",
            "labels": {
                "app.kubernetes.io/name": "perfeng-control-plane",
                "app.kubernetes.io/instance": "control-plane",
            },
        },
        "spec": {"replicas": 0, "template": {"spec": {"serviceAccountName": "control-plane"}}},
        "status": {"replicas": 0},
    }


class MaintenanceTests(unittest.TestCase):
    def test_wrong_cluster_refuses_before_any_pause_or_scale(self) -> None:
        with (
            patch.object(maintenance.cluster, "verify_local_docker"),
            patch.object(maintenance.cluster, "verify_context"),
            patch.object(maintenance, "validate_release"),
            patch.object(
                maintenance.benchmark_authority,
                "verify_target",
                side_effect=ValueError("wrong incarnation"),
            ),
            patch.object(maintenance, "patch") as write,
            patch.object(maintenance, "read") as read,
            self.assertRaisesRegex(ValueError, "wrong incarnation"),
        ):
            maintenance.pause()
        write.assert_not_called()
        read.assert_not_called()

    def test_preview_is_offline(self) -> None:
        with (
            patch("sys.argv", ["control_plane_maintenance", "pause"]),
            patch.object(maintenance, "pause") as pause,
        ):
            self.assertEqual(maintenance.main(), 0)
        pause.assert_not_called()

    def test_quiescent_and_fresh_environments_are_read_only(self) -> None:
        for values in (
            [application(name) for name in maintenance.APPLICATIONS] + [deployment()],
            [None, None, None],
        ):
            with (
                patch.object(maintenance, "read", side_effect=values),
                patch.object(maintenance.cluster, "run", return_value='{"items": []}') as run,
            ):
                maintenance.verify_quiescent(cluster.ROOT)
            run.assert_called_once()
            self.assertIn("get", run.call_args.args[0])

    def test_active_sync_foreign_source_and_enabled_reconciler_refuse(self) -> None:
        original = application(maintenance.APPLICATIONS[0])
        cases = []
        for change in ("operation", "source", "enabled", "owner", "running"):
            value = deepcopy(original)
            if change == "operation":
                value["operation"] = {"sync": {}}
            elif change == "source":
                value["spec"]["source"]["repoURL"] = "foreign"
            elif change == "enabled":
                value["spec"]["syncPolicy"]["automated"]["enabled"] = True
            elif change == "owner":
                value["metadata"]["ownerReferences"] = [{"kind": "ApplicationSet"}]
            else:
                value["status"] = {"operationState": {"phase": "Running"}}
            cases.append(value)
        for value in cases:
            with (
                self.subTest(value=value),
                patch.object(maintenance, "read", return_value=value),
                self.assertRaises(ValueError),
            ):
                maintenance.verify_quiescent(cluster.ROOT)

    def test_replicas_or_remaining_pods_block_migration(self) -> None:
        running = deployment()
        running["status"]["replicas"] = 1
        for dep, pods in [(running, []), (deployment(), [{"metadata": {"name": "old-pod"}}])]:
            with (
                patch.object(maintenance, "read", side_effect=[None, None, dep]),
                patch.object(maintenance.cluster, "run", return_value=json.dumps({"items": pods})),
                self.assertRaises(ValueError),
            ):
                maintenance.verify_quiescent(cluster.ROOT)

    def test_pause_orders_reconcilers_before_scale_with_concurrency_tests(self) -> None:
        with (
            patch.object(maintenance.cluster, "verify_local_docker"),
            patch.object(maintenance.cluster, "verify_context"),
            patch.object(maintenance, "validate_release"),
            patch.object(maintenance.benchmark_authority, "verify_target"),
            patch.object(
                maintenance,
                "read",
                side_effect=[application(name, enabled=True) for name in maintenance.APPLICATIONS]
                + [deployment()],
            ),
            patch.object(
                maintenance.cluster,
                "run",
                side_effect=["", "", "", '{"items": [{"metadata": {"name": "old-pod"}}]}', ""],
            ) as run,
            patch.object(maintenance, "verify_quiescent") as verify,
        ):
            maintenance.pause()
        patches = [call.args[0] for call in run.call_args_list[:3]]
        self.assertEqual(
            [command[command.index("patch") + 2] for command in patches],
            [*maintenance.APPLICATIONS, "control-plane"],
        )
        for command in patches:
            operations = json.loads(command[-1])
            self.assertEqual(
                [item["path"] for item in operations[:2]],
                ["/metadata/uid", "/metadata/resourceVersion"],
            )
        self.assertIn("--timeout=180s", run.call_args.args[0])
        verify.assert_called_once_with(cluster.ROOT)
