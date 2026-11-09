"""Focused checks for the disposable OpenBao fixture bootstrap and JWTs."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from scripts.openbao_qualification import jwt, stage_binary


class OpenBaoQualificationTests(unittest.TestCase):
    def test_unpinned_binary_is_refused_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            untrusted = root / "untrusted"
            untrusted.write_bytes(b"unexpected executable")
            with patch("scripts.openbao_qualification.platform.machine", return_value="x86_64"):
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    stage_binary(untrusted, root)
            self.assertFalse((root / "bao").exists())

    def test_signed_token_binds_claims_and_short_expiry(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        header, payload, signature = jwt(key, {"sub": "fixture"}).split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        self.assertEqual(claims["sub"], "fixture")
        self.assertEqual(claims["exp"] - claims["iat"], 120)
        key.public_key().verify(
            base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
            f"{header}.{payload}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
