import base64
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import cluster, postgres


def existing_secret():
    return {
        "type": "Opaque",
        "immutable": True,
        "metadata": {"labels": {"app.kubernetes.io/managed-by": postgres.OWNER}},
        "data": {"password": base64.b64encode(b"x" * 32).decode("ascii")},
    }


class PostgresTests(unittest.TestCase):
    def test_preview_has_no_execution_or_secret_generation(self):
        with (
            patch("sys.argv", ["postgres.py", "deploy"]),
            patch("scripts.postgres.cluster.run") as run,
            patch("scripts.postgres.secrets.token_urlsafe") as generate,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(postgres.main(), 0)
            run.assert_not_called()
            generate.assert_not_called()
            self.assertIn("Preview only", output.getvalue())

    def test_all_commands_pin_local_context_and_kubeconfig(self):
        for action in ["deploy", "health"]:
            for command in postgres.commands(action):
                self.assertIn(str(cluster.kubeconfig(cluster.ROOT)), command)
                self.assertIn(cluster.CONTEXT, command)
                self.assertIn(postgres.NAMESPACE, command)
                self.assertNotIn("delete", command)

    def test_new_secret_is_private_and_create_only(self):
        with (
            patch("scripts.postgres.cluster.run", side_effect=["", "", "", ""]) as run,
            patch("scripts.postgres.secrets.token_urlsafe", return_value="private-password") as gen,
            redirect_stdout(io.StringIO()) as output,
        ):
            postgres.ensure_secret(cluster.ROOT, create=True)
            gen.assert_called_once_with(32)
            created = run.call_args
            self.assertEqual(created.args[0][-3:], ["create", "-f", "-"])
            self.assertNotIn("private-password", str(created.args))
            document = json.loads(created.kwargs["input_data"])
            self.assertTrue(document["immutable"])
            self.assertEqual(document["stringData"]["password"], "private-password")
            self.assertNotIn("private-password", output.getvalue())

    def test_existing_secret_is_not_rotated(self):
        with (
            patch(
                "scripts.postgres.cluster.run", return_value=json.dumps(existing_secret())
            ) as run,
            patch("scripts.postgres.secrets.token_urlsafe") as generate,
            redirect_stdout(io.StringIO()),
        ):
            postgres.ensure_secret(cluster.ROOT, create=True)
            self.assertEqual(run.call_count, 1)
            generate.assert_not_called()

    def test_concurrent_secret_creation_failure_is_not_overwritten(self):
        with (
            patch(
                "scripts.postgres.cluster.run", side_effect=["", "", "", ValueError("exists")]
            ) as run,
            self.assertRaisesRegex(ValueError, "exists"),
        ):
            postgres.ensure_secret(cluster.ROOT, create=True)
        self.assertEqual(run.call_count, 4)
        self.assertFalse(any("apply" in call.args[0] for call in run.call_args_list))

    def test_orphaned_storage_or_statefulset_refuses_new_password(self):
        for responses in [["", "pvc/data-postgres-0"], ["", "", "statefulset/postgres"]]:
            with (
                patch("scripts.postgres.cluster.run", side_effect=responses) as run,
                patch("scripts.postgres.secrets.token_urlsafe") as generate,
                self.assertRaisesRegex(ValueError, "restore the Secret"),
            ):
                postgres.ensure_secret(cluster.ROOT, create=True)
            generate.assert_not_called()
            self.assertFalse(any("create" in call.args[0] for call in run.call_args_list))

    def test_unowned_mutable_or_invalid_secret_is_rejected(self):
        cases = [existing_secret() for _ in range(5)]
        cases[0]["metadata"] = {}
        cases[1]["immutable"] = False
        cases[2]["data"] = {}
        cases[3]["data"]["password"] = "not-base64"
        cases[4]["data"]["password"] = base64.b64encode(b"short").decode("ascii")
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                postgres.validate_secret(value)

    def test_missing_secret_health_never_creates(self):
        with patch("scripts.postgres.cluster.run", return_value="") as run:
            with self.assertRaisesRegex(ValueError, "missing"):
                postgres.ensure_secret(cluster.ROOT, create=False)
            self.assertEqual(run.call_count, 1)

    def test_foreign_context_stops_before_cluster_access(self):
        with (
            patch("scripts.postgres.cluster.verify_local_docker"),
            patch("scripts.postgres.cluster.verify_context", side_effect=ValueError("foreign")),
            patch("scripts.postgres.cluster.run") as run,
            redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(ValueError, "foreign"),
        ):
            postgres.execute("deploy")
        run.assert_not_called()

    def test_wrong_storage_class_stops_before_secret_creation(self):
        with (
            patch("scripts.postgres.cluster.verify_local_docker"),
            patch("scripts.postgres.cluster.verify_context"),
            patch("scripts.postgres.cluster.run", side_effect=["namespace/perf-platform", "{}"]),
            patch("scripts.postgres.ensure_secret") as ensure,
            redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(ValueError, "StorageClass"),
        ):
            postgres.execute("deploy")
        ensure.assert_not_called()

    def test_health_requires_bound_volume_and_authenticated_query(self):
        for phase, result, success in [
            ("Bound", "1", True),
            ("Pending", "1", False),
            ("Bound", "", False),
        ]:
            with (
                patch("scripts.postgres.cluster.verify_local_docker"),
                patch("scripts.postgres.cluster.verify_context"),
                patch("scripts.postgres.ensure_secret"),
                patch(
                    "scripts.postgres.cluster.run",
                    side_effect=[
                        "namespace/perf-platform",
                        "rollout complete",
                        json.dumps({"status": {"phase": phase}}),
                        result,
                    ],
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                if success:
                    postgres.execute("health")
                    self.assertIn("completed: health", output.getvalue())
                else:
                    with self.assertRaises(ValueError):
                        postgres.execute("health")
                    self.assertNotIn("completed: health", output.getvalue())


if __name__ == "__main__":
    unittest.main()
