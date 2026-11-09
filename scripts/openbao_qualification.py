"""Qualify a pinned, loopback-only OpenBao instance with synthetic JWT identities."""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import platform
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from scripts.contracts_distribution_tools import checked_bytes, local_bytes

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "tests/qualification/openbao/fixture.lock.json"
WORKLOAD_SECRET = "fixture/data/workload"
CI_SECRET = "fixture/data/ci"
AUDIENCE = "perfeng-openbao-fixture"


def stage_binary(source: Path, directory: Path) -> Path:
    """Copy only an independently pinned executable into a private directory."""
    lock = json.loads(LOCK.read_bytes())
    if f"{sys.platform}-{platform.machine()}" != lock["platform"]:
        raise ValueError("Unsupported OpenBao fixture platform")
    content = checked_bytes(source, lock["executableSha256"], limit=256 * 1024 * 1024)
    target = directory / "bao"
    with target.open("xb") as stream:
        stream.write(content)
    target.chmod(0o700)
    return target


def jwt(key: rsa.RSAPrivateKey, claims: dict[str, Any]) -> str:
    """Sign a short-lived test token. The caller retains the ephemeral key."""

    def encode(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    now = int(time.time())
    payload = json.dumps({**claims, "iat": now, "exp": now + 120}).encode()
    signed = encode(b'{"alg":"RS256","typ":"JWT"}') + b"." + encode(payload)
    signature = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    return (signed + b"." + encode(signature)).decode("ascii")


class Bao:
    """Use only a fixed loopback address and never follow redirects."""

    def __init__(self, port: int) -> None:
        self.port = port

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        data: dict[str, Any] | None = None,
        statuses: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        try:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["X-Vault-Token"] = token
            connection.request(
                method, f"/v1/{path}", json.dumps(data) if data is not None else None, headers
            )
            response = connection.getresponse()
            if response.status not in statuses:
                raise ValueError(f"OpenBao fixture {method} {path} returned HTTP {response.status}")
            if response.status >= 400:
                return {}
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise ValueError("OpenBao fixture response exceeds the size limit")
            return json.loads(body) if body else {}
        finally:
            connection.close()


def child_environment(directory: Path) -> dict[str, str]:
    return {"HOME": str(directory), "TMPDIR": str(directory)}


@contextmanager
def server(binary: Path, directory: Path) -> Iterator[Bao]:
    """Start and clean up exactly one disposable in-memory server."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = directory / "server.hcl"
    config.write_text(
        f'disable_mlock = true\napi_addr = "http://127.0.0.1:{port}"\n'
        'storage "inmem" {}\nlistener "tcp" {\n'
        f'  address = "127.0.0.1:{port}"\n  tls_disable = true\n}}\n',
        encoding="utf-8",
    )
    with subprocess.Popen(
        [str(binary), "server", "-config", str(config)],
        cwd=directory,
        env=child_environment(directory),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            api = Bao(port)
            deadline = time.monotonic() + 15
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    initialized = api.request("GET", "sys/init")["initialized"]
                except (OSError, http.client.HTTPException):
                    time.sleep(0.05)
                    continue
                if initialized:
                    raise ValueError("OpenBao fixture endpoint is already initialized")
                yield api
                break
            else:
                raise ValueError("Disposable OpenBao server did not become ready")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def configure_role(
    api: Bao, root: str, role: str, claims: dict[str, str], secret_path: str
) -> rsa.RSAPrivateKey:
    """Install one JWT issuer and one exact read-only KV policy."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )
    api.request(
        "POST",
        f"sys/policies/acl/{role}",
        token=root,
        statuses=(204,),
        data={
            "policy": f'path "{secret_path}" {{ capabilities = ["read"] }}',
        },
    )
    api.request("POST", f"sys/auth/{role}", token=root, statuses=(204,), data={"type": "jwt"})
    api.request(
        "POST",
        f"auth/{role}/config",
        token=root,
        statuses=(204,),
        data={
            "bound_issuer": claims["iss"],
            "jwt_validation_pubkeys": [public],
            "jwt_supported_algs": ["RS256"],
        },
    )
    api.request(
        "POST",
        f"auth/{role}/role/fixture",
        token=root,
        statuses=(204,),
        data={
            "role_type": "jwt",
            "user_claim": "sub",
            "bound_subject": claims["sub"],
            "bound_audiences": [AUDIENCE],
            "bound_claims": {
                name: value for name, value in claims.items() if name not in {"iss", "sub", "aud"}
            },
            "token_policies": [role],
            "token_no_default_policy": True,
            "token_type": "service",
            "token_ttl": "120s",
            "token_max_ttl": "120s",
        },
    )
    return key


def login(
    api: Bao, role: str, key: rsa.RSAPrivateKey, claims: dict[str, Any], *, denied: bool = False
) -> dict[str, Any]:
    return api.request(
        "POST",
        f"auth/{role}/login",
        data={"role": "fixture", "jwt": jwt(key, claims)},
        statuses=(400, 403) if denied else (200,),
    )


def private_read(path: Path) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Agent delivery is not a private regular file")
    return local_bytes(path, limit=4096).decode("ascii")


def deliver(binary: Path, directory: Path, api: Bao, signed: str, expected: str) -> str:
    """Run one bounded Agent delivery and return its token for revocation checks."""
    directory.mkdir(mode=0o700)
    jwt_path = directory / "jwt"
    fd = jwt_path.open("x", encoding="ascii")
    with fd:
        jwt_path.chmod(0o600)
        fd.write(signed)
    token_path = directory / "token"
    output = directory / "credential"
    config = directory / "agent.hcl"
    config.write_text(
        "exit_after_auth = true\n"
        f'vault {{ address = "http://127.0.0.1:{api.port}" }}\n'
        'auto_auth {\n  method "jwt" {\n    mount_path = "auth/workload"\n'
        f'    config = {{ path = {json.dumps(str(jwt_path))}, role = "fixture" }}\n'
        '  }\n  sink "file" {\n'
        f"    config = {{ path = {json.dumps(str(token_path))}, mode = 384 }}\n"
        "  }\n}\n"
        "template {\n  contents = <<EOT\n"
        '{{ with secret "fixture/data/workload" }}{{ .Data.data.credential }}{{ end }}\n'
        f'EOT\n  destination = {json.dumps(str(output))}\n  perms = "0600"\n}}\n',
        encoding="utf-8",
    )
    subprocess.run(
        [str(binary), "agent", "-config", str(config)],
        cwd=directory,
        env=child_environment(directory),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=True,
    )
    if not secrets.compare_digest(private_read(output).strip(), expected):
        raise ValueError("Agent rendered a different credential")
    token = private_read(token_path)
    token_path.unlink()
    output.unlink()
    return token


def exercise(binary: Path, directory: Path, api: Bao) -> None:
    init = api.request("POST", "sys/init", data={"secret_shares": 1, "secret_threshold": 1})
    root = init["root_token"]
    api.request("POST", "sys/unseal", data={"key": init["keys"][0]})
    api.request(
        "POST",
        "sys/mounts/fixture",
        token=root,
        statuses=(204,),
        data={
            "type": "kv",
            "options": {"version": "2"},
        },
    )
    workload = {
        "iss": "https://kubernetes.fixture.invalid",
        "sub": "system:serviceaccount:perfeng:registry",
        "aud": AUDIENCE,
    }
    ci = {
        "iss": "https://actions.fixture.invalid",
        "sub": "repo:stanimirivanov/perfeng-environment:environment:fixture",
        "aud": AUDIENCE,
        "repository": "stanimirivanov/perfeng-environment",
        "ref": "refs/heads/main",
        "environment": "fixture",
    }
    workload_key = configure_role(api, root, "workload", workload, WORKLOAD_SECRET)
    ci_key = configure_role(api, root, "ci", ci, CI_SECRET)
    for role, key, claims, overrides in (
        (
            "workload",
            workload_key,
            workload,
            {
                "sub": "system:serviceaccount:other:registry",
                "aud": "other-audience",
                "iss": "https://untrusted.fixture.invalid",
            },
        ),
        (
            "ci",
            ci_key,
            ci,
            {
                "repository": "other/perfeng-environment",
                "ref": "refs/heads/unreviewed",
                "environment": "unprotected",
                "aud": "other-audience",
            },
        ),
    ):
        for name, wrong in overrides.items():
            login(api, role, key, {**claims, name: wrong}, denied=True)
    login(api, "workload", ci_key, workload, denied=True)

    first, replacement, ci_value = (secrets.token_urlsafe(32) for _ in range(3))
    for path, credential in ((WORKLOAD_SECRET, first), (CI_SECRET, ci_value)):
        api.request("POST", path, token=root, data={"data": {"credential": credential}})
    ci_token = login(api, "ci", ci_key, ci)["auth"]["client_token"]
    if not secrets.compare_digest(
        api.request("GET", CI_SECRET, token=ci_token)["data"]["data"]["credential"], ci_value
    ):
        raise ValueError("CI identity did not receive its own credential")
    api.request("GET", WORKLOAD_SECRET, token=ci_token, statuses=(403,))
    old_token = deliver(binary, directory / "first", api, jwt(workload_key, workload), first)
    api.request("GET", CI_SECRET, token=old_token, statuses=(403,))
    api.request("POST", WORKLOAD_SECRET, token=old_token, data={"data": {}}, statuses=(403,))
    api.request("GET", "fixture/metadata?list=true", token=old_token, statuses=(403,))

    api.request("POST", WORKLOAD_SECRET, token=root, data={"data": {"credential": replacement}})
    new_token = deliver(
        binary, directory / "replacement", api, jwt(workload_key, workload), replacement
    )
    if secrets.compare_digest(old_token, new_token):
        raise ValueError("Agent restart did not obtain a new token")
    api.request("POST", "auth/token/revoke", token=root, data={"token": old_token}, statuses=(204,))
    api.request("GET", WORKLOAD_SECRET, token=old_token, statuses=(403,))
    if not secrets.compare_digest(
        api.request("GET", WORKLOAD_SECRET, token=new_token)["data"]["data"]["credential"],
        replacement,
    ):
        raise ValueError("Replacement token did not retain access after revocation")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bao", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args()
    try:
        with (
            tempfile.TemporaryDirectory(prefix="perfeng-openbao-tool-") as tool,
            tempfile.TemporaryDirectory(
                prefix="perfeng-openbao-fixture-", dir="/dev/shm"
            ) as scratch,
        ):
            binary = stage_binary(args.bao, Path(tool))
            directory = Path(scratch)
            with server(binary, directory) as api:
                exercise(binary, directory, api)
    except ValueError as exc:
        print(f"openbao-qualification: {exc}", file=sys.stderr)
        return 1
    except (OSError, KeyError, http.client.HTTPException, subprocess.SubprocessError):
        print("openbao-qualification: local fixture process or API failed", file=sys.stderr)
        return 1
    print(
        "OpenBao qualification passed for synthetic JWT identities and local Agent delivery, rotation and revocation."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
