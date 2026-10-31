"""Acquire and inspect the explicitly selected serving binary, without PATH or trust writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "stanimirivanov/perfeng-environment"
MAX_BINARY_BYTES = 128 * 1024 * 1024
GO_VERSION = "go1.27.3"


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate lock fields rather than silently choosing an ambiguous identity."""
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise GatewayError(
                "RELEASE_INVALID", "Duplicate release-selection field", "review the serving lock"
            )
        value[key] = item
    return value


def private_state(root: Path) -> None:
    """Require private state before executing downloaded or installed bytes."""
    if __package__:
        from .native_gateway_host import private_permissions
    else:
        from native_gateway_host import private_permissions
    private_permissions(root)


class GatewayError(ValueError):
    """A safe failure classification and fixed operator remedy; never vendor stderr."""

    def __init__(self, code: str, message: str, remedy: str) -> None:
        self.code = code
        self.remedy = remedy
        super().__init__(f"[{code}] {message}. Next: {remedy}")


@dataclass(frozen=True)
class Selection:
    """Reviewed release-asset identity; checksum and source stay in Git, not a local receipt."""

    tag: str
    asset_id: int
    sha256: str
    size: int
    revision: str


def safe_state(root: Path) -> Path:
    """Reject redirected gateway state while normalizing legitimate path aliases."""
    root = root.resolve()
    state = root / ".local/native-gateway"
    for path in (root / ".local", state):
        if path.is_symlink() or path.is_junction() or path.resolve() != path:
            raise GatewayError(
                "STATE_REDIRECTED",
                "Gateway state is redirected",
                "inspect .local; do not delete CA state",
            )
    if state.exists():
        for path in state.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise GatewayError(
                    "STATE_REDIRECTED",
                    "Gateway state contains a redirected path",
                    "inspect .local/native-gateway",
                ) from None
    return state


def selection(root: Path = ROOT) -> Selection:
    """Read the complete reviewed serving lock; unpublished or ambiguous releases fail closed."""
    value = json.loads(
        (root / "native-gateway/serving-release.json").read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
    )
    return parse_selection(value)


