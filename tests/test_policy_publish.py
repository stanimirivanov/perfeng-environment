import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import cluster, policy_publish
from scripts import object_store as store

POLICY_ID = "pod-startup-observe"
VERSION = "1.0.0"


def policy_bytes() -> bytes:
    return (
        json.dumps(
            {
                "apiVersion": "performance.perfeng.io/v1",
                "kind": "PerformancePolicy",
                "metadata": {"name": POLICY_ID, "version": VERSION, "owner": "platform"},
                "spec": {"mode": "observe", "missingData": "inconclusive", "rules": [{}]},
            },
            indent=2,
        )
        + "\n"
    ).encode()


def bundle(root: Path, content: bytes | None = None) -> Path:
    policy_path = root / "policies/pod-startup.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_bytes(content or policy_bytes())
    (root / "registry.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "entries": [
                    {
                        "policy": "policies/pod-startup.json",
                        "contractsVersion": "0.13.0",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


class PolicyPublishTests(unittest.TestCase):
    def test_resolves_exact_registry_policy_and_builds_stable_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = bundle(Path(temp).resolve())
            source = policy_publish.resolve_source(root, POLICY_ID, VERSION)
        self.assertEqual(source.content, policy_bytes())
        self.assertEqual(source.mode, "observe")
        result = policy_publish.receipt(source)
        self.assertEqual(result["contractsVersion"], "0.13.0")
        self.assertEqual(result["bundlePolicyPath"], "policies/pod-startup.json")
        self.assertEqual(result["policy"]["id"], POLICY_ID)
        self.assertEqual(result["policy"]["version"], VERSION)
        self.assertEqual(
            result["policy"]["uri"],
            f"s3://{store.BUCKET}/policies/{POLICY_ID}/{VERSION}/performance-policy.json",
        )
        self.assertEqual(result["policy"]["sha256"], hashlib.sha256(policy_bytes()).hexdigest())

    def test_rejects_undeclared_ambiguous_redirected_and_invalid_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = bundle(Path(temp).resolve())
            registry_path = root / "registry.json"
            registry = json.loads(registry_path.read_text())
            registry["entries"].append(registry["entries"][0])
            registry_path.write_text(json.dumps(registry))
            with self.assertRaisesRegex(ValueError, "exactly one"):
                policy_publish.resolve_source(root, POLICY_ID, VERSION)

        cases = [b"{}", b'{"apiVersion":"performance.perfeng.io/v1","apiVersion":"x"}']
        for content in cases:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temp:
                root = bundle(Path(temp).resolve(), content)
                with self.assertRaises(ValueError):
                    policy_publish.resolve_source(root, POLICY_ID, VERSION)

        with tempfile.TemporaryDirectory() as temp:
            root = bundle(Path(temp).resolve())
            (root / "registry.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "entries": [{"policy": "../outside.json", "contractsVersion": "0.13.0"}],
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "path"):
                policy_publish.resolve_source(root, POLICY_ID, VERSION)

    def test_preview_is_offline_and_execution_requires_matching_reviewed_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = bundle(Path(temp).resolve())
            receipt = root / "receipt.json"
            arguments = [
                str(root),
                "--policy-id",
                POLICY_ID,
                "--version",
                VERSION,
                "--receipt",
                str(receipt),
            ]
            with (
                patch("scripts.policy_publish.cluster.run") as run,
                patch("scripts.policy_publish.execute") as execute,
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(policy_publish.main(arguments), 0)
                run.assert_not_called()
                execute.assert_not_called()
                self.assertIn(hashlib.sha256(policy_bytes()).hexdigest(), output.getvalue())
            self.assertFalse(receipt.exists())

            for expected in [None, "0" * 64]:
                execute_arguments = [*arguments, "--execute"]
                if expected is not None:
                    execute_arguments += ["--expected-sha256", expected]
                with (
                    patch("scripts.policy_publish.execute") as execute,
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(policy_publish.main(execute_arguments), 1)
                    execute.assert_not_called()

    def test_upload_is_conditional_and_verifies_bytes_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            source = policy_publish.resolve_source(bundle(Path(temp).resolve()), POLICY_ID, VERSION)
        script = policy_publish.upload_script(source)
        self.assertIn('--if-none-match "*"', script)
        self.assertIn("sha256sum -c", script)
        self.assertIn("ContentLength", script)
        self.assertIn("ContentType", script)
        self.assertIn('Metadata."policy-id"', script)
        self.assertIn('Metadata."policy-version"', script)
        self.assertIn("Metadata.sha256", script)
        self.assertIn("Metadata.format", script)
        self.assertNotIn("delete-object", script)

    def test_execute_uses_bootstrap_credentials_and_writes_receipt_last(self):
        with tempfile.TemporaryDirectory() as temp:
            root = bundle(Path(temp).resolve())
            source = policy_publish.resolve_source(root, POLICY_ID, VERSION)
            output = root / "publication.json"
            responses = ["", "", "", "", "", "", ""]
            with (
                patch("scripts.policy_publish.cluster.verify_local_docker"),
                patch("scripts.policy_publish.cluster.verify_context"),
                patch("scripts.policy_publish.store.ensure_secret") as ensure,
                patch("scripts.policy_publish.cluster.run", side_effect=responses) as run,
                patch("scripts.policy_publish.uuid.uuid4") as identity,
                redirect_stdout(io.StringIO()),
            ):
                identity.return_value.hex = "a" * 32
                result = policy_publish.execute(source, output, cluster.ROOT)
            ensure.assert_called_once_with(cluster.ROOT, create=False)
            create = run.call_args_list[1]
            pod = json.loads(create.kwargs["input_data"])
            secrets = {
                item["valueFrom"]["secretKeyRef"]["name"]
                for item in pod["spec"]["containers"][0]["env"]
                if "valueFrom" in item
            }
            self.assertEqual(secrets, {store.SECRET})
            self.assertEqual(run.call_args_list[3].kwargs["input_data"], policy_bytes())
            self.assertEqual(json.loads(output.read_text()), result)
            self.assertEqual(
                run.call_args_list[-1].args[0][-5:],
                [
                    "delete",
                    "pod",
                    "policy-publish-aaaaaaaaaaaaaaaa",
                    "--wait=true",
                    "--timeout=60s",
                ],
            )

    def test_failed_publication_removes_receipt_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = bundle(Path(temp).resolve())
            source = policy_publish.resolve_source(root, POLICY_ID, VERSION)
            output = root / "publication.json"
            with (
                patch("scripts.policy_publish.cluster.verify_local_docker"),
                patch("scripts.policy_publish.cluster.verify_context"),
                patch("scripts.policy_publish.store.ensure_secret"),
                patch("scripts.policy_publish.cluster.run", side_effect=["", "", ValueError("no")]),
                redirect_stdout(io.StringIO()),
            ):
                with self.assertRaisesRegex(ValueError, "no"):
                    policy_publish.execute(source, output, cluster.ROOT)
            self.assertFalse(output.exists())

    def test_transfer_pod_rejects_unsafe_secret_names(self):
        with self.assertRaisesRegex(ValueError, "Secret name"):
            policy_publish.artifact_upload.upload_pod(
                "preview", cluster.ROOT, credential_secret="$(secret)"
            )


if __name__ == "__main__":
    unittest.main()
