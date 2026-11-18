"""Public phase boundaries; scripted HTTP observations are not a live receipt."""

import base64
import json
import ssl
import unittest
from dataclasses import FrozenInstanceError, asdict
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import rsa

from scripts import keycloak_qualification as fixture
from scripts import keycloak_rotation as rotation


class PhaseObserverTests(unittest.TestCase):
    def protocol(self, observer=None, malformed=None):
        issuer = "https://127.0.0.1:12345" + rotation.ISSUER_PATH
        calls, phases = [], []
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        def token(kid):
            header = (
                base64.urlsafe_b64encode(json.dumps({"kid": kid}).encode()).rstrip(b"=").decode()
            )
            return header + ".TOKEN-CANARY.signature"

        def jwks(*kids):
            return {"keys": [{"kid": kid, "alg": "RS256", "use": "sig"} for kid in kids]}

        initial = {
            "active": {"RS256": "wrong" if malformed == "initial" else "old"},
            "keys": [
                {"kid": "old", "algorithm": "RS256", "providerId": "owned-old", "status": "ACTIVE"}
            ],
        }
        overlap = jwks(*(["new"] if malformed == "overlap" else ["old", "new"]))
        retired = jwks(*(["old", "new"] if malformed == "retired" else ["new"]))
        script = [
            (200, {"token_type": "Bearer", "access_token": token("old")}),
            (200, initial),
            (200, {"id": "realm"}),
            (
                200,
                {
                    "parentId": "realm",
                    "providerId": "rsa-generated",
                    "providerType": "org.keycloak.keys.KeyProvider",
                    "config": {"active": ["true"]},
                },
            ),
            (403, {}),
            (201, None),
            (204, None),
            (200, {"active": {"RS256": "new"}, "keys": [{"kid": "old", "status": "PASSIVE"}]}),
            (200, overlap),
            (200, {"token_type": "Bearer", "access_token": token("new")}),
            (200, {"token_type": "Bearer", "access_token": token("new")}),
            (204, None),
            (200, retired),
            (200, {"token_type": "Bearer", "access_token": token("new")}),
        ]
        connection = Mock()
        connection.request.side_effect = lambda method, path, *_args: calls.append((method, path))

        def response():
            status, body = script.pop(0)
            value = Mock()
            value.status = status
            value.read.return_value = b"" if body is None else json.dumps(body).encode()
            return value

        def verify(value, keys, _issuer, **_profile):
            if value == token("old") and "old" not in {entry["kid"] for entry in keys["keys"]}:
                raise ValueError("retired key")
            return {}

        def observe(phase):
            phases.append(phase)
            if observer is not None:
                observer(phase, calls)

        connection.getresponse.side_effect = response
        with patch.object(rotation.http.client, "HTTPSConnection", return_value=connection):
            checks = rotation.qualify(
                issuer,
                ssl.create_default_context(),
                key,
                key,
                token("old"),
                jwks("old"),
                lambda *_args, **_kwargs: "ASSERTION-CANARY",
                verify,
                observe_key_phase=observe if observer is not None else None,
            )
        return checks, calls, phases

    def test_verified_phases_are_immutable_public_data_before_next_mutation(self):
        observed = []

        def observe(phase, calls):
            observed.append(phase.phase)
            self.assertEqual(len(calls), {"initial": 5, "overlap": 10, "retired": 14}[phase.phase])
            self.assertEqual(phase.issuer, "https://127.0.0.1:12345" + rotation.ISSUER_PATH)
            self.assertEqual(phase.old_key_id, "old")
            self.assertEqual(phase.new_key_id, None if phase.phase == "initial" else "new")
            self.assertEqual(set(asdict(phase)), {"phase", "issuer", "old_key_id", "new_key_id"})
            self.assertNotIn("CANARY", repr(phase))
            with self.assertRaises(FrozenInstanceError):
                phase.old_key_id = "changed"

        checks, _, _ = self.protocol(observe)
        self.assertEqual(observed, ["initial", "overlap", "retired"])
        self.assertEqual(checks, rotation.CHECKS)
        self.assertEqual(fixture.receipt(checks)["admission"], "NOT_ADMITTED")

    def test_invalid_provider_observations_never_emit_the_failed_phase(self):
        for failed, expected in [
            ("initial", []),
            ("overlap", ["initial"]),
            ("retired", ["initial", "overlap"]),
        ]:
            observed = []
            with self.subTest(phase=failed), self.assertRaises(ValueError):
                self.protocol(
                    lambda phase, _calls, observed=observed: observed.append(phase.phase),
                    malformed=failed,
                )
            self.assertEqual(observed, expected)

    def test_callback_failure_is_scrubbed_and_aborts_further_administration(self):
        for stopped in ("initial", "overlap", "retired"):
            calls_at_stop = []

            def observe(phase, calls, stopped=stopped, calls_at_stop=calls_at_stop):
                if phase.phase == stopped:
                    calls_at_stop.extend(calls)
                    raise RuntimeError("TOKEN-CANARY")

            with self.subTest(phase=stopped), self.assertRaises(ValueError) as caught:
                self.protocol(observe)
            self.assertEqual(str(caught.exception), "Key phase observer failed")
            self.assertIsNone(caught.exception.__cause__)
            if stopped == "initial":
                self.assertNotIn(("POST", rotation.ADMIN + "/components"), calls_at_stop)
            if stopped != "retired":
                self.assertFalse(any(method == "DELETE" for method, _ in calls_at_stop))

    def test_default_protocol_checklist_remains_unchanged(self):
        checks, calls, phases = self.protocol()
        self.assertEqual(checks, rotation.CHECKS)
        self.assertEqual(len(calls), 14)
        self.assertEqual(phases, [])
