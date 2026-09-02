import base64
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import storage_access as access

BOOT = {"access-key": "a" * 32, "secret-key": "b" * 64}
UPLOAD = {"access-key": "c" * 32, "secret-key": "d" * 64}


def secret(name, data):
    value = access.secret_document(name, data)
    value.pop("stringData")
    value["data"] = {key: base64.b64encode(text.encode()).decode() for key, text in data.items()}
    return json.dumps(value)


def existing():
    return [
        secret(
            access.store.SECRET,
            {
                **BOOT,
                "s3.json": json.dumps(
                    access.store.s3_config(BOOT["access-key"], BOOT["secret-key"])
                ),
            },
        ),
        secret(access.UPLOADER_SECRET, UPLOAD),
        secret(access.SERVER_SECRET, {"s3.json": json.dumps(access.server_config(BOOT, UPLOAD))}),
    ]


ACTIVE = json.dumps(
    {
        "spec": {
            "template": {
                "spec": {
                    "volumes": [
                        {"secret": {"secretName": access.SERVER_SECRET}},
                    ]
                }
            }
        }
    }
)


class StorageAccessTests(unittest.TestCase):
    def test_policy_is_object_get_put_only_under_runs(self):
        statement = access.policy()["Statement"]
        self.assertEqual(
            statement,
            [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject"],
                    "Resource": ["arn:aws:s3:::perfeng-artifacts/runs/*"],
                }
            ],
        )
        config = access.server_config(BOOT, UPLOAD)
        self.assertNotIn("actions", config["identities"][1])
        self.assertEqual(
            config["identities"][0],
            access.store.s3_config(BOOT["access-key"], BOOT["secret-key"])["identities"][0],
        )

    def test_migration_creates_pair_on_private_stdin_without_rotation(self):
        with (
            patch.object(
                access.cluster, "run", side_effect=[existing()[0], "", "", "", "", ""]
            ) as run,
            patch.object(access.secrets, "token_hex", side_effect=list(UPLOAD.values())),
            redirect_stdout(io.StringIO()) as output,
        ):
            access.ensure_credentials(access.cluster.ROOT, create=True)
            created = [
                json.loads(c.kwargs["input_data"])
                for c in run.call_args_list
                if "input_data" in c.kwargs
            ]
            self.assertEqual(
                [v["metadata"]["name"] for v in created],
                [access.UPLOADER_SECRET, access.SERVER_SECRET],
            )
            self.assertTrue(all(v["immutable"] for v in created))
            self.assertNotIn(BOOT["secret-key"], output.getvalue())
            self.assertNotIn(UPLOAD["secret-key"], output.getvalue())
            self.assertNotIn(BOOT["secret-key"], str([c.args for c in run.call_args_list]))

    def test_complete_pair_reused_without_writes_or_randomness(self):
        with (
            patch.object(access.cluster, "run", side_effect=[*existing(), ACTIVE]) as run,
            patch.object(access.secrets, "token_hex") as random,
            redirect_stdout(io.StringIO()),
        ):
            access.ensure_credentials(access.cluster.ROOT, create=False)
            random.assert_not_called()
            self.assertFalse(any("create" in c.args[0] for c in run.call_args_list))

    def test_partial_creation_reuses_uploader(self):
        with (
            patch.object(access.cluster, "run", side_effect=[*existing()[:2], "", ""]) as run,
            patch.object(access.secrets, "token_hex") as random,
            redirect_stdout(io.StringIO()),
        ):
            access.ensure_credentials(access.cluster.ROOT, create=True)
            random.assert_not_called()
            created = json.loads(run.call_args.kwargs["input_data"])
            self.assertEqual(created["metadata"]["name"], access.SERVER_SECRET)

    def test_mismatches_and_missing_credentials_fail_before_writes(self):
        altered = json.loads(existing()[2])
        altered["data"]["s3.json"] = base64.b64encode(b'{"identities":[]}').decode()
        for responses, create in [
            ([existing()[0], "", existing()[2]], True),
            ([existing()[0], "", "", ACTIVE], True),
            ([existing()[0], "", ""], False),
            ([*existing()[:2], json.dumps(altered)], True),
            ([*existing(), "{}"], False),
        ]:
            with (
                patch.object(access.cluster, "run", side_effect=responses) as run,
                patch.object(access.secrets, "token_hex") as random,
            ):
                with self.assertRaises(ValueError):
                    access.ensure_credentials(access.cluster.ROOT, create=create)
                random.assert_not_called()
                self.assertFalse(any("create" in c.args[0] for c in run.call_args_list))

    def test_unowned_mutable_or_extra_secret_data_rejected(self):
        for field in ["immutable", "owner", "extra", "namespace"]:
            value = json.loads(existing()[1])
            if field == "immutable":
                value["immutable"] = False
            elif field == "owner":
                value["metadata"]["labels"] = {}
            elif field == "namespace":
                value["metadata"]["namespace"] = "other"
            else:
                value["data"]["s3.json"] = base64.b64encode(b"{}").decode()
            with self.assertRaises(ValueError):
                access.decode(value, access.UPLOADER_SECRET, {"access-key", "secret-key"})

    def test_preview_is_offline_and_verifier_targets_only_its_new_probe(self):
        with (
            patch("sys.argv", ["storage_access.py"]),
            patch.object(access.cluster, "run") as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(access.main(), 0)
            run.assert_not_called()
        document = access.verification_job("e" * 32, access.cluster.ROOT)
        self.assertNotIn('"perfeng-s3-auth"', json.dumps(document))
        script = document["spec"]["template"]["spec"]["containers"][0]["command"][-1]
        self.assertIn(
            'denied delete-object --bucket "perfeng-artifacts" --key "runs/access-check/'
            + "e" * 32,
            script,
        )
        self.assertIn("AccessDenied|Forbidden|403", script)
        self.assertNotIn("delete-bucket", script)
        self.assertNotIn("put-bucket-policy", script)
        with self.assertRaises(ValueError):
            access.verification_job("$(id)", access.cluster.ROOT)

    def test_foreign_cluster_blocks_secret_reads_and_mutations(self):
        with (
            patch.object(access.cluster, "verify_local_docker"),
            patch.object(access.cluster, "verify_context", side_effect=ValueError("foreign")),
            patch.object(access.cluster, "run") as run,
        ):
            with self.assertRaises(ValueError):
                access.execute()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
