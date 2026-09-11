from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from scripts import control_plane_registry as registry

TOKEN = "github-package-read-token-value"
IMAGE = (
    "ghcr.io/stanimirivanov/perfeng-control-plane@sha256:"
    "ca309ca08c22ec57a0921d6cbb1882b4d57e05572b830437e5d24e3c28b076ab"
)


class Response:
    def __init__(self, content: bytes, headers: dict[str, str] | None = None) -> None:
        self.content = content
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self.content[:size]


def stored_secret() -> dict[str, Any]:
    document = registry.secret_document(registry.USERNAME, TOKEN)
    content = document["stringData"][".dockerconfigjson"].encode()
    return {
        "metadata": document["metadata"],
        "type": document["type"],
        "immutable": document["immutable"],
        "data": {".dockerconfigjson": base64.b64encode(content).decode("ascii")},
    }


class ControlPlaneRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_secret_round_trip_is_single_registry_and_immutable(self) -> None:
        value = stored_secret()
        self.assertEqual(registry.decode_secret(value), (registry.USERNAME, TOKEN))
        self.assertEqual(value["type"], "kubernetes.io/dockerconfigjson")
        self.assertIs(value["immutable"], True)

    def test_existing_owned_secret_is_verified_without_rotation(self) -> None:
        read_token = Mock(side_effect=AssertionError("credential prompt was unexpected"))
        with (
            patch(
                "scripts.control_plane_registry.cluster.run",
                return_value=json.dumps(stored_secret()),
            ),
            patch("scripts.control_plane_registry.verify_registry_credentials") as verify,
        ):
            credentials = registry.ensure_secret(
                self.root,
                IMAGE,
                create=True,
                read_token=read_token,
            )

        self.assertEqual(credentials, (registry.USERNAME, TOKEN))
        read_token.assert_not_called()
        verify.assert_called_once_with(registry.USERNAME, TOKEN, IMAGE)

    def test_new_secret_is_verified_then_created_through_private_stdin(self) -> None:
        with (
            patch("scripts.control_plane_registry.cluster.run", side_effect=["", ""]) as run,
            patch("scripts.control_plane_registry.verify_registry_credentials") as verify,
        ):
            registry.ensure_secret(self.root, IMAGE, create=True, read_token=lambda _: TOKEN)

        verify.assert_called_once_with(registry.USERNAME, TOKEN, IMAGE)
        create = run.call_args_list[1]
        self.assertNotIn(TOKEN, " ".join(create.args[0]))
        payload = json.loads(create.kwargs["input_data"])
        self.assertEqual(payload["metadata"]["name"], registry.SECRET)
        self.assertIn(TOKEN, payload["stringData"][".dockerconfigjson"])

    def test_missing_health_secret_never_prompts_or_creates(self) -> None:
        read_token = Mock(side_effect=AssertionError("credential prompt was unexpected"))
        with patch("scripts.control_plane_registry.cluster.run", return_value="") as run:
            with self.assertRaises(ValueError):
                registry.ensure_secret(self.root, IMAGE, create=False, read_token=read_token)

        run.assert_called_once()
        read_token.assert_not_called()

    def test_registry_verification_uses_basic_exchange_then_exact_digest(self) -> None:
        requests = []
        digest = IMAGE.rsplit("@", 1)[1]
        responses = iter(
            [
                Response(json.dumps({"token": "registry-bearer-token-value"}).encode()),
                Response(b"manifest", {"Docker-Content-Digest": digest}),
            ]
        )

        def open_request(request, *, timeout: int):
            self.assertEqual(timeout, 30)
            requests.append(request)
            return next(responses)

        registry.verify_registry_credentials(
            registry.USERNAME,
            TOKEN,
            IMAGE,
            opener=open_request,
        )

        self.assertTrue(requests[0].get_header("Authorization").startswith("Basic "))
        self.assertEqual(
            requests[1].get_header("Authorization"),
            "Bearer registry-bearer-token-value",
        )
        self.assertTrue(requests[1].full_url.endswith(digest))

    def test_invalid_or_foreign_secret_is_rejected(self) -> None:
        invalid_token = stored_secret()
        config = json.loads(
            base64.b64decode(invalid_token["data"][".dockerconfigjson"], validate=True)
        )
        config["auths"][registry.REGISTRY]["password"] = "short"
        config["auths"][registry.REGISTRY]["auth"] = base64.b64encode(
            f"{registry.USERNAME}:short".encode()
        ).decode("ascii")
        invalid_token["data"][".dockerconfigjson"] = base64.b64encode(
            json.dumps(config).encode()
        ).decode("ascii")
        cases = [invalid_token, stored_secret()]
        cases[1]["metadata"]["namespace"] = "other"
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    registry.decode_secret(value)


if __name__ == "__main__":
    unittest.main()
