"""Independent fixture expectations; no Docker/network or component imports."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from scripts import keycloak_bff as bff
from scripts import keycloak_human as human
from scripts import keycloak_qualification as fixture


class BffFixtureTests(unittest.TestCase):
    def test_preview_never_imports_sdk_or_provisions_and_bad_selection_fails_before_docker(self):
        import contextlib
        import io

        with (
            patch.object(
                bff,
                "preview",
                return_value={"consoleCommit": "reviewed", "admission": "NOT_ADMITTED"},
            ) as preview,
            patch.object(bff, "qualify") as qualify,
            patch.object(fixture, "docker") as docker,
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(fixture.main(["--console-source", "/selected"]), 0)
            self.assertEqual(
                json.loads(output.getvalue())["bffTransaction"]["consoleCommit"], "reviewed"
            )
            qualify.assert_not_called()
            docker.assert_not_called()
            preview.assert_called_once()
        with (
            patch.object(bff, "preview", side_effect=ValueError("refused")),
            patch.object(fixture, "docker") as docker,
        ):
            with self.assertRaises(ValueError):
                fixture.execute(console_source=Path("/selected"))
            docker.assert_not_called()

    def test_only_opt_in_login_client_gets_the_exact_bff_redirect(self):
        config = fixture.realm("ci")
        human.configure(config, {human.LOGIN: "login", human.DELEGATE: "delegate"}, "PASSWORD")
        clients = {c["clientId"]: c for c in config["clients"]}
        self.assertEqual(clients[human.LOGIN]["redirectUris"], [human.REDIRECT])
        other = fixture.realm("ci")
        human.configure(
            other, {human.LOGIN: "login", human.DELEGATE: "delegate"}, "PASSWORD", bff=True
        )
        selected = {c["clientId"]: c for c in other["clients"]}
        self.assertEqual(
            selected[human.LOGIN]["redirectUris"], [human.REDIRECT, human.BFF_REDIRECT]
        )
        self.assertEqual(selected[human.DELEGATE]["redirectUris"], [])

    def test_authorization_is_bff_supplied_but_cannot_select_foreign_destinations(self):
        from urllib.parse import urlencode

        issuer = "https://127.0.0.1:12345/realms/perfeng-fixture"
        values = {
            "client_id": human.LOGIN,
            "redirect_uri": "https://127.0.0.1/console/auth/callback",
            "response_type": "code",
            "scope": "openid",
            "state": "s" * 43,
            "nonce": "n" * 43,
            "code_challenge": "c" * 43,
            "code_challenge_method": "S256",
        }
        url = issuer + "/protocol/openid-connect/auth?" + urlencode(values)
        self.assertEqual(bff.authorization_state(url, issuer), values["state"])
        for change in [
            {"redirect_uri": "https://foreign/callback"},
            {"client_id": human.DELEGATE},
            {"scope": "openid offline_access"},
            {"state": "short"},
            {"code_challenge_method": "plain"},
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                bff.authorization_state(
                    issuer + "/protocol/openid-connect/auth?" + urlencode({**values, **change}),
                    issuer,
                )
        for bad in [url + "&state=extra", url.replace("127.0.0.1", "foreign"), url + "#fragment"]:
            with self.assertRaises(ValueError):
                bff.authorization_state(bad, issuer)

    def test_human_driver_preserves_raw_callback_and_never_posts_a_token_request(self):
        import io
        import ssl
        from urllib.parse import urlencode

        issuer = "https://127.0.0.1:12345/realms/perfeng-fixture"
        state = "s" * 43
        query = urlencode({"state": state, "iss": issuer, "code": "code+with/slash"})
        callback = human.BFF_REDIRECT + "?" + query
        action = issuer + "/login-actions/authenticate?session=fixture"
        authorization = (
            issuer
            + "/protocol/openid-connect/auth?"
            + urlencode(
                {
                    "client_id": human.LOGIN,
                    "redirect_uri": human.BFF_REDIRECT,
                    "response_type": "code",
                    "scope": "openid",
                    "state": state,
                    "nonce": "n" * 43,
                    "code_challenge": "c" * 43,
                    "code_challenge_method": "S256",
                }
            )
        )

        def response(code, body, headers=None):
            result = Mock(code=code, headers=headers or {})
            result.read1 = io.BytesIO(body.encode()).read1
            result.__enter__ = Mock(return_value=result)
            result.__exit__ = Mock(return_value=False)
            return result

        opener = Mock()
        opener.open.side_effect = [
            response(200, '<form id="kc-form-login" action="' + action + '"></form>'),
            response(302, "", {"Location": callback}),
        ]
        with patch.object(bff, "build_opener", return_value=opener):
            authorize = bff.authorizer(issuer, ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), "secret")
            self.assertEqual(authorize(authorization), "/console/auth/callback?" + query)
        calls = opener.open.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[0].full_url, authorization)
        self.assertIsNone(calls[0].args[0].data)
        self.assertEqual(calls[1].args[0].full_url, action)
        self.assertEqual(
            calls[1].args[0].data, b"username=fixture-human&password=secret&credentialId="
        )
        with self.assertRaises(ValueError):
            human.callback_code(callback, state, issuer)
        with self.assertRaises(ValueError):
            human.callback_code(callback, state, issuer, redirect="https://foreign/callback")

    def test_source_preview_is_exact_offline_and_exports_git_objects_not_working_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "console"
            source.mkdir()

            def git(*args):
                return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()

            git("init", "-q")
            git("config", "user.name", "fixture")
            git("config", "user.email", "fixture@invalid")
            git("remote", "add", "origin", "https://github.com/stanimirivanov/perfeng-console.git")
            for name, data in {
                "package.json": '{"engines":{"node":"26.13.0"},"packageManager":"pnpm@12.16.1","dependencies":{"effect":"4.1.2"}}',
                "pnpm-lock.yaml": "locked",
                "src/bff/login/owner.ts": "reviewed",
                "src/generated/validators.mjs": "validator",
            }.items():
                file = source / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(data)
            (source / "node_modules").mkdir()
            git("add", ".")
            git("commit", "-qm", "reviewed")
            pin = {
                **bff.read_pin(),
                "commit": git("rev-parse", "HEAD"),
                "lockSha256": hashlib.sha256(b"locked").hexdigest(),
            }
            with patch.object(bff, "read_pin", return_value=pin):
                self.assertEqual(bff.preview(source)["consoleCommit"], pin["commit"])
                (source / "src/bff/login/owner.ts").write_text("mutated")
                with self.assertRaises(ValueError):
                    bff.preview(source)
                target = Path(temporary) / "export"
                bff.export_console(source, target, pin)
                self.assertEqual((target / "src/bff/login/owner.ts").read_text(), "reviewed")


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_pipe_acquisition_is_killed_and_reaped(self):
        child = Mock(stdin=None, stdout=None, stderr=None, returncode=None)
        child.wait = AsyncMock(return_value=0)
        with patch.object(bff.asyncio, "create_subprocess_exec", AsyncMock(return_value=child)):
            with self.assertRaisesRegex(ValueError, "BFF qualification refused"):
                await bff.consume(["unused"], {}, lambda _: "unused")
        child.kill.assert_called_once()
        child.wait.assert_awaited_once()

    async def test_process_phases_eof_and_private_failures(self):
        import asyncio

        receipt = bff.PASSED
        authorize = "https://127.0.0.1:12345/realms/perfeng-fixture/protocol/openid-connect/auth"
        program = (
            "import sys,json; sys.stdin.readline(); print(json.dumps({'phase':'authorize','authorizationUrl':"
            + repr(authorize)
            + "}),flush=True); sys.stdin.read(); print("
            + repr(json.dumps(receipt))
            + ")"
        )
        result = await bff.consume(
            [sys.executable, "-I", "-c", program],
            {"privateKeyPem": "KEY-CANARY"},
            lambda _: "/console/auth/callback?code=CODE-CANARY",
        )
        self.assertEqual(result, receipt)
        for bad in [
            "import time; time.sleep(5)",
            "import sys; print('KEY-CANARY',file=sys.stderr)",
            "print('x'*20000)",
            program.replace("sys.stdin.read();", "sys.stdin.read(); print('extra');"),
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError) as caught:
                await asyncio.wait_for(
                    bff.consume(
                        [sys.executable, "-I", "-c", bad],
                        {"privateKeyPem": "KEY-CANARY"},
                        lambda _: "callback",
                        timeout=0.2,
                    ),
                    2,
                )
            self.assertEqual(str(caught.exception), "BFF qualification refused")
            self.assertNotIn("KEY-CANARY", str(caught.exception))
