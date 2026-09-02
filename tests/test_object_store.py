import base64
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import cluster
from scripts import object_store as store

PROBE = "a" * 32


def existing_secret():
    access, secret = "a" * 32, "b" * 64
    data = {
        "access-key": access,
        "secret-key": secret,
        "s3.json": json.dumps(store.s3_config(access, secret)),
    }
    return {
        "type": "Opaque",
        "immutable": True,
        "metadata": {"labels": {"app.kubernetes.io/managed-by": store.OWNER}},
        "data": {key: base64.b64encode(value.encode()).decode() for key, value in data.items()},
    }


class ObjectStoreTests(unittest.TestCase):
    def test_preview_does_not_execute_or_generate_credentials(self):
        for action in ["deploy", "health", "smoke", "verify"]:
            args = ["object_store.py", action]
            if action == "verify":
                args += ["--probe-id", PROBE]
            with (
                patch("sys.argv", args),
                patch("scripts.object_store.cluster.run") as run,
                patch("scripts.object_store.secrets.token_hex") as random,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(store.main(), 0)
                run.assert_not_called()
                random.assert_not_called()

    def test_probe_id_validation_blocks_shell_injection(self):
        for probe in ["", "../data", "$(id)", "A" * 32, "a" * 33]:
            with self.assertRaises(ValueError):
                store.client_script("verify", probe)

    def test_verify_requires_id_and_smoke_does_not_accept_one(self):
        for args in [["verify"], ["smoke", "--probe-id", PROBE]]:
            with (
                patch("sys.argv", ["object_store.py", *args]),
                patch("scripts.object_store.execute") as execute,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(store.main(), 1)
                execute.assert_not_called()

    def test_secret_created_on_stdin_and_never_printed(self):
        with (
            patch("scripts.object_store.cluster.run", side_effect=["", "", "", ""]) as run,
            patch("scripts.object_store.secrets.token_hex", side_effect=["a" * 32, "b" * 64]),
            redirect_stdout(io.StringIO()) as output,
        ):
            store.ensure_secret(cluster.ROOT, create=True)
            call = run.call_args
            self.assertEqual(call.args[0][-3:], ["create", "-f", "-"])
            self.assertNotIn("b" * 64, str(call.args))
            self.assertNotIn("b" * 64, output.getvalue())
            value = json.loads(call.kwargs["input_data"])
            self.assertTrue(value["immutable"])
            self.assertEqual(
                json.loads(value["stringData"]["s3.json"]), store.s3_config("a" * 32, "b" * 64)
            )

    def test_existing_secret_is_reused_without_rotation(self):
        with (
            patch(
                "scripts.object_store.cluster.run", return_value=json.dumps(existing_secret())
            ) as run,
            patch("scripts.object_store.secrets.token_hex") as random,
            redirect_stdout(io.StringIO()),
        ):
            store.ensure_secret(cluster.ROOT, create=True)
            self.assertEqual(run.call_count, 1)
            random.assert_not_called()

    def test_orphaned_resources_refuse_new_credentials(self):
        for results in [["", "pvc/data-seaweedfs-0"], ["", "", "statefulset/seaweedfs"]]:
            with (
                patch("scripts.object_store.cluster.run", side_effect=results) as run,
                patch("scripts.object_store.secrets.token_hex") as random,
            ):
                with self.assertRaisesRegex(ValueError, "restore"):
                    store.ensure_secret(cluster.ROOT, create=True)
                random.assert_not_called()
                self.assertFalse(any("create" in call.args[0] for call in run.call_args_list))

    def test_invalid_or_anonymous_config_refused(self):
        cases = [existing_secret() for _ in range(5)]
        cases[0]["immutable"] = False
        cases[1]["metadata"] = {}
        cases[2]["data"]["secret-key"] = "invalid"
        cases[3]["data"]["s3.json"] = base64.b64encode(b'{"identities": []}').decode()
        cases[4]["data"]["access-key"] = base64.b64encode(b"short").decode()
        for value in cases:
            with self.assertRaises(ValueError):
                store.validate_secret(value)

    def test_health_never_creates_missing_secret(self):
        with patch("scripts.object_store.cluster.run", return_value="") as run:
            with self.assertRaises(ValueError):
                store.ensure_secret(cluster.ROOT, create=False)
            self.assertEqual(run.call_count, 1)

    def test_client_checks_are_separate_and_no_automatic_object_deletion(self):
        for action in ["deploy", "health", "smoke", "verify"]:
            script = store.client_script(action, PROBE)
            self.assertNotIn("delete-object", script)
            self.assertEqual("create-bucket" in script, action == "deploy")
            self.assertEqual("put-object" in script, action == "smoke")
            if action in {"smoke", "verify"}:
                self.assertIn("--no-sign-request", script)
                self.assertIn(store.CHECKSUM, script)
                self.assertIn(f"smoke/{PROBE}.bin", script)
                self.assertIn("sha256sum /tmp/download", script)
            if action == "smoke":
                self.assertIn('--if-none-match "*"', script)

    def test_job_has_scoped_credentials_and_bounded_lifetime(self):
        job = store.client_job("smoke", PROBE, "s3-test", cluster.ROOT)
        self.assertEqual(job["metadata"]["namespace"], "perf-platform")
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 180)
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["nodeSelector"], {"workload": "control-plane"})
        container = pod["containers"][0]
        self.assertIn("@sha256:", container["image"])
        env = {entry["name"]: entry for entry in container["env"]}
        self.assertEqual(
            env["AWS_SECRET_ACCESS_KEY"]["valueFrom"]["secretKeyRef"]["name"], store.SECRET
        )
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])

    def test_unpinned_client_image_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "charts/seaweedfs").mkdir(parents=True)
            (root / "charts/seaweedfs/values.yaml").write_text("clientImage: amazon/aws-cli:latest")
            with self.assertRaises(ValueError):
                store.client_job("health", PROBE, "test", root)

    def test_foreign_context_blocks_all_cluster_commands(self):
        with (
            patch("scripts.object_store.cluster.verify_local_docker"),
            patch("scripts.object_store.cluster.verify_context", side_effect=ValueError("foreign")),
            patch("scripts.object_store.cluster.run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "foreign"):
                store.execute("deploy", PROBE)
            run.assert_not_called()

    def test_successful_health_and_failed_job_are_distinct(self):
        for failed in [False, True]:
            responses = [
                "namespace/perf-platform",
                "ready",
                '{"status":{"phase":"Bound"}}',
                "",
                ValueError("timeout") if failed else "",
            ]
            with (
                patch("scripts.object_store.cluster.verify_local_docker"),
                patch("scripts.object_store.cluster.verify_context"),
                patch("scripts.object_store.ensure_secret"),
                patch("scripts.object_store.cluster.run", side_effect=responses),
                redirect_stdout(io.StringIO()) as output,
            ):
                if failed:
                    with self.assertRaisesRegex(ValueError, "inspect Job"):
                        store.execute("health", PROBE)
                    self.assertNotIn("completed:", output.getvalue())
                else:
                    store.execute("health", PROBE)
                    self.assertIn("completed: health", output.getvalue())


if __name__ == "__main__":
    unittest.main()
