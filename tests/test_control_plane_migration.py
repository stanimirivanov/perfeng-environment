import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from scripts import cluster
from scripts import control_plane_migration as migration
from scripts.control_plane_release import DeploymentLock

RELEASE = DeploymentLock(
    image="ghcr.io/stanimirivanov/perfeng-control-plane@sha256:" + "a" * 64,
    source_revision="b" * 40,
    deployment_revision="c" * 40,
    chart_path="charts/control-plane",
    chart_version="0.3.0",
    bundle_path="bundles/local-kind-windows/1.1.0",
    bundle_version="1.1.0",
    contracts_version="0.13.0",
    runtime_config_map="perfeng-control-plane-runtime-" + "1" * 12,
    registry_config_map="perfeng-runtime-registry-" + "2" * 12,
)


class ControlPlaneMigrationTests(unittest.TestCase):
    def test_job_uses_only_pinned_inputs_and_migration_identity(self) -> None:
        job = migration.job_document(RELEASE)
        pod = job["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertEqual(container["image"], RELEASE.image)
        self.assertEqual(container["command"], ["/usr/local/bin/migrate"])
        self.assertEqual(
            container["env"][0]["valueFrom"]["secretKeyRef"],
            {"name": "perfeng-control-plane-database", "key": "migration-url"},
        )
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["imagePullSecrets"], [{"name": "perfeng-control-plane-registry"}])

    def test_changed_image_or_credential_source_is_rejected(self) -> None:
        for change in ("image", "secret"):
            job = deepcopy(migration.job_document(RELEASE))
            if change == "image":
                job["spec"]["template"]["spec"]["containers"][0]["image"] += "changed"
            else:
                job["spec"]["template"]["spec"]["containers"][0]["env"][0]["valueFrom"][
                    "secretKeyRef"
                ]["name"] = "other"
            with self.subTest(change=change), self.assertRaises(ValueError):
                migration.validate_job(job, RELEASE)

    def test_known_kubernetes_container_defaults_are_accepted(self) -> None:
        job = migration.job_document(RELEASE)
        container = job["spec"]["template"]["spec"]["containers"][0]
        container["terminationMessagePath"] = "/dev/termination-log"
        container["terminationMessagePolicy"] = "File"
        migration.validate_job(job, RELEASE)

    def test_unowned_container_arguments_are_rejected(self) -> None:
        job = migration.job_document(RELEASE)
        job["spec"]["template"]["spec"]["containers"][0]["args"] = ["unexpected"]
        with self.assertRaises(ValueError):
            migration.validate_job(job, RELEASE)

    def test_runtime_grants_are_exact_and_transactional(self) -> None:
        with patch("scripts.control_plane_migration.control_plane_database.psql") as psql:
            migration.grant_runtime(cluster.ROOT)
        sql = psql.call_args.args[1]
        self.assertTrue(sql.startswith(b"BEGIN;") and sql.endswith(b"COMMIT;\n"))
        self.assertIn(b"REVOKE ALL", sql)
        self.assertNotIn(b"GRANT ALL", sql)
        self.assertNotIn(b"DELETE", sql)
        self.assertIn(
            b"GRANT SELECT, INSERT, UPDATE ON perfeng_control.benchmark_target_reservations TO perfeng_runtime;",
            sql,
        )
        self.assertEqual(migration.EXPECTED_RUNTIME_PRIVILEGES[-4:], (True, True, True, False))

    def test_deploy_refuses_before_database_or_job_effects_when_not_quiescent(self) -> None:
        with (
            patch("scripts.control_plane_migration.validate_release", return_value=RELEASE),
            patch("scripts.control_plane_migration.cluster.verify_local_docker"),
            patch("scripts.control_plane_migration.cluster.verify_context"),
            patch(
                "scripts.control_plane_migration.control_plane_maintenance.verify_quiescent",
                side_effect=ValueError("not quiescent"),
            ),
            patch(
                "scripts.control_plane_migration.control_plane_database.ensure_identities"
            ) as identities,
            patch("scripts.control_plane_migration.cluster.run") as run,
            patch("scripts.control_plane_migration.grant_runtime") as grant,
            self.assertRaisesRegex(ValueError, "not quiescent"),
        ):
            migration.execute("deploy")
        identities.assert_not_called()
        run.assert_not_called()
        grant.assert_not_called()

    def test_runtime_privilege_verification_accepts_only_exact_result(self) -> None:
        result = "|".join("t" if value else "f" for value in migration.EXPECTED_RUNTIME_PRIVILEGES)
        with patch(
            "scripts.control_plane_migration.control_plane_database.psql",
            return_value=result,
        ):
            migration.verify_runtime(cluster.ROOT)
        with (
            patch(
                "scripts.control_plane_migration.control_plane_database.psql",
                return_value=result.replace("|f", "|t", 1),
            ),
            self.assertRaises(ValueError),
        ):
            migration.verify_runtime(cluster.ROOT)

        with (
            patch(
                "scripts.control_plane_migration.control_plane_database.psql",
                return_value=result[:-1] + "t",
            ),
            self.assertRaises(ValueError),
        ):
            migration.verify_runtime(cluster.ROOT)

    def test_health_reuses_job_and_never_applies_grants(self) -> None:
        current = migration.job_document(RELEASE)
        completed = deepcopy(current)
        completed["status"] = {"succeeded": 1}
        with (
            patch("scripts.control_plane_migration.validate_release", return_value=RELEASE),
            patch("scripts.control_plane_migration.cluster.verify_local_docker"),
            patch("scripts.control_plane_migration.cluster.verify_context"),
            patch("scripts.control_plane_migration.control_plane_database.ensure_identities"),
            patch("scripts.control_plane_migration.control_plane_registry.ensure_secret"),
            patch(
                "scripts.control_plane_migration.cluster.run",
                side_effect=[json.dumps(current), "complete", json.dumps(completed)],
            ) as run,
            patch("scripts.control_plane_migration.grant_runtime") as grant,
            patch("scripts.control_plane_migration.verify_runtime") as verify,
        ):
            migration.execute("health")

        grant.assert_not_called()
        verify.assert_called_once_with(cluster.ROOT)
        self.assertFalse(any("create" in call.args[0] for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
