"""Opt-in composition client for one immutable Console SDK; never component implementation."""

from __future__ import annotations

import asyncio
import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPSHandler, ProxyHandler, Request, build_opener

from scripts import keycloak_human as human

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "tests/qualification/keycloak/console.lock.json"
CONSUMER = LOCK.with_name("bff-consumer.mjs")
CHECKS = [
    "BFF-state-cookie-admission",
    "BFF-PKCE-code-exchange",
    "signed-nonce-subject",
    "replay-refused",
    "run-return-intent",
]
PASSED = {
    "profile": "perfeng-bff-keycloak-transaction-v1",
    "status": "PASS",
    "admission": "NOT_ADMITTED",
    "checks": CHECKS,
}


def read_pin() -> dict[str, str]:
    """Read only the repository-reviewed selection and independent expectations."""
    pin = json.loads(LOCK.read_bytes())
    if (
        not isinstance(pin, dict)
        or set(pin)
        != {"repository", "commit", "lockSha256", "node", "pnpm", "effect", "subject", "runId"}
        or not all(isinstance(value, str) and value for value in pin.values())
    ):
        raise ValueError("Console selection refused")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", pin["commit"])
        or not re.fullmatch(r"[0-9a-f]{64}", pin["lockSha256"])
        or pin["subject"] != human.HUMAN
    ):
        raise ValueError("Console selection refused")
    return pin


def git(source: Path, *args: str) -> bytes:
    """Read local Git objects only, with no credential or network operation."""
    return subprocess.check_output(
        ["git", "-C", str(source), *args], stderr=subprocess.DEVNULL, timeout=5
    )


def preview(source: Path) -> dict[str, str]:
    """Offline source/pin validation; does not import SDK modules or access Docker/Node."""
    pin = read_pin()
    if (
        git(source, "remote", "get-url", "origin").decode().strip() != pin["repository"]
        or git(source, "rev-parse", "HEAD").decode().strip() != pin["commit"]
        or git(source, "status", "--porcelain", "--untracked-files=no")
        or git(
            source, "ls-files", "--others", "--exclude-standard", "--", "src/bff", "src/generated"
        )
    ):
        raise ValueError("Console source selection refused")
    lock = git(source, "show", pin["commit"] + ":pnpm-lock.yaml")
    package = json.loads(git(source, "show", pin["commit"] + ":package.json"))
    if (
        hashlib.sha256(lock).hexdigest() != pin["lockSha256"]
        or package.get("engines", {}).get("node") != pin["node"]
        or package.get("packageManager") != "pnpm@" + pin["pnpm"]
        or package.get("dependencies", {}).get("effect") != pin["effect"]
    ):
        raise ValueError("Console toolchain selection refused")
    return {
        "consoleCommit": pin["commit"],
        "consoleLockSha256": pin["lockSha256"],
        "admission": "NOT_ADMITTED",
    }


def preflight(source: Path) -> dict[str, str]:
    """Refuse source/runtime problems before provisioning the disposable provider."""
    selected = preview(source)
    pin = read_pin()
    node = shutil.which("node")
    if (
        not node
        or subprocess.check_output([node, "--version"], env={}, timeout=3).strip()
        != ("v" + pin["node"]).encode()
    ):
        raise ValueError("Pinned Console Node runtime required")
    package = json.loads(git(source, "show", pin["commit"] + ":package.json"))
    for name in ("effect", "@effect/platform-node", "jose", "jsonc-parser"):
        installed = json.loads((source / "node_modules" / name / "package.json").read_bytes())
        if (
            not isinstance(installed, dict)
            or installed.get("version") != package["dependencies"][name]
        ):
            raise ValueError("Frozen Console dependency installation required")
    paths = [
        "scripts/keycloak_qualification.py",
        "scripts/keycloak_human.py",
        "scripts/keycloak_rotation.py",
        "scripts/keycloak_bff.py",
        "tests/qualification/keycloak",
    ]
    if git(ROOT, "status", "--porcelain", "--untracked-files=normal", "--", *paths):
        raise ValueError("Commit BFF qualification sources before execution")
    return selected


