import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import yaml

from scripts import benchmark_authority as authority
from scripts import cluster
from tests.test_control_plane_migration import RELEASE


class BenchmarkAuthorityTests(unittest.TestCase):
    def test_project_source_destination_and_resource_expansion_is_rejected(self) -> None:
        directory = cluster.ROOT / "gitops/environments/local"
        project = yaml.safe_load(
            (directory / "benchmark-authority-project.yaml").read_text(encoding="utf-8")
        )
        application = yaml.safe_load(
            (directory / "benchmark-authority-application.yaml").read_text(encoding="utf-8")
        )
        for field, value in (
            ("sourceRepos", ["*"]),
            ("destinations", [{"server": "*", "namespace": "*"}]),
            ("clusterResourceWhitelist", [{"group": "*", "kind": "*"}]),
        ):
            changed = deepcopy(project)
            changed["spec"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                authority.validate_gitops(changed, application)

    def test_manifests_match_explicit_dedicated_cluster_ceiling(self) -> None:
        authority.validate_configuration()
        objects = authority.expected_objects()
        self.assertEqual(len(objects), 4)
        for value in objects:
            authority.require_object(value, value)
        cluster_role = objects[0]
        self.assertFalse(
            any("secrets" in rule.get("resources", []) for rule in cluster_role["rules"])
        )
        self.assertFalse(
            any(
                "delete" in rule["verbs"] and "namespaces" in rule.get("resources", [])
                for rule in cluster_role["rules"]
            )
        )

    def test_expanded_authority_aggregation_and_foreign_subjects_are_rejected(self) -> None:
        objects = authority.expected_objects()
        for index, field, value in [
            (0, "rules", [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]),
            (0, "aggregationRule", {}),
            (1, "subjects", [{"kind": "Group", "name": "system:authenticated"}]),
        ]:
            changed = deepcopy(objects[index])
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                authority.require_object(changed, objects[index])

    def test_health_reads_exact_cluster_and_authority_without_repair(self) -> None:
        manifest = {
            "entries": [{"benchmark": {"clusterId": "perfeng-local", "clusterUid": "reviewed-uid"}}]
        }
        directory = cluster.ROOT / "gitops/environments/local"
        gitops_objects = [
            yaml.safe_load(
                (directory / f"benchmark-authority-{kind}.yaml").read_text(encoding="utf-8")
            )
            for kind in ("project", "application")
        ]
        with (
            patch.object(
                authority.control_plane_runtime,
                "registry_files",
                return_value={"registry.json": json.dumps(manifest)},
            ),
            patch.object(
                authority.cluster,
                "run",
                side_effect=[
                    json.dumps({"metadata": {"uid": "reviewed-uid"}}),
                    *[json.dumps(value) for value in gitops_objects],
                    *[json.dumps(value) for value in authority.expected_objects()],
                ],
            ) as run,
        ):
            authority.verify(cluster.ROOT, RELEASE)
        self.assertEqual(run.call_count, 7)
        self.assertTrue(all("get" in call.args[0] for call in run.call_args_list))

    def test_changed_cluster_incarnation_refuses_before_reading_authority(self) -> None:
        with (
            patch.object(
                authority.control_plane_runtime,
                "registry_files",
                return_value={
                    "registry.json": json.dumps(
                        {
                            "entries": [
                                {
                                    "benchmark": {
                                        "clusterId": "perfeng-local",
                                        "clusterUid": "old-uid",
                                    }
                                }
                            ]
                        }
                    )
                },
            ),
            patch.object(
                authority.cluster, "run", return_value='{"metadata": {"uid": "new-uid"}}'
            ) as run,
            self.assertRaisesRegex(ValueError, "incarnation"),
        ):
            authority.verify(cluster.ROOT, RELEASE)
        run.assert_called_once()
