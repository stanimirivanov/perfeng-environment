"""Offline protocol boundary checks; no provider or Console admission receipt."""

import json
import ssl
import unittest
from dataclasses import FrozenInstanceError, asdict
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import keycloak_human as human
from scripts import keycloak_qualification as fixture


class CodeObserverTests(unittest.TestCase):
    def protocol(self, observer=None, changes=None, refuse=False, bad_signature=False):
        issuer = "https://127.0.0.1:12345/realms/perfeng-fixture"
        events = []
        nonces = []
        redeemed = False
        response_body = {
            "id_token": "ID-CANARY",
            "access_token": "ACCESS-CANARY",
            "expires_in": 300,
            "refresh_token": "REFRESH-CANARY",
            **(changes or {}),
        }

        def response(status, body, headers=None):
            value = MagicMock()
            value.code = status
            value.headers = headers or {}
            value.read.return_value = body.encode()
            value.__enter__.return_value = value
            return value

        def send(request, timeout):
            nonlocal redeemed
            self.assertEqual(timeout, 5)
            target = urlsplit(request.full_url)
            if target.path.endswith("/auth"):
                query = parse_qs(target.query)
                nonces.append(query["nonce"][0])
                callback = (
                    human.REDIRECT
                    + "?"
                    + urlencode({"code": "accepted", "state": query["state"][0], "iss": issuer})
                )
                return response(302, "", {"Location": callback})
            form = parse_qs(request.data.decode())
            grant = form["grant_type"][0]
            events.append("post:" + grant)
            if grant == "authorization_code":
                if form["code_verifier"][0].startswith("wrong-") or redeemed or refuse:
                    return response(400, '{"error":"invalid_grant"}')
                redeemed = True
                return response(200, json.dumps(response_body))
            if grant == human.EXCHANGE:
                if form["client_id"] == [human.DELEGATE]:
                    return response(200, '{"access_token":"EXCHANGED","token_type":"Bearer"}')
                return response(
                    400,
                    json.dumps(
                        {
                            "error": "invalid_request",
                            "error_description": "Standard token exchange is not enabled for the requested client",
                        }
                    ),
                )
            return response(400, '{"error":"unauthorized_client"}')

        def verify(token, _jwks, _issuer, **profile):
            events.append("verify:" + str(token))
            if bad_signature or (token == "ID-CANARY" and profile["client"] == human.DELEGATE):
                raise ValueError("BAD-SIGNATURE-CANARY")
            return {}

        def observe(pair):
            events.append("observe")
            assert observer is not None
            observer(pair, events, nonces)

        def clock():
            events.append("clock")
            return 1_800_000_000_123_456_789

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        opener = MagicMock()
        opener.open.side_effect = send
        with (
            patch.object(human, "build_opener", return_value=opener),
            patch.object(human.time, "time_ns", side_effect=clock),
        ):
            result = human.qualify(
                issuer,
                ssl.create_default_context(),
                {name: key for name in (human.LOGIN, human.DELEGATE, "perfeng-ci")},
                "PASSWORD-CANARY",
                {},
                lambda *_args, **_kwargs: "ASSERTION-CANARY",
                verify,
                fixture.require_denial,
                observe_code_pair=observe if observer is not None else None,
            )
        return result, events

    def test_observation_is_owned_immutable_redacted_and_precedes_replay(self):
        seen = []

        def observe(pair, events, nonces):
            seen.append(pair)
            self.assertEqual(pair.stage, "unverified-code")
            self.assertEqual(pair.issuer, "https://127.0.0.1:12345/realms/perfeng-fixture")
            self.assertEqual(pair.nonce, nonces[-1])
            self.assertEqual(pair.request_started_at_ms, 1_800_000_000_123)
            self.assertEqual(
                events[-4:],
                ["post:authorization_code", "verify:ID-CANARY", "verify:ACCESS-CANARY", "observe"],
            )
            with self.assertRaises(FrozenInstanceError):
                pair.nonce = "changed"
            self.assertEqual(
                set(asdict(pair)),
                {
                    "stage",
                    "issuer",
                    "nonce",
                    "request_started_at_ms",
                    "id_token",
                    "access_token",
                    "expires_in",
                },
            )
            for secret in (
                "ID-CANARY",
                "ACCESS-CANARY",
                "REFRESH-CANARY",
                "PASSWORD-CANARY",
                "ASSERTION-CANARY",
            ):
                self.assertNotIn(secret, repr(pair))

        result, events = self.protocol(observe)
        self.assertEqual(result, human.HUMAN_CHECKS)
        self.assertEqual(len(seen), 1)
        self.assertEqual(events[-1], "post:authorization_code")
        post = events.index("observe") - 3
        self.assertEqual(events[post - 1], "clock")

    def test_refused_unverified_and_malformed_envelopes_do_not_notify(self):
        def unexpected(*_args):
            self.fail("observer received refused or malformed candidates")

        for index, options in enumerate(
            [
                {"refuse": True},
                {"bad_signature": True},
                *({"changes": {"expires_in": value}} for value in (0, 301, True, "300")),
                {"changes": {"id_token": "x" * 16385}},
            ]
        ):
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.protocol(unexpected, **options)

    def test_observer_defect_is_safely_wrapped_and_stops_the_protocol(self):
        def broken(*_args):
            raise RuntimeError("ACCESS-CANARY")

        with self.assertRaises(ValueError) as caught:
            self.protocol(broken)
        self.assertEqual(str(caught.exception), "Human code-pair observer failed")
        self.assertIsNone(caught.exception.__cause__)

    def test_default_observation_does_not_change_existing_checks_or_receipt(self):
        result, events = self.protocol()
        self.assertNotIn("observe", events)
        self.assertEqual(result, human.HUMAN_CHECKS)
        self.assertEqual(fixture.receipt(result)["admission"], "NOT_ADMITTED")

    def test_command_failure_never_prints_callback_diagnostics(self):
        import contextlib
        import io

        diagnostic = io.StringIO()
        with (
            patch.object(fixture, "execute", side_effect=ValueError("ACCESS-CANARY")),
            contextlib.redirect_stderr(diagnostic),
        ):
            self.assertEqual(fixture.main(["--execute"]), 1)
        self.assertEqual(diagnostic.getvalue(), "Keycloak qualification failed; no admission\n")