def export_console(source: Path, target: Path, pin: dict[str, str]) -> str:
    """Export immutable regular Git objects; only frozen installed dependencies are linked."""
    target.mkdir(mode=0o700)
    entries = git(
        source,
        "ls-tree",
        "-rz",
        pin["commit"],
        "--",
        "package.json",
        "pnpm-lock.yaml",
        "src/bff/login",
        "src/generated/validators.mjs",
    )
    digest = hashlib.sha256(b"perfeng-bff-sdk-export-v1\0")
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, _object = metadata.split()
        name = raw_name.decode("utf-8")
        path = Path(name)
        if (
            mode not in (b"100644", b"100755")
            or kind != b"blob"
            or path.is_absolute()
            or ".." in path.parts
        ):
            raise ValueError("Console source export refused")
        content = git(source, "show", pin["commit"] + ":" + name)
        output = target / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        digest.update(
            len(raw_name).to_bytes(8, "big") + raw_name + len(content).to_bytes(8, "big") + content
        )
    if not (source / "node_modules").is_dir():
        raise ValueError("Frozen Console dependency installation required")
    (target / "node_modules").symlink_to(
        source.resolve() / "node_modules", target_is_directory=True
    )
    return digest.hexdigest()


def authorization_state(url: str, issuer: str) -> str:
    """Observe BFF-produced fields without choosing state, nonce, verifier or a destination."""
    parsed, origin = urlsplit(url), urlsplit(issuer)
    values = parse_qs(parsed.query, keep_blank_values=True)
    if (
        len(url) > 8192
        or (parsed.scheme, parsed.netloc, parsed.path)
        != ("https", origin.netloc, origin.path + "/protocol/openid-connect/auth")
        or parsed.fragment
        or set(values)
        != {
            "client_id",
            "redirect_uri",
            "response_type",
            "scope",
            "state",
            "nonce",
            "code_challenge",
            "code_challenge_method",
        }
        or any(len(value) != 1 for value in values.values())
    ):
        raise ValueError("BFF authorization URL refused")
    if any(
        values[name] != [expected]
        for name, expected in {
            "client_id": human.LOGIN,
            "redirect_uri": human.BFF_REDIRECT,
            "response_type": "code",
            "scope": "openid",
            "code_challenge_method": "S256",
        }.items()
    ) or any(
        not re.fullmatch(r"[A-Za-z0-9_-]{43}", values[name][0])
        for name in ("state", "nonce", "code_challenge")
    ):
        raise ValueError("BFF authorization profile refused")
    return values["state"][0]


def authorizer(issuer: str, context: ssl.SSLContext, password: str) -> Callable[[str], str]:
    """Drive only the fixture person's login form; never exchange its returned code."""
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=context),
        HTTPCookieProcessor(http.cookiejar.CookieJar()),
        human.NoRedirect(),
    )

    def request(url: str, form: dict[str, str] | None = None):
        message = Request(url, data=urlencode(form).encode() if form else None)
        if form:
            message.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            response = opener.open(message, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            end = time.monotonic() + 5
            data = bytearray()
            while True:
                if time.monotonic() >= end:
                    raise ValueError("BFF login response deadline")
                chunk = response.read1(65537 - len(data))
                data.extend(chunk)
                if len(data) > 65536:
                    raise ValueError("BFF login response bound")
                if not chunk:
                    return response.code, response.headers, data.decode("utf-8", errors="strict")

    def authorize(url: str) -> str:
        state = authorization_state(url, issuer)
        status, headers, html = request(url)
        if status == 200:
            status, headers, _ = request(
                human.form_action(html, issuer),
                {"username": "fixture-human", "password": password, "credentialId": ""},
            )
        if status != 302:
            raise ValueError("BFF human login refused")
        location = headers.get("Location", "")
        human.callback_code(location, state, issuer, redirect=human.BFF_REDIRECT)
        target = urlsplit(location)
        return target.path + "?" + target.query

    return authorize


def unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Refuse ambiguous child JSON before any field is trusted."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate child member")
        result[key] = value
    return result


