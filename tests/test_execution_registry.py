import base64
import hashlib
import json
import unittest
from typing import Any
from unittest.mock import patch

from scripts import cluster
from scripts import execution_registry as execution
from tests.test_control_plane_migration import RELEASE

TOKEN = "test-package-read-token-never-a-real-secret"
MANIFEST = b"reviewed manifest bytes"
DIGEST = hashlib.sha256(MANIFEST).hexdigest()
IMAGES = tuple(
    f"ghcr.io/stanimirivanov/{name}@sha256:{DIGEST}"
    for name in ("perfeng-analysis", "perfeng-k6", "perfeng-kube-burner")
)


def stored_secret() -> dict:
    document = execution.registry.secret_document(
        execution.registry.USERNAME, TOKEN, **execution.IDENTITY
    )
    document["data"] = {
        key: base64.b64encode(value.encode()).decode()
        for key, value in document.pop("stringData").items()
    }
    return document


class ExecutionRegistryTests(unittest.TestCase):
    def test_preview_is_offline(self) -> None:
        with (
            patch("sys.argv", ["execution_registry", "deploy"]),
            patch.object(execution, "execute") as execute,
        ):
            self.assertEqual(execution.main(), 0)
        execute.assert_not_called()

    def test_exact_three_images_are_read_from_pinned_registry(self) -> None:
        entries: list[dict[str, Any]] = [
            {
                "rawProducer": {"artifact": {"image": image}},
                "normalizerProducer": {"image": IMAGES[0]},
                "reportProducer": {"image": IMAGES[0]},
            }
            for image in IMAGES[1:]
        ]
        with patch.object(
            execution.runtime,
            "registry_files",
            return_value={"registry.json": json.dumps({"entries": entries})},
        ) as read:
            self.assertEqual(execution.selected_images(RELEASE), IMAGES)
            read.assert_called_once_with(RELEASE)
            entries[0]["rawProducer"]["artifact"]["image"] = "unreviewed:latest"
            read.return_value = {"registry.json": json.dumps({"entries": entries})}
            with self.assertRaises(ValueError):
                execution.selected_images(RELEASE)

    def test_create_verifies_all_images_before_private_stdin(self) -> None:
        with (
            patch.object(execution.cluster, "run", side_effect=["", ""]) as run,
            patch.object(
                execution.registry, "verify_registry_credentials", return_value=MANIFEST
            ) as verify,
            patch.dict(
                execution.os.environ, {execution.registry.TOKEN_ENVIRONMENT_VARIABLE: TOKEN}
            ),
        ):
            execution.ensure_secret(cluster.ROOT, IMAGES, create=True)
        self.assertEqual(verify.call_count, 3)
        command = run.call_args.args[0]
        self.assertNotIn(TOKEN, " ".join(command))
        self.assertEqual(command[-3:], ["create", "-f", "-"])
        document = json.loads(run.call_args.kwargs["input_data"])
        self.assertEqual(document["metadata"]["namespace"], execution.NAMESPACE)
        self.assertTrue(document["immutable"])

    def test_existing_secret_is_verified_without_environment_or_rotation(self) -> None:
        with (
            patch.object(execution.cluster, "run", return_value=json.dumps(stored_secret())) as run,
            patch.object(
                execution.registry, "verify_registry_credentials", return_value=MANIFEST
            ) as verify,
            patch.object(
                execution.os.environ, "get", side_effect=AssertionError("must not read token")
            ),
        ):
            execution.ensure_secret(cluster.ROOT, IMAGES, create=True)
        run.assert_called_once()
        self.assertEqual(verify.call_count, 3)

    def test_missing_health_secret_never_reads_environment_or_creates(self) -> None:
        with (
            patch.object(execution.cluster, "run", return_value="") as run,
            patch.object(
                execution.os.environ, "get", side_effect=AssertionError("must not read token")
            ),
            self.assertRaisesRegex(ValueError, "missing"),
        ):
            execution.ensure_secret(cluster.ROOT, IMAGES, create=False)
        run.assert_called_once()

    def test_foreign_secret_failed_access_and_wrong_bytes_do_not_create(self) -> None:
        foreign = stored_secret()
        foreign["metadata"]["labels"]["perfeng.io/purpose"] = "control-plane-image-pull"
        for text, manifest, failure in [
            (json.dumps(foreign), MANIFEST, None),
            ("", b"other bytes", None),
            ("", MANIFEST, ValueError("denied")),
        ]:
            with (
                self.subTest(text=bool(text), failure=bool(failure)),
                patch.object(execution.cluster, "run", return_value=text) as run,
                patch.object(
                    execution.registry,
                    "verify_registry_credentials",
                    return_value=manifest,
                    side_effect=failure,
                ),
                patch.dict(
                    execution.os.environ, {execution.registry.TOKEN_ENVIRONMENT_VARIABLE: TOKEN}
                ),
                self.assertRaises(ValueError),
            ):
                execution.ensure_secret(cluster.ROOT, IMAGES, create=True)
            run.assert_called_once()
