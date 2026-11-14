"""Offline checks for the explicitly selected real-provider fixture."""

import json
import time
import unittest
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import keycloak_qualification as fixture


class KeycloakQualificationTests(unittest.TestCase):
    def setUp(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims: dict[str, Any] = {
            "iss": "https://127.0.0.1:12345/realms/perfeng-fixture",
            "sub": fixture.CI_SUBJECT,
            "aud": fixture.AUDIENCE,
            "azp": fixture.CI_CLIENT,
            "typ": "Bearer",
            "iat": int(time.time()),
            "exp": int(time.time()) + 120,
        }
        self.jwks = {
            "keys": [
                {
                    "kid": "test",
                    "kty": "RSA",
                    "alg": "RS256",
                    "use": "sig",
                    "n": fixture.encode(self.key.public_key().public_numbers().n.to_bytes(256)),
                    "e": "AQAB",
                }
            ]
        }

    def test_verified_machine_profile_and_rejections(self):
        for field, bad in [
            ("iss", "https://foreign"),
            ("sub", "foreign"),
            ("azp", "login"),
            ("typ", "ID"),
            ("aud", [fixture.AUDIENCE, "extra"]),
            ("exp", self.claims["iat"] + 301),
            ("iat", True),
            ("nbf", int(time.time()) + 1000),
        ]:
            with self.subTest(field=field):
                claims = {**self.claims, field: bad}
                with self.assertRaises(ValueError):
                    fixture.verify(fixture.sign(self.key, claims), self.jwks, self.claims["iss"])
        fixture.verify(fixture.sign(self.key, self.claims), self.jwks, self.claims["iss"])
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with self.assertRaises(ValueError):
            fixture.verify(fixture.sign(other, self.claims), self.jwks, self.claims["iss"])

    def test_disjoint_grants_and_redacted_receipt(self):
        realm = fixture.realm("PUBLIC-CERTIFICATE")
        clients = {client["clientId"]: client for client in realm["clients"]}
        self.assertIn(fixture.AUDIENCE, clients)
        for name, client in clients.items():
            self.assertFalse(client["directAccessGrantsEnabled"])
            self.assertFalse(client["implicitFlowEnabled"])
            self.assertEqual(client["serviceAccountsEnabled"], name == fixture.CI_CLIENT)
            self.assertEqual(client["standardFlowEnabled"], name == "perfeng-console-login")
        self.assertEqual(clients[fixture.CI_CLIENT]["clientAuthenticatorType"], "client-jwt")
        receipt = fixture.receipt(["machine-profile", "wrong-key-denied"])
        self.assertEqual(receipt["admission"], "NOT_ADMITTED")
        self.assertTrue(receipt["remainingGates"])
        rendered = json.dumps(receipt)
        for secret in ("PUBLIC-CERTIFICATE", "access_token", "PRIVATE KEY"):
            self.assertNotIn(secret, rendered)

    def test_preview_has_no_docker_or_file_side_effects(self):
        from unittest.mock import patch

        with patch.object(fixture, "execute", side_effect=AssertionError("live mutation")):
            self.assertEqual(fixture.main([]), 0)

    def test_failed_grant_cleans_only_owned_container(self):
        from unittest.mock import Mock, patch

        calls = []

        def docker(*args):
            calls.append(args)
            if args[0] == "context":
                return "unix:///var/run/docker.sock"
            if args[0] == "create":
                return "owned-container"
            if args[0] == "inspect":
                return "true" if "Running" in args[2] else "fixture-nonce"
            return ""

        response = Mock()
        response.status = 200
        response.read.return_value = b"{}"
        refused = Mock()
        refused.status = 401
        refused.read.return_value = b'{"error":"invalid_client","error_description":"TOKEN-CANARY"}'
        connection = Mock()
        connection.getresponse.side_effect = [response, refused]
        with (
            patch.object(fixture, "docker", side_effect=docker),
            patch.object(fixture.os, "getuid", return_value=1000),
            patch.object(fixture.secrets, "token_hex", return_value="fixture-nonce"),
            patch.object(fixture.http.client, "HTTPSConnection", return_value=connection),
        ):
            with self.assertRaises(ValueError) as caught:
                fixture.execute()
        self.assertNotIn("TOKEN-CANARY", str(caught.exception))
        self.assertEqual(calls[-1], ("rm", "--force", "owned-container"))
        self.assertFalse(any("TOKEN-CANARY" in str(args) for args in calls))

    def test_ambiguous_create_timeout_recovers_exact_owned_container(self):
        from unittest.mock import patch

        calls = []

        def docker(*args):
            calls.append(args)
            if args[0] == "context":
                return "unix:///var/run/docker.sock"
            if args[0] == "create":
                raise fixture.subprocess.TimeoutExpired("docker", 60)
            if args[0] == "ps":
                return "owned-container"
            if args[0] == "inspect":
                return "fixture-nonce"
            return ""

        with (
            patch.object(fixture, "docker", side_effect=docker),
            patch.object(fixture.os, "getuid", return_value=1000),
            patch.object(fixture.secrets, "token_hex", return_value="fixture-nonce"),
        ):
            with self.assertRaises(fixture.subprocess.TimeoutExpired):
                fixture.execute()
        self.assertEqual(calls[-1], ("rm", "--force", "owned-container"))
        self.assertIn("label=perfeng.keycloak.fixture=fixture-nonce", calls[-3])

    def test_malformed_request_is_not_evidence_of_disabled_grant(self):
        with self.assertRaises(ValueError):
            fixture.require_denial(400, {"error": "invalid_request"}, "unauthorized_client")
        fixture.require_denial(400, {"error": "unauthorized_client"}, "unauthorized_client")

    def test_remote_docker_endpoint_is_refused(self):
        from unittest.mock import patch

        with patch.dict(fixture.os.environ, {"DOCKER_HOST": "tcp://remote.example:2375"}):
            with self.assertRaises(ValueError):
                fixture.require_local_docker()