async def consume(
    command: list[str],
    start: dict[str, str],
    authorize: Callable[[str], str],
    *,
    timeout: float = 30,
) -> dict[str, object]:
    """Bound a two-phase private process; kill/reap before exposing a safe failure."""
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", os.defpath)},
        limit=8192,
    )
    errors: asyncio.Task[bytes] | None = None

    async def exchange(
        stdin: asyncio.StreamWriter, stdout: asyncio.StreamReader, diagnostics: asyncio.Task[bytes]
    ):
        output = 0
        sent = 0

        async def reply():
            nonlocal output
            line = await stdout.readline()
            output += len(line)
            if not line.endswith(b"\n") or output > 8192:
                raise ValueError("Child output refused")
            return json.loads(line, object_pairs_hook=unique_members)

        async def write(value):
            nonlocal sent
            content = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
            sent += len(content)
            if sent > 65536:
                raise ValueError("Child input refused")
            stdin.write(content)
            await stdin.drain()

        await write(start)
        requested = await reply()
        if (
            not isinstance(requested, dict)
            or set(requested) != {"phase", "authorizationUrl"}
            or requested["phase"] != "authorize"
            or not isinstance(requested["authorizationUrl"], str)
        ):
            raise ValueError("Child phase refused")
        target = await asyncio.to_thread(authorize, requested["authorizationUrl"])
        await write({"phase": "callback", "target": target})
        stdin.close()
        result = await reply()
        if (
            result != PASSED
            or await stdout.read(1)
            or await diagnostics
            or await process.wait() != 0
        ):
            raise ValueError("Child receipt refused")
        return dict(PASSED)

    try:
        stdin, stdout, stderr = process.stdin, process.stdout, process.stderr
        if stdin is None or stdout is None or stderr is None:
            raise ValueError("Child pipe acquisition refused")
        errors = asyncio.create_task(stderr.read(4097))
        return await asyncio.wait_for(exchange(stdin, stdout, errors), timeout)
    except (ValueError, OSError, UnicodeError, EOFError, TimeoutError):
        raise ValueError("BFF qualification refused") from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        if errors is not None:
            if not errors.done():
                errors.cancel()
            await asyncio.gather(errors, return_exceptions=True)
        if process.stdin is not None:
            process.stdin.close()


def qualify(
    source: Path, issuer: str, context: ssl.SSLContext, ca: str, private_key_pem: str, password: str
) -> dict[str, object]:
    """Run the existing component public API against real fixture-owned Keycloak only."""
    selected = preflight(source)
    pin = read_pin()
    node = shutil.which("node")
    assert node is not None
    environment_commit = git(ROOT, "rev-parse", "HEAD").decode().strip()
    with tempfile.TemporaryDirectory(prefix="perfeng-bff-sdk-") as temporary:
        exported = Path(temporary) / "source"
        digest = export_console(source, exported, pin)
        start = {"phase": "start", "issuer": issuer, "ca": ca, "privateKeyPem": private_key_pem}
        result = asyncio.run(
            consume(
                [node, str(CONSUMER), str(exported)], start, authorizer(issuer, context, password)
            )
        )
    if (
        preflight(source) != selected
        or git(ROOT, "rev-parse", "HEAD").decode().strip() != environment_commit
    ):
        raise ValueError("Console source changed")
    return {
        **result,
        **selected,
        "environmentCommit": environment_commit,
        "consoleExportDigestFormat": "sha256-length-framed-v1",
        "consoleExportSha256": digest,
    }
