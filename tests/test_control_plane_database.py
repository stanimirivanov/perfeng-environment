import base64
import json
import unittest
from unittest.mock import patch

from scripts import cluster
from scripts import control_plane_database as database

MIGRATION_PASSWORD = "a" * 43
RUNTIME_PASSWORD = "b" * 43


def stored_secret():
    document = database.secret_document(MIGRATION_PASSWORD, RUNTIME_PASSWORD)
    return {
        "type": document["type"],
        "immutable": document["immutable"],
        "metadata": document["metadata"],
        "data": {
            key: base64.b64encode(value.encode()).decode()
            for key, value in document["stringData"].items()
        },
    }


class ControlPlaneDatabaseTests(unittest.TestCase):
    def test_secret_round_trip_keeps_identities_separate(self) -> None:
        self.assertEqual(
            database.decode_secret(stored_secret()),
            (MIGRATION_PASSWORD, RUNTIME_PASSWORD),
        )
        self.assertNotEqual(MIGRATION_PASSWORD, RUNTIME_PASSWORD)

    def test_new_credentials_are_created_before_roles_through_private_stdin(self) -> None:
        role_states = ["", "perfeng_migration|t|f|f|f|f|f\nperfeng_runtime|t|f|f|f|f|f\n"]
        with (
            patch(
                "scripts.control_plane_database.cluster.run",
                side_effect=[
                    "",
                    role_states[0],
                    "",
                    "",
                    role_states[1],
                    "t|t|t|f\n",
                    "1\n",
                    "1\n",
                ],
            ) as run,
            patch(
                "scripts.control_plane_database.secrets.token_urlsafe",
                side_effect=[MIGRATION_PASSWORD, RUNTIME_PASSWORD],
            ),
        ):
            database.ensure_identities(cluster.ROOT, create=True)

        secret_create = run.call_args_list[2]
        role_create = run.call_args_list[3]
        self.assertEqual(secret_create.args[0][-3:], ["create", "-f", "-"])
        self.assertNotIn(MIGRATION_PASSWORD, str(secret_create.args[0]))
        self.assertIn(MIGRATION_PASSWORD.encode(), secret_create.kwargs["input_data"])
        self.assertNotIn(MIGRATION_PASSWORD, str(role_create.args[0]))
        self.assertIn(MIGRATION_PASSWORD.encode(), role_create.kwargs["input_data"])

    def test_existing_credentials_are_not_rotated(self) -> None:
        state = "perfeng_migration|t|f|f|f|f|f\nperfeng_runtime|t|f|f|f|f|f\n"
        with (
            patch(
                "scripts.control_plane_database.cluster.run",
                side_effect=[
                    json.dumps(stored_secret()),
                    state,
                    "",
                    state,
                    "t|t|t|f\n",
                    "1\n",
                    "1\n",
                ],
            ),
            patch("scripts.control_plane_database.secrets.token_urlsafe") as generate,
        ):
            database.ensure_identities(cluster.ROOT, create=True)
        generate.assert_not_called()

    def test_orphaned_roles_refuse_new_credentials(self) -> None:
        with (
            patch(
                "scripts.control_plane_database.cluster.run",
                side_effect=["", "perfeng_runtime|t|f|f|f|f|f\n"],
            ) as run,
            patch("scripts.control_plane_database.secrets.token_urlsafe") as generate,
            self.assertRaisesRegex(ValueError, "restore the Secret"),
        ):
            database.ensure_identities(cluster.ROOT, create=True)
        generate.assert_not_called()
        self.assertEqual(run.call_count, 2)

    def test_health_never_creates_missing_identity(self) -> None:
        with (
            patch("scripts.control_plane_database.cluster.run", side_effect=["", ""]),
            self.assertRaisesRegex(ValueError, "missing"),
        ):
            database.ensure_identities(cluster.ROOT, create=False)

    def test_health_only_reads_existing_roles_and_privileges(self) -> None:
        state = "perfeng_migration|t|f|f|f|f|f\nperfeng_runtime|t|f|f|f|f|f\n"
        with patch(
            "scripts.control_plane_database.cluster.run",
            side_effect=[json.dumps(stored_secret()), state, "t|t|t|f\n", "1\n", "1\n"],
        ) as run:
            database.ensure_identities(cluster.ROOT, create=False)

        inputs = [call.kwargs.get("input_data", b"") for call in run.call_args_list]
        self.assertFalse(any(b"GRANT" in value or b"REVOKE" in value for value in inputs))
        self.assertFalse(
            any(MIGRATION_PASSWORD in str(call.args[0]) for call in run.call_args_list)
        )

    def test_unsafe_existing_role_is_rejected_before_sql_write(self) -> None:
        with (
            patch(
                "scripts.control_plane_database.cluster.run",
                side_effect=[json.dumps(stored_secret()), "perfeng_runtime|t|t|f|f|f|f\n"],
            ) as run,
            self.assertRaisesRegex(ValueError, "unsafe attributes"),
        ):
            database.ensure_identities(cluster.ROOT, create=True)
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
