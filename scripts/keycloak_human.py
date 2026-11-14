"""Protocol-only human/exchange qualification, never a Console session implementation."""

from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import secrets
import ssl
import time
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from cryptography.hazmat.primitives.asymmetric import rsa

LOGIN = "perfeng-console-login"
DELEGATE = "perfeng-console-delegate"
HUMAN = "e3295d37-a188-4753-b89c-1ca188d85102"
REDIRECT = "https://127.0.0.1/callback"
EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS = "urn:ietf:params:oauth:token-type:access_token"


HUMAN_CHECKS = [
    "human-login-PKCE-state-nonce",
    "delegate-exchange-same-subject-single-audience",
    "wrong-PKCE-and-code-replay-denied",
    "human-machine-grants-denied",
    "unapproved-exchange-caller-denied",
    "ID-token-substitution-denied",
]


def configure(config: dict[str, Any], certificates: dict[str, str], password: str) -> None:
    """Provision a test person and disjoint, private-key-authenticated BFF clients."""
    clients = {client["clientId"]: client for client in config["clients"]}
    for name in (LOGIN, DELEGATE):
        client = clients[name]
        client["attributes"].update(
            {
                "jwt.credential.certificate": certificates[name],
                "standard.token.exchange.enabled": str(name == DELEGATE).lower(),
                "standard.token.exchange.enableRefreshRequestedTokenType": "false",
                "standard.token.exchange.allowTokenExpirationAfterSubjectTokenExpiration": "false",
            }
        )
        client["protocolMappers"] = [
            {
                "name": "explicit-audience",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "config": {
                    "included.client.audience": DELEGATE
                    if name == LOGIN
                    else "perfeng-control-plane",
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                },
            }
        ]
        # No broad default scopes: explicitly map the provider's immutable user ID.
        client["protocolMappers"].append(
            {
                "name": "immutable-subject",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-sub-mapper",
                "config": {"access.token.claim": "true"},
            }
        )
    config["users"].append(
        {
            "id": HUMAN,
            "username": "fixture-human",
            "enabled": True,
            "firstName": "Fixture",
            "lastName": "Human",
            "email": "fixture@example.invalid",
            "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}],
            "requiredActions": [],
        }
    )