def parse_selection(value: object) -> Selection:
    """Validate a release selection before local installation or an explicit Git selection update."""
    if not isinstance(value, dict) or set(value) != {
        "schemaVersion",
        "platform",
        "caddyVersion",
        "goVersion",
        "release",
    }:
        raise GatewayError(
            "RELEASE_INVALID", "Invalid serving lock", "review native-gateway/serving-release.json"
        )
    if type(value["schemaVersion"]) is not int or (
        value["schemaVersion"],
        value["platform"],
        value["caddyVersion"],
    ) != (
        1,
        "linux/amd64",
        "v2.11.7",
    ):
        raise GatewayError(
            "RELEASE_INVALID", "Unsupported serving lock", "review the gateway release selection"
        )
    if value["goVersion"] != GO_VERSION:
        raise GatewayError(
            "RELEASE_TOOLCHAIN",
            f"Serving requires reviewed {GO_VERSION}; the selected asset is not approved",
            "publish and select a patched gateway release; never relabel an existing asset",
        )
    release = value["release"]
    if release is None:
        raise GatewayError(
            "RELEASE_UNSELECTED",
            "No serving binary has been published and selected",
            "run the reviewed gateway publisher, then select its release-selection.json",
        )
    if not isinstance(release, dict) or set(release) != {
        "tag",
        "assetId",
        "sha256",
        "sizeBytes",
        "sourceRevision",
    }:
        raise GatewayError("RELEASE_INVALID", "Invalid release identity", "review the serving lock")
    revision = release["sourceRevision"]
    if type(value["schemaVersion"]) is not int or (
        not isinstance(revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        or release["tag"] != f"native-gateway-{revision}"
        or type(release["assetId"]) is not int
        or release["assetId"] <= 0
        or type(release["sizeBytes"]) is not int
        or not 0 < release["sizeBytes"] <= MAX_BINARY_BYTES
        or not isinstance(release["sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", release["sha256"]) is None
    ):
        raise GatewayError(
            "RELEASE_INVALID", "Invalid release provenance or checksum", "review the serving lock"
        )
    return Selection(
        release["tag"], release["assetId"], release["sha256"], release["sizeBytes"], revision
    )


def executable(root: Path, selected: Selection) -> Path:
    """Locate one content-selected installation without considering ambient PATH."""
    return safe_state(root) / "tools" / selected.sha256 / "caddy"


def digest(path: Path) -> str:
    """Hash an executable without loading its complete bytes into memory."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def metadata(binary: Path, serving: bool = True) -> str:
    """Check bounded real CLI metadata, including dependency identity, before executing configuration."""
    try:
        results = [
            subprocess.run([str(binary), action], capture_output=True, check=True, timeout=10)
            for action in ("version", "build-info")
        ]
    except FileNotFoundError:
        raise GatewayError(
            "TOOL_MISSING", f"Caddy is missing at {binary}", "run task native:setup"
        ) from None
    except subprocess.TimeoutExpired:
        raise GatewayError(
            "TOOL_TIMEOUT",
            "Caddy metadata timed out",
            "inspect the selected executable and host security software",
        ) from None
    except (OSError, subprocess.CalledProcessError):
        raise GatewayError(
            "TOOL_EXECUTION",
            "Caddy metadata execution failed",
            "inspect the selected executable; vendor output was withheld",
        ) from None
    if any(len(result.stdout) > 64 * 1024 for result in results):
        raise GatewayError(
            "TOOL_METADATA", "Caddy metadata exceeds its limit", "review the selected binary"
        )
    version = results[0].stdout.decode("utf-8", errors="replace").split()
    info = results[1].stdout.decode("utf-8", errors="replace")
    if not version or version[0] != "v2.11.7":
        observed = (
            version[0]
            if version and re.fullmatch(r"[a-zA-Z0-9.()_-]{1,40}", version[0])
            else "invalid"
        )
        raise GatewayError(
            "TOOL_VERSION",
            f"Caddy at {binary}: expected v2.11.7; observed {observed}",
            "use task native:setup; manual cmd/caddy builds may report unknown",
        )
    if re.search(
        r"^(?:mod|dep)\s+github.com/caddyserver/caddy/v2\s+v2\.11\.7\s", info, re.MULTILINE
    ) is None or re.search(r"^\s*=>", info, re.MULTILINE):
        raise GatewayError(
            "TOOL_PROVENANCE",
            "Caddy release identity is missing or replaced",
            "review the binary build inputs",
        )
    if serving:
        go = re.search(r"^go\s+go(\d+)\.(\d+)\.(\d+)\s*$", info, re.MULTILINE)
        net = re.search(r"^dep\s+golang.org/x/net\s+v(\d+)\.(\d+)\.(\d+)\s", info, re.MULTILINE)
        if (
            go is None
            or "go" + ".".join(go.groups()) != GO_VERSION
            or net is None
            or tuple(map(int, net.groups())) < (0, 60, 0)
        ):
            raise GatewayError(
                "TOOL_SECURITY",
                f"Caddy requires reviewed {GO_VERSION} and x/net >=v0.60.0",
                "publish a reviewed patched build; do not bypass the gate",
            )
        if "build\tGOOS=linux" not in info or "build\tGOARCH=amd64" not in info:
            raise GatewayError(
                "TOOL_PLATFORM",
                "Expected a Linux AMD64 serving binary",
                "use the selected Linux release",
            )
    return info


def installed(root: Path = ROOT) -> Path:
    """Require the selected binary's exact bytes and verified serving metadata; no receipt is authority."""
    selected = selection(root)
    path = executable(root, selected)
    if not path.is_file():
        raise GatewayError(
            "TOOL_MISSING", f"Selected Caddy is missing at {path}", "run task native:setup"
        )
    if path.stat().st_size != selected.size or digest(path) != selected.sha256:
        raise GatewayError(
            "TOOL_CHECKSUM",
            "Installed Caddy differs from the reviewed checksum",
            "retain it for inspection; setup never overwrites modified installations",
        )
    private_state(root)
    metadata(path)
    return path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Handle private GitHub asset redirects explicitly rather than forwarding authorization."""

    def redirect_request(
        self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def download(selected: Selection, destination: Path) -> None:
    """Stream an exact GitHub asset; drop authorization before its allowlisted storage redirect."""
    token = os.environ.get("PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN")
    if not token:
        raise GatewayError(
            "REPOSITORY_AUTH",
            "Private release access needs a contents-read token",
            "set PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN in this terminal",
        )
    if len(token) > 4096 or any(character in token for character in ("\r", "\n")):
        raise GatewayError(
            "REPOSITORY_AUTH",
            "Contents-read token input is invalid",
            "set a single token value without line breaks; it will not be printed",
        )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{selected.asset_id}",
        headers={
            "Accept": "application/octet-stream",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                raise
            location = error.headers.get("Location", "")
            error.close()
            url = urllib.parse.urlsplit(location)
            if (
                url.scheme != "https"
                or url.hostname != "release-assets.githubusercontent.com"
                or url.port not in (None, 443)
                or url.username
                or url.password
                or url.fragment
            ):
                raise GatewayError(
                    "DOWNLOAD_REDIRECT",
                    "Release asset redirected outside reviewed storage",
                    "review GitHub asset delivery; no credentials were forwarded",
                ) from None
            response = opener.open(urllib.request.Request(location), timeout=30)
        deadline = time.monotonic() + 120
        progress = time.monotonic()
        size = 0
        with response, destination.open("xb") as output:
            while True:
                if time.monotonic() >= deadline:
                    raise GatewayError(
                        "DOWNLOAD_TIMEOUT",
                        "Release download exceeded 120 seconds",
                        "retry task native:setup",
                    )
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > selected.size:
                    raise GatewayError(
                        "DOWNLOAD_SIZE",
                        "Release asset exceeds the selected size",
                        "review the asset selection",
                    )
                output.write(chunk)
                if time.monotonic() - progress >= 10:
                    print(
                        f"Downloading selected gateway: {size}/{selected.size} bytes...", flush=True
                    )
                    progress = time.monotonic()
    except (OSError, urllib.error.URLError):
        raise GatewayError(
            "DOWNLOAD_FAILED",
            "Release asset download failed",
            "check contents-read access and network; no token or vendor URL was printed",
        ) from None


def setup(root: Path = ROOT) -> Path:
    """Install only a new verified content-selected binary; preserve existing builds and CA keys."""
    selected = selection(root)
    target = executable(root, selected)
    if target.exists():
        path = installed(root)
        print(f"PASS selected Caddy already installed: {path}", flush=True)
        return path
    state = safe_state(root)
    if not state.exists():
        from scripts.native_gateway_host import secure_permissions

        secure_permissions(root)
    private_state(root)
    print(f"[1/3] Downloading selected Caddy asset {selected.asset_id}...", flush=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=state) as directory:
        candidate = Path(directory) / "caddy"
        download(selected, candidate)
        if candidate.stat().st_size != selected.size or digest(candidate) != selected.sha256:
            raise GatewayError(
                "DOWNLOAD_CHECKSUM",
                "Downloaded Caddy differs from the reviewed bytes",
                "review the release selection; nothing was installed",
            )
        print("[2/3] Checking executable version and patched build metadata...", flush=True)
        candidate.chmod(0o700)
        metadata(candidate)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Hard-link publication is exclusive: concurrent setup must never replace bytes.
        try:
            os.link(candidate, target)
        except FileExistsError:
            installed(root)
    print(
        f"[3/3] PASS Caddy v2.11.7; {GO_VERSION}; SHA-256 {selected.sha256}\nExecutable: {target}\nSource: {selected.revision}\nNext: task native:doctor",
        flush=True,
    )
    return target
