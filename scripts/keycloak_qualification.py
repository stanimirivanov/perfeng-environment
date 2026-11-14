"""Opt-in, disposable Keycloak identity qualification; never enable serving."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import http.client
import ipaddress
import json
import os
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from scripts import keycloak_human as human
from scripts import keycloak_rotation as rotation

LOCK = Path(__file__).resolve().parents[1] / "tests/qualification/keycloak/fixture.lock.json"
CI_CLIENT = "perfeng-ci"
CI_SUBJECT = "e3295d37-a188-4753-b89c-1ca188d85101"
AUDIENCE = "perfeng-control-plane"
TOKEN_PATH = "/realms/perfeng-fixture/protocol/openid-connect/token"


def encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def sign(key: rsa.RSAPrivateKey, claims: dict[str, Any], kid: str | None = "test") -> str:
    """Sign only ephemeral fixture assertions; no secret reaches argv or output."""
    content = (
        encode(json.dumps({"alg": "RS256", "typ": "JWT", **({"kid": kid} if kid else {})}).encode())
        + "."
        + encode(json.dumps(claims).encode())
    )
    return content + "." + encode(key.sign(content.encode(), padding.PKCS1v15(), hashes.SHA256()))


def realm(certificate: str) -> dict[str, Any]:
    """Keep machine/human capabilities disjoint; no bootstrap administrator."""
    clients = []
    for name in (CI_CLIENT, "perfeng-console-login", "perfeng-console-delegate", AUDIENCE):
        clients.append(
            {
                "clientId": name,
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "bearerOnly": name == AUDIENCE,
                "clientAuthenticatorType": "client-jwt",
                "standardFlowEnabled": name == "perfeng-console-login",
                "serviceAccountsEnabled": name == CI_CLIENT,
                "directAccessGrantsEnabled": False,
                "implicitFlowEnabled": False,
                "fullScopeAllowed": False,
                "defaultClientScopes": [],
                "optionalClientScopes": [],
                "redirectUris": ["https://127.0.0.1/callback"]
                if name == "perfeng-console-login"
                else [],
                "attributes": {
                    "jwt.credential.certificate": certificate if name == CI_CLIENT else "",
                    "token.endpoint.auth.signing.alg": "RS256",
                    "pkce.code.challenge.method": "S256",
                },
                "protocolMappers": [
                    {
                        "name": "api-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.client.audience": AUDIENCE,
                            "access.token.claim": "true",
                            "id.token.claim": "false",
                        },
                    }
                ]
                if name == CI_CLIENT
                else [],
            }
        )
    return {
        "realm": "perfeng-fixture",
        "enabled": True,
        "sslRequired": "all",
        "accessTokenLifespan": 300,
        "registrationAllowed": False,
        "clients": clients,
        "users": [
            {
                "id": CI_SUBJECT,
                "username": "service-account-perfeng-ci",
                "enabled": True,
                "serviceAccountClientId": CI_CLIENT,
            }
        ],
    }


def verify(
    token: str,
    jwks: dict[str, Any],
    issuer: str,
    *,
    subject: str = CI_SUBJECT,
    client: str = CI_CLIENT,
    audience: str = AUDIENCE,
    kind: str = "Bearer",
    nonce: str | None = None,
) -> dict[str, Any]:
    """Verify a declared exact fixture profile; never infer expectations from output."""
    stage = "envelope"
    try:
        if len(token) > 16384:
            raise ValueError
        head, body, signature = token.split(".")
        header, claims = json.loads(decode(head)), json.loads(decode(body))
        if (
            set(header) != {"alg", "typ", "kid"}
            or header["alg"] != "RS256"
            or header["typ"] != "JWT"
        ):
            raise ValueError
        stage = "signing-key"
        keys = [
            k
            for k in jwks["keys"]
            if k.get("kid") == header["kid"]
            and k.get("kty") == "RSA"
            and k.get("use") == "sig"
            and k.get("alg") == "RS256"
        ]
        if len(keys) != 1:
            raise ValueError
        n, e = (int.from_bytes(decode(keys[0][name])) for name in ("n", "e"))
        if not 2048 <= n.bit_length() <= 8192 or e < 3 or e % 2 == 0:
            raise ValueError
        rsa.RSAPublicNumbers(e, n).public_key().verify(
            decode(signature), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        stage = "identity-and-audience"
        observed_audience = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        for field, expected in {"iss": issuer, "sub": subject, "azp": client, "typ": kind}.items():
            stage = field
            if claims.get(field) != expected:
                raise ValueError
        stage = "audience"
        if observed_audience != [audience]:
            raise ValueError
        stage = "nonce"
        if nonce is not None and claims.get("nonce") != nonce:
            raise ValueError
        stage = "time-bounds"
        issued, expires = claims["iat"], claims["exp"]
        not_before = claims.get("nbf", 0)
        if (
            type(issued) is not int
            or type(expires) is not int
            or not 0 < expires - issued <= 300
            or not time.time() - 330 <= issued <= time.time() + 30
            or expires < time.time() - 30
        ):
            raise ValueError
        if type(not_before) is not int or not_before > time.time() + 30 or not_before >= expires:
            raise ValueError
        return claims
    except (ValueError, KeyError, TypeError, InvalidSignature) as error:
        raise ValueError("Fixture token profile refused at " + stage + " for " + client) from error


def receipt(checks: list[str]) -> dict[str, Any]:
    return {
        "version": 1,
        "image": json.loads(LOCK.read_bytes())["image"],
        "admission": "NOT_ADMITTED",
        "passedChecks": checks,
        "remainingGates": [
            *(
                []
                if set(human.HUMAN_CHECKS).issubset(checks)
                else ["human-login-and-delegate-exchange"]
            ),
            "control-plane-real-token-conformance",
            *(
                ["control-plane-real-key-rotation"]
                if set(rotation.CHECKS).issubset(checks)
                else ["real-key-rotation"]
            ),
            "BFF-browser-session-and-shared-pilot",
        ],
    }


def require_denial(status: int, response: dict[str, Any], expected: str) -> None:
    """A generic malformed request is not proof of a denied grant or credential."""
    if status not in (400, 401) or response.get("error") != expected:
        raise ValueError("Expected OAuth refusal was not observed")


def docker(*args: str) -> str:
    """Bound subprocesses and suppress diagnostics that could contain secrets."""
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode:
        raise ValueError("Docker fixture operation failed")
    return result.stdout.strip()


def require_local_docker() -> None:
    """Remote Docker contexts are outside this loopback-only authorization."""
    override = os.environ.get("DOCKER_HOST", "")
    if override and not override.startswith("unix:///"):
        raise ValueError("Remote Docker endpoint refused")
    endpoint = docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
    if not endpoint.startswith("unix:///"):
        raise ValueError("Remote Docker context refused")


def execute() -> dict[str, Any]:
    """Own only one uniquely labelled container and a private temporary directory."""
    lock = json.loads(LOCK.read_bytes())
    require_local_docker()
    docker("image", "inspect", lock["image"])
    if os.getuid() != 1000:
        raise ValueError("Fixture requires UID 1000 for private mounted credentials")
    with tempfile.TemporaryDirectory(prefix="perfeng-keycloak-", dir="/dev/shm") as temporary:
        root = Path(temporary)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "disposable-perfeng")])
        now = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(tls_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False,
            )
            .sign(tls_key, hashes.SHA256())
        )
        pem = cert.public_bytes(serialization.Encoding.PEM)
        files = {
            "tls.pem": pem,
            "tls.key": tls_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        }
        import_dir = root / "import"
        import_dir.mkdir(mode=0o700)

        def certificate(signing_key: rsa.RSAPrivateKey) -> str:
            cert = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(signing_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(minutes=1))
                .not_valid_after(now + dt.timedelta(hours=1))
                .sign(signing_key, hashes.SHA256())
            )
            return base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode("ascii")

        human_keys = {
            client: rsa.generate_private_key(public_exponent=65537, key_size=2048)
            for client in (human.LOGIN, human.DELEGATE)
        }
        rotation_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        password = secrets.token_urlsafe(32)
        config = realm(certificate(key))
        human.configure(
            config,
            {client: certificate(signing_key) for client, signing_key in human_keys.items()},
            password,
        )
        rotation.configure(config, certificate(rotation_key))
        files["import/perfeng-fixture-realm.json"] = json.dumps(config).encode()
        for path, content in files.items():
            target = root / path
            target.touch(mode=0o600)
            target.write_bytes(content)
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        issuer = f"https://127.0.0.1:{port}/realms/perfeng-fixture"
        context = ssl.create_default_context(cadata=pem.decode())

        def request(path: str, form: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
            connection = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=5)
            try:
                connection.request(
                    "POST" if form else "GET",
                    path,
                    urlencode(form) if form else None,
                    {"Content-Type": "application/x-www-form-urlencoded"} if form else {},
                )
                response = connection.getresponse()
                body = response.read(65537)
                if len(body) > 65536:
                    raise ValueError("Oversized fixture response")
                return response.status, json.loads(body)
            finally:
                connection.close()

        container = ""
        nonce = secrets.token_hex(8)
        try:
            container = docker(
                "create",
                "--name",
                f"perfeng-keycloak-{nonce}",
                "--label",
                f"perfeng.keycloak.fixture={nonce}",
                "--platform",
                lock["platform"],
                "--publish",
                f"127.0.0.1:{port}:8443",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--memory=1g",
                "--cpus=2",
                "--pids-limit=256",
                "--log-driver=none",
                "--tmpfs",
                "/opt/keycloak/data:rw,uid=1000,mode=0700",
                "--tmpfs",
                "/tmp:rw,uid=1000,mode=0700",
                "--volume",
                f"{root}:/fixture:ro",
                "--volume",
                f"{import_dir}:/opt/keycloak/data/import:ro",
                lock["image"],
                "start",
                "--db=dev-file",
                "--cache=local",
                "--http-enabled=false",
                f"--hostname=https://127.0.0.1:{port}",
                "--https-certificate-file=/fixture/tls.pem",
                "--https-certificate-key-file=/fixture/tls.key",
                "--import-realm",
            )
            docker("start", container)
            deadline = time.monotonic() + 90
            while True:
                if docker("inspect", "--format", "{{.State.Running}}", container) != "true":
                    raise ValueError("Keycloak exited before readiness; no admission")
                try:
                    status, _ = request("/realms/perfeng-fixture/.well-known/openid-configuration")
                    if status == 200:
                        break
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                if time.monotonic() >= deadline:
                    raise ValueError("Keycloak readiness timed out; no admission")
                time.sleep(0.5)

            def grant(
                grant_type: str, signing_key: rsa.RSAPrivateKey = key
            ) -> tuple[int, dict[str, Any]]:
                assertion = sign(
                    signing_key,
                    {
                        "iss": CI_CLIENT,
                        "sub": CI_CLIENT,
                        "aud": issuer + "/protocol/openid-connect/token",
                        "iat": int(time.time()),
                        "exp": int(time.time()) + 60,
                        "jti": secrets.token_hex(16),
                    },
                    kid=None,
                )
                return request(
                    TOKEN_PATH,
                    {
                        "grant_type": grant_type,
                        "client_id": CI_CLIENT,
                        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                        "client_assertion": assertion,
                    },
                )

            status, result = grant("client_credentials")
            if status != 200:
                description = str(result.get("error_description", ""))
                category = next(
                    (
                        value
                        for value in (
                            "Unable to load public key",
                            "Signature",
                            "audience",
                            "expired",
                            "Invalid client credentials",
                        )
                        if value in description
                    ),
                    "unclassified",
                )
                raise ValueError("Machine credential grant refused: " + category)
            key_status, jwks = request("/realms/perfeng-fixture/protocol/openid-connect/certs")
            if key_status != 200:
                raise ValueError("Key retrieval refused")
            if result.get("token_type") != "Bearer" or not isinstance(
                result.get("access_token"), str
            ):
                raise ValueError("Machine response is not an access credential")
            verify(result["access_token"], jwks, issuer)
            for grant_type, signing_key in [
                ("password", key),
                (
                    "client_credentials",
                    rsa.generate_private_key(public_exponent=65537, key_size=2048),
                ),
            ]:
                status, response = grant(grant_type, signing_key)
                require_denial(
                    status,
                    response,
                    "unauthorized_client" if grant_type == "password" else "invalid_client",
                )
            human_checks = human.qualify(
                issuer,
                context,
                {**human_keys, CI_CLIENT: key},
                password,
                jwks,
                sign,
                verify,
                require_denial,
            )
            rotation_checks = rotation.qualify(
                issuer, context, rotation_key, key, result["access_token"], jwks, sign, verify
            )
            return receipt(
                [
                    "loopback-TLS",
                    "private-key-jwt-machine-profile",
                    "password-grant-denied",
                    "wrong-key-denied",
                    *human_checks,
                    *rotation_checks,
                ]
            )
        finally:
            if not container:
                container = docker(
                    "ps",
                    "-aq",
                    "--no-trunc",
                    "--filter",
                    f"label=perfeng.keycloak.fixture={nonce}",
                    "--filter",
                    f"name=^/perfeng-keycloak-{nonce}$",
                )
                if "\n" in container:
                    raise ValueError("Ambiguous fixture ownership; cleanup refused")
            if container:
                label = docker(
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "perfeng.keycloak.fixture" }}',
                    container,
                )
                if label != nonce:
                    raise ValueError("Fixture ownership changed; cleanup refused")
                docker("rm", "--force", container)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        print(
            json.dumps(
                {
                    "mode": "offline-preview",
                    "image": json.loads(LOCK.read_bytes())["image"],
                    "admission": "NOT_ADMITTED",
                }
            )
        )
        return 0
    try:
        print(json.dumps(execute(), sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError):
        print("Keycloak qualification failed; no admission", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
