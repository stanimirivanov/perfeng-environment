"""Fixed expectations for real human login and delegated exchange."""

import unittest

from scripts import keycloak_human as human
from scripts import keycloak_qualification as fixture


class HumanQualificationTests(unittest.TestCase):
    def test_configuration_preserves_disjoint_identity_and_audiences(self):
        config = fixture.realm("ci-cert")
        human.configure(
            config, {human.LOGIN: "login-cert", human.DELEGATE: "delegate-cert"}, "PASSWORD-CANARY"
        )
        clients = {client["clientId"]: client for client in config["clients"]}
        for name in (human.LOGIN, human.DELEGATE):
            self.assertFalse(clients[name]["serviceAccountsEnabled"])
            self.assertFalse(clients[name]["directAccessGrantsEnabled"])
            self.assertEqual(
                clients[name]["attributes"]["standard.token.exchange.enabled"],
                str(name == human.DELEGATE).lower(),
            )
        for name in (human.LOGIN, human.DELEGATE):
            self.assertTrue(
                any(
                    mapper["protocolMapper"] == "oidc-sub-mapper"
                    and mapper["config"].get("access.token.claim") == "true"
                    for mapper in clients[name]["protocolMappers"]
                )
            )
        self.assertEqual(
            clients[human.LOGIN]["protocolMappers"][0]["config"]["included.client.audience"],
            human.DELEGATE,
        )
        self.assertEqual(
            clients[human.DELEGATE]["protocolMappers"][0]["config"]["included.client.audience"],
            fixture.AUDIENCE,
        )
        self.assertNotEqual(config["users"][0]["id"], config["users"][1]["id"])

    def test_form_and_callback_cannot_select_another_destination_or_state(self):
        issuer = "https://127.0.0.1:12345/realms/perfeng-fixture"
        action = issuer + "/login-actions/authenticate?session_code=test"
        self.assertEqual(
            human.form_action(f'<form id="kc-form-login" action="{action}">', issuer), action
        )
        for bad in (
            "https://foreign.example/login",
            issuer + "/admin",
            "http://127.0.0.1:12345/login",
            "//foreign.example/login",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                human.form_action(f'<form id="kc-form-login" action="{bad}">', issuer)
        callback = human.REDIRECT + "?code=test&state=expected&iss=" + issuer
        self.assertEqual(human.callback_code(callback, "expected", issuer), "test")
        for bad in (
            callback.replace("state=expected", "state=foreign"),
            callback + "&code=other",
            callback.replace(issuer, "https://foreign"),
            callback.replace(human.REDIRECT, "https://foreign/callback"),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                human.callback_code(bad, "expected", issuer)

    def test_signed_human_profile_and_nonce_are_independent_expectations(self):
        import time

        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwks = {
            "keys": [
                {
                    "kid": "test",
                    "kty": "RSA",
                    "alg": "RS256",
                    "use": "sig",
                    "n": fixture.encode(key.public_key().public_numbers().n.to_bytes(256)),
                    "e": "AQAB",
                }
            ]
        }
        claims = {
            "iss": "https://issuer.example",
            "sub": human.HUMAN,
            "azp": human.LOGIN,
            "aud": human.LOGIN,
            "typ": "ID",
            "nonce": "expected",
            "iat": int(time.time()),
            "exp": int(time.time()) + 120,
        }
        for field, value in [
            ("nonce", "other"),
            ("sub", fixture.CI_SUBJECT),
            ("azp", human.DELEGATE),
            ("aud", fixture.AUDIENCE),
        ]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                fixture.verify(
                    fixture.sign(key, {**claims, field: value}),
                    jwks,
                    claims["iss"],
                    subject=human.HUMAN,
                    client=human.LOGIN,
                    audience=human.LOGIN,
                    kind="ID",
                    nonce="expected",
                )
        fixture.verify(
            fixture.sign(key, claims),
            jwks,
            claims["iss"],
            subject=human.HUMAN,
            client=human.LOGIN,
            audience=human.LOGIN,
            kind="ID",
            nonce="expected",
        )

    def test_machine_only_receipt_does_not_retire_human_gate(self):
        self.assertIn(
            "human-login-and-delegate-exchange",
            fixture.receipt(["private-key-jwt-machine-profile"])["remainingGates"],
        )