class LoginForm(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.actions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.actions.append(values.get("action") or "")


def form_action(html: str, issuer: str) -> str:
    parser = LoginForm()
    parser.feed(html)
    if len(parser.actions) != 1:
        raise ValueError("Expected one login form")
    action = parser.actions[0]
    target, origin = urlsplit(action), urlsplit(issuer)
    if (target.scheme, target.netloc, target.path) != (
        "https",
        origin.netloc,
        origin.path + "/login-actions/authenticate",
    ) or target.fragment:
        raise ValueError("Foreign login form target refused")
    return action


def callback_code(location: str, state: str, issuer: str) -> str:
    target = urlsplit(location)
    callback = urlsplit(REDIRECT)
    values = parse_qs(target.query, keep_blank_values=True)
    if (target.scheme, target.netloc, target.path) != (
        callback.scheme,
        callback.netloc,
        callback.path,
    ) or target.fragment:
        raise ValueError("Foreign callback refused")
    if (
        any(len(value) != 1 for value in values.values())
        or values.get("state") != [state]
        or values.get("iss") != [issuer]
        or not values.get("code", [""])[0]
        or "error" in values
    ):
        raise ValueError("Callback state/issuer/code refused")
    return values["code"][0]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def qualify(
    issuer: str,
    context: ssl.SSLContext,
    keys: dict[str, rsa.RSAPrivateKey],
    password: str,
    jwks: dict[str, Any],
    sign: Callable[..., str],
    verify: Callable[..., dict[str, Any]],
    deny: Callable[..., None],
) -> list[str]:
    """Use the real login protocol; keep tokens/cookies/assertions in memory only."""
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=context),
        HTTPCookieProcessor(http.cookiejar.CookieJar()),
        NoRedirect(),
    )
    endpoint = issuer + "/protocol/openid-connect/token"

    def request(url: str, form: dict[str, str] | None = None) -> tuple[int, Any, str]:
        # Every followed URL is constructed here or validated by form_action.
        request = Request(url, data=urlencode(form).encode() if form else None)
        if form:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            response = opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError("Oversized human fixture response")
            return response.code, response.headers, data.decode()

    def token(client: str, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        now = int(time.time())
        assertion = sign(
            keys[client],
            {
                "iss": client,
                "sub": client,
                "aud": endpoint,
                "iat": now,
                "exp": now + 60,
                "jti": secrets.token_hex(16),
            },
            kid=None,
        )
        status, _, data = request(
            endpoint,
            {
                **form,
                "client_id": client,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            },
        )
        return status, json.loads(data)

    def code() -> tuple[str, str, str]:
        verifier, state, nonce = (
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(24),
            secrets.token_urlsafe(24),
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        status, headers, html = request(
            issuer
            + "/protocol/openid-connect/auth?"
            + urlencode(
                {
                    "client_id": LOGIN,
                    "redirect_uri": REDIRECT,
                    "response_type": "code",
                    "scope": "openid",
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )
        if status == 200:
            status, headers, _ = request(
                form_action(html, issuer),
                {"username": "fixture-human", "password": password, "credentialId": ""},
            )
        if status != 302:
            raise ValueError("Human login did not return an authorization code")
        return callback_code(headers.get("Location", ""), state, issuer), verifier, nonce

    bad_code, _, _ = code()
    deny(
        *token(
            LOGIN,
            {
                "grant_type": "authorization_code",
                "code": bad_code,
                "redirect_uri": REDIRECT,
                "code_verifier": "wrong-verifier" * 4,
            },
        ),
        "invalid_grant",
    )
    accepted, verifier, nonce = code()
    code_form = {
        "grant_type": "authorization_code",
        "code": accepted,
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
    }
    status, result = token(LOGIN, code_form)
    if status != 200:
        raise ValueError("Human code grant refused")
    verify(
        result["id_token"],
        jwks,
        issuer,
        subject=HUMAN,
        client=LOGIN,
        audience=LOGIN,
        kind="ID",
        nonce=nonce,
    )
    verify(result["access_token"], jwks, issuer, subject=HUMAN, client=LOGIN, audience=DELEGATE)
    exchange = {
        "grant_type": EXCHANGE,
        "subject_token": result["access_token"],
        "subject_token_type": ACCESS,
        "requested_token_type": ACCESS,
        "audience": "perfeng-control-plane",
    }
    for unapproved in (LOGIN, "perfeng-ci"):
        disabled_status, disabled = token(unapproved, exchange)
        deny(disabled_status, disabled, "invalid_request")
        if (
            disabled.get("error_description")
            != "Standard token exchange is not enabled for the requested client"
        ):
            raise ValueError("Expected disabled-exchange reason was not observed")
    status, exchanged = token(DELEGATE, exchange)
    if status != 200 or exchanged.get("refresh_token") or exchanged.get("token_type") != "Bearer":
        raise ValueError("Delegated exchange refused or returned unexpected credential")
    verify(exchanged["access_token"], jwks, issuer, subject=HUMAN, client=DELEGATE)
    try:
        verify(result["id_token"], jwks, issuer, subject=HUMAN, client=DELEGATE)
    except ValueError:
        pass
    else:
        raise ValueError("ID token admitted as API credential")
    for client in (LOGIN, DELEGATE):
        deny(*token(client, {"grant_type": "client_credentials"}), "unauthorized_client")
        deny(
            *token(
                client,
                {"grant_type": "password", "username": "fixture-human", "password": password},
            ),
            "unauthorized_client",
        )
    # Replay can revoke credentials derived from this code; run it after the positive path.
    deny(*token(LOGIN, code_form), "invalid_grant")
    return list(HUMAN_CHECKS)
