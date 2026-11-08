"""Qualify a locally prepared verifier against independent bootstrap pins."""

import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "contracts/distribution-tools.lock.json"
FIXTURE = ROOT / "tests/fixtures/contract-distribution"
TOOL_LIMIT = 128 * 1024 * 1024
ROOT_LIMIT = 1024 * 1024


def checked_bytes(path: Path, digest: str, *, limit: int) -> bytes:
    """Read bounded regular-file bytes and require the independently pinned digest."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
                raise ValueError(f"Invalid file type or size: {path.name}")
            data = source.read(limit + 1)
    except OSError as exc:
        raise ValueError(f"Local input unavailable: {path.name}") from exc
    if not 0 < len(data) <= limit:
        raise ValueError(f"Invalid file size: {path.name}")
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"SHA-256 mismatch: {path.name}")
    return data


def verify_fixture(
    executable: Path, trust_root: Path, directory: Path, policy: dict[str, str]
) -> bool:
    """Check the fixture with pinned certificate identity and local trust material."""
    command = [
        str(executable),
        "attestation",
        "verify",
        str(FIXTURE / "reusable-workflow-artifact"),
        "--bundle",
        str(FIXTURE / "reusable-workflow-attestation.sigstore.json"),
        "--custom-trusted-root",
        str(trust_root),
        "--repo",
        policy["repository"],
        "--source-digest",
        policy["sourceDigest"],
        "--source-ref",
        policy["sourceRef"],
        "--signer-workflow",
        policy["signerWorkflow"],
        "--signer-digest",
        policy["signerDigest"],
        "--deny-self-hosted-runners",
    ]
    result = subprocess.run(
        command,
        env={"HOME": str(directory), "GH_CONFIG_DIR": str(directory), "GH_PROMPT_DISABLED": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    return result.returncode == 0


def qualify(verifier: Path) -> None:
    """Verify positive and negative outcomes using the same checked executable and root."""
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if f"{sys.platform}-{platform.machine()}" != lock["platform"]:
        raise ValueError("Unqualified verifier platform")
    if platform.python_version() != lock["pythonVersion"]:
        raise ValueError("Unqualified Python version")
    checked_bytes(Path(sys.executable), lock["pythonSha256"], limit=TOOL_LIMIT)
    executable_bytes = checked_bytes(verifier, lock["verifierSha256"], limit=TOOL_LIMIT)
    root_bytes = checked_bytes(
        FIXTURE / "trusted_root.json", lock["trustRootSha256"], limit=ROOT_LIMIT
    )
    with tempfile.TemporaryDirectory(prefix="perfeng-contract-verifier-") as tmp:
        directory = Path(tmp)
        executable, trust_root = directory / "gh", directory / "trusted_root.json"
        executable.write_bytes(executable_bytes)
        executable.chmod(0o700)
        trust_root.write_bytes(root_bytes)
        policy: dict[str, str] = lock["qualification"]
        if not verify_fixture(executable, trust_root, directory, policy):
            raise ValueError("Pinned offline attestation failed verification")
        for field, wrong in (
            ("repository", "malancas/other"),
            ("sourceDigest", "0" * 40),
            (
                "signerWorkflow",
                "github/artifact-attestations-workflows/.github/workflows/other.yml",
            ),
        ):
            if verify_fixture(executable, trust_root, directory, {**policy, field: wrong}):
                raise ValueError(f"Verifier accepted incorrect {field}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verifier", required=True, type=Path)
    try:
        qualify(parser.parse_args().verifier)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"contracts-tools-check: {exc}", file=sys.stderr)
        return 1
    print("contracts-tools-check: offline attestation and identity rejection cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
