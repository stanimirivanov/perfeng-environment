"""Real key lifecycle inside the owned disposable realm, never a production rotator."""

from __future__ import annotations

import http.client
import json
import re
import secrets
import ssl
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa

CLIENT = "fixture-key-rotation"
SUBJECT = "e3295d37-a188-4753-b89c-1ca188d85103"
ADMIN = "/admin/realms/perfeng-fixture"
ISSUER_PATH = "/realms/perfeng-fixture"
CHECKS = [
    "provider-key-overlap",
    "provider-new-key-issuance",
    "provider-old-key-retirement",
    "provider-surviving-key-issuance",
    "fixture-admin-isolation",
]


def configure(config: dict[str, Any], certificate: str) -> None:
    """Give only a separate disposable client the two required realm-admin roles."""
    config["clients"].append(
        {
            "clientId": CLIENT,
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": False,
            "clientAuthenticatorType": "client-jwt",
            "serviceAccountsEnabled": True,
            "standardFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "implicitFlowEnabled": False,
            "fullScopeAllowed": True,
            "defaultClientScopes": ["kc-admin-api"],
            "optionalClientScopes": [],
            "attributes": {
                "jwt.credential.certificate": certificate,
                "token.endpoint.auth.signing.alg": "RS256",
            },
            "protocolMappers": [
                {
                    "name": "realm-admin-roles",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-usermodel-client-role-mapper",
                    "config": {
                        "usermodel.clientRoleMapping.clientId": "realm-management",
                        "claim.name": "resource_access.realm-management.roles",
                        "jsonType.label": "String",
                        "multivalued": "true",
                        "access.token.claim": "true",
                        "id.token.claim": "false",
                    },
                },
            ],
        }
    )
    config["users"].append(
        {
            "id": SUBJECT,
            "username": "service-account-" + CLIENT,
            "enabled": True,
            "serviceAccountClientId": CLIENT,
            "clientRoles": {"realm-management": ["view-realm", "manage-realm"]},
        }
    )


def component_path(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
        raise ValueError("Unsafe key component identity")
    return ADMIN + "/components/" + identifier


def check_keys(old: str, active: str, published: set[str], *, retired: bool) -> None:
    """Expect a different signing key, overlap first and exact old-key removal later."""
    if (
        not old
        or not active
        or old == active
        or active not in published
        or ((old in published) == retired)
    ):
        raise ValueError("Key lifecycle differs from declared expectation")


def check_passive(metadata: dict[str, Any], old: str) -> None:
    matches = [entry for entry in metadata["keys"] if entry.get("kid") == old]
    if len(matches) != 1 or matches[0].get("status") != "PASSIVE":
        raise ValueError("Old key is not explicitly passive")


def qualify(
    issuer: str,
    context: ssl.SSLContext,
    admin_key: rsa.RSAPrivateKey,
    ci_key: rsa.RSAPrivateKey,
    old_token: str,
    original_jwks: dict[str, Any],
    sign: Callable[..., str],
    verify: Callable[..., dict[str, Any]],
) -> list[str]:
    origin = urlsplit(issuer)
    if (
        origin.scheme != "https"
        or origin.hostname != "127.0.0.1"
        or not origin.port
        or origin.path != ISSUER_PATH
        or origin.query
        or origin.fragment
        or origin.username
    ):
        raise ValueError("Foreign rotation fixture refused")
    token_path = ISSUER_PATH + "/protocol/openid-connect/token"
    jwks_path = ISSUER_PATH + "/protocol/openid-connect/certs"

    def request(
        method: str, path: str, *, bearer: str = "", data: Any = None, form: bool = False
    ) -> tuple[int, Any]:
        connection = http.client.HTTPSConnection(
            "127.0.0.1", origin.port, context=context, timeout=5
        )
        headers = {"Authorization": "Bearer " + bearer} if bearer else {}
        body = None
        if data is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
            body = urlencode(data) if form else json.dumps(data)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("Oversized rotation response")
            return response.status, json.loads(raw) if raw else None
        finally:
            connection.close()

    def credential(client: str, key: rsa.RSAPrivateKey) -> str:
        now = int(time.time())
        assertion = sign(
            key,
            {
                "iss": client,
                "sub": client,
                "aud": issuer + "/protocol/openid-connect/token",
                "iat": now,
                "exp": now + 60,
                "jti": secrets.token_hex(16),
            },
            kid=None,
        )
        status, result = request(
            "POST",
            token_path,
            form=True,
            data={
                "grant_type": "client_credentials",
                "client_id": client,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            },
        )
        if status != 200 or not isinstance(result, dict) or result.get("token_type") != "Bearer":
            raise ValueError("Rotation credential refused")
        return result["access_token"]

    def read(path: str, bearer: str = "") -> Any:
        status, result = request("GET", path, bearer=bearer)
        if status != 200:
            raise ValueError(
                "Rotation observation refused at "
                + (
                    "keys"
                    if path.endswith("/keys")
                    else "component"
                    if "/components/" in path
                    else "realm-or-JWKS"
                )
                + " status "
                + str(status)
            )
        return result

    def kid(token: str) -> str:
        import base64

        head = token.split(".")[0]
        value = json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)))["kid"]
        if not isinstance(value, str) or not 1 <= len(value) <= 256:
            raise ValueError("Invalid observed key identity")
        return value

    def published(keys: dict[str, Any]) -> set[str]:
        return {
            key["kid"]
            for key in keys["keys"]
            if key.get("alg") == "RS256" and key.get("use") == "sig"
        }

    verify(old_token, original_jwks, issuer)
    old = kid(old_token)
    admin = credential(CLIENT, admin_key)
    verify(
        admin,
        original_jwks,
        issuer,
        subject=SUBJECT,
        client=CLIENT,
        audience=issuer.removesuffix(ISSUER_PATH) + ADMIN,
    )
    metadata = read(ADMIN + "/keys", admin)
    if metadata["active"].get("RS256") != old:
        raise ValueError("Initial provider and verified token key disagree")
    matches = [
        entry
        for entry in metadata["keys"]
        if entry["kid"] == old and entry.get("algorithm") == "RS256"
    ]
    if len(matches) != 1:
        raise ValueError("Ambiguous old key provider")
    old_path = component_path(matches[0]["providerId"])
    realm_id = read(ADMIN, admin)["id"]
    component = read(old_path, admin)
    if (
        component.get("parentId") != realm_id
        or component.get("providerId") != "rsa-generated"
        or component.get("providerType") != "org.keycloak.keys.KeyProvider"
    ):
        raise ValueError("Old component is not the owned RSA provider")
    denied, _ = request("GET", ADMIN + "/keys", bearer=old_token)
    if denied not in (401, 403):
        raise ValueError("CI received administrative authority")
    status, _ = request(
        "POST",
        ADMIN + "/components",
        bearer=admin,
        data={
            "name": "fixture-rotated-rsa",
            "parentId": realm_id,
            "providerId": "rsa-generated",
            "providerType": "org.keycloak.keys.KeyProvider",
            "config": {
                "priority": ["200"],
                "enabled": ["true"],
                "active": ["true"],
                "algorithm": ["RS256"],
                "keySize": ["2048"],
            },
        },
    )
    if status != 201:
        raise ValueError("New key provider creation refused")
    component["config"]["active"] = ["false"]
    status, _ = request("PUT", old_path, bearer=admin, data=component)
    if status != 204:
        raise ValueError("Old provider passive transition refused")
    metadata = read(ADMIN + "/keys", admin)
    check_passive(metadata, old)
    new = metadata["active"]["RS256"]
    overlap = read(jwks_path)
    check_keys(old, new, published(overlap), retired=False)
    rotated = credential("perfeng-ci", ci_key)
    verify(rotated, overlap, issuer)
    verify(old_token, overlap, issuer)
    if kid(rotated) != new:
        raise ValueError("New issuance did not use new active key")
    # Obtain administration under the new key before retiring the old one.
    admin = credential(CLIENT, admin_key)
    verify(
        admin,
        overlap,
        issuer,
        subject=SUBJECT,
        client=CLIENT,
        audience=issuer.removesuffix(ISSUER_PATH) + ADMIN,
    )
    status, _ = request("DELETE", old_path, bearer=admin)
    if status != 204:
        raise ValueError("Old key retirement refused")
    retired = read(jwks_path)
    check_keys(old, new, published(retired), retired=True)
    # Prove refusal is key retirement, not an expired/otherwise invalid token.
    verify(old_token, original_jwks, issuer)
    try:
        verify(old_token, retired, issuer)
    except ValueError:
        pass
    else:
        raise ValueError("Retired token accepted by fresh JWKS")
    verify(rotated, retired, issuer)
    surviving = credential("perfeng-ci", ci_key)
    verify(surviving, retired, issuer)
    if kid(surviving) != new:
        raise ValueError("Surviving issuance changed active key")
    return list(CHECKS)
