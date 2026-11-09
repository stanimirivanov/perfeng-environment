"""Build and restore a pinned Linux materializer execution closure."""

import argparse
import gzip
import hashlib
import importlib.metadata
import io
import platform
import re
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import tomllib
from pathlib import Path

from scripts.contracts_distribution_tools import (
    checked_bytes,
    local_bytes,
    stage_verifier,
    verify_attestation,
)
from scripts.contracts_inspect import json_data

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "stanimirivanov/perfeng-environment"
WORKFLOW = f"{REPOSITORY}/.github/workflows/contracts-tool-release.yml"
PLATFORM = "linux-x86_64"
LIMIT = 192 * 1024 * 1024
EXECUTABLES = {"bin/gh", "runtime/bin/python3.12"}
SAFE_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*(/[A-Za-z0-9_][A-Za-z0-9_.+-]*)*")
MODULES = (
    "contracts_materialize",
    "contracts_inspect",
    "contracts_distribution_tools",
    "kubernetes_benchmark_contract",
    "control_plane_release",
    "workspace",
)
DEPENDENCIES = (
    "attrs",
    "jsonschema",
    "jsonschema-specifications",
    "pyyaml",
    "referencing",
    "rpds-py",
    "typing-extensions",
)


def archive_bytes(files: dict[str, bytes]) -> bytes:
    """Archive exact member bytes with stable ordering and metadata."""
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.mode = 0o755 if name in EXECUTABLES else 0o644
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def build(verifier: Path) -> bytes:
    """Collect the reviewed scripts and the installed, locked execution inputs."""
    lock_file = ROOT / "uv.lock"
    versions = {
        item["name"]: item["version"]
        for item in tomllib.loads(lock_file.read_text(encoding="utf-8"))["package"]
    }
    files = {
        name: (ROOT / name).read_bytes()
        for name in (
            *(f"scripts/{name}.py" for name in MODULES),
            "LICENSE",
            "uv.lock",
            "contracts/distribution-manifest.schema.json",
            "contracts/distribution-tools.lock.json",
            "tests/fixtures/contract-distribution/trusted_root.json",
            "tests/fixtures/contract-distribution/LICENSE",
            "tests/fixtures/contract-distribution/reusable-workflow-artifact",
            "tests/fixtures/contract-distribution/reusable-workflow-attestation.sigstore.json",
        )
    }
    with tempfile.TemporaryDirectory(prefix="perfeng-materializer-build-") as directory:
        gh, _ = stage_verifier(verifier, Path(directory))
        files["bin/gh"] = gh.read_bytes()
    files["runtime/bin/python3.12"] = Path(sys.executable).read_bytes()
    stdlib = Path(sysconfig.get_path("stdlib"))
    for path in stdlib.rglob("*"):
        relative = path.relative_to(stdlib)
        if (
            path.is_file()
            and SAFE_PATH.fullmatch(relative.as_posix())
            and not {"site-packages", "__pycache__"}.intersection(relative.parts)
        ):
            files[f"runtime/lib/python3.12/{relative.as_posix()}"] = path.read_bytes()

    site = Path(sysconfig.get_path("platlib")).resolve()
    for name in DEPENDENCIES:
        installed = importlib.metadata.distribution(name)
        if installed.version != versions[name] or installed.files is None:
            raise ValueError(f"Missing locked distribution inventory: {name}")
        for member in installed.files:
            path = Path(str(installed.locate_file(member))).resolve()
            if path.is_relative_to(site) and path.is_file() and path.suffix != ".pyc":
                relative = path.relative_to(site).as_posix()
                files[f"runtime/lib/python3.12/site-packages/{relative}"] = path.read_bytes()

    files["materialize.py"] = (
        b"import sys\nfrom pathlib import Path\n"
        b"root = Path(__file__).resolve().parent\n"
        b"sys.path.insert(0, str(root))\n"
        b"from scripts.contracts_materialize import main\n"
        b"sys.argv.extend(('--verifier', str(root / 'bin/gh')))\n"
        b"raise SystemExit(main())\n"
    )
    return archive_bytes(files)


def verify_publisher(verifier: Path, content: bytes, bundle: bytes, pins: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="perfeng-materializer-verify-") as directory:
        root = Path(directory)
        gh, trust = stage_verifier(verifier, root)
        artifact, evidence = root / "materializer.tar.gz", root / "attestation.jsonl"
        artifact.write_bytes(content)
        evidence.write_bytes(bundle)
        policy = {
            "repository": REPOSITORY,
            "sourceDigest": pins["sourceDigest"],
            "sourceRef": "refs/heads/main",
            "signerWorkflow": WORKFLOW,
            "signerDigest": pins["signerDigest"],
        }
        if not verify_attestation(gh, trust, artifact, evidence, policy):
            raise ValueError("Materializer publisher attestation failed")


def restore(
    archive_path: Path, bundle: Path, verifier: Path, pins: dict[str, str], target: Path
) -> None:
    """Verify independent pins and unpack only regular files into a fresh directory."""
    fields = {"archiveSha256": 64, "sourceDigest": 40, "signerDigest": 40}
    if (
        not isinstance(pins, dict)
        or pins.keys() != {*fields, "platform"}
        or pins["platform"] != PLATFORM
        or f"{sys.platform}-{platform.machine()}" != PLATFORM
        or any(
            not isinstance(pins[key], str)
            or not re.fullmatch(r"[a-f0-9]+", pins[key])
            or len(pins[key]) != size
            for key, size in fields.items()
        )
    ):
        raise ValueError("Invalid independent materializer pins or platform")
    content = checked_bytes(archive_path, pins["archiveSha256"], limit=LIMIT)
    verify_publisher(verifier, content, local_bytes(bundle, limit=4 * 1024 * 1024), pins)
    unpack(content, target)


def unpack(content: bytes, target: Path) -> None:
    with gzip.GzipFile(fileobj=io.BytesIO(content)) as compressed:
        expanded = compressed.read(LIMIT + 1)
    if len(expanded) > LIMIT:
        raise ValueError("Materializer archive exceeds expanded size limit")
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        names: set[str] = set()
        for member in archive:
            if (
                member.type != tarfile.REGTYPE
                or member.size < 0
                or member.name in names
                or not SAFE_PATH.fullmatch(member.name)
                or len(names) >= 10000
            ):
                raise ValueError("Invalid materializer archive member")
            names.add(member.name)
        if any(parent.as_posix() in names for name in names for parent in Path(name).parents):
            raise ValueError("Conflicting materializer archive member paths")
        trailer = expanded[archive.offset :]
        if len(trailer) < 1024 or len(trailer) % 512 or any(trailer):
            raise ValueError("Invalid materializer archive trailer")
        target.mkdir(mode=0o700)
        for member in archive.getmembers():
            path = target / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Missing materializer member bytes")
            with stream, path.open("xb") as output:
                output.write(stream.read())
            if member.name in EXECUTABLES:
                path.chmod(0o700)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action, names in (
        ("build", ("verifier", "output")),
        ("restore", ("archive", "bundle", "verifier", "pins", "target")),
    ):
        command = actions.add_parser(action)
        for name in names:
            command.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "build":
            data = build(args.verifier)
            with tempfile.TemporaryDirectory(prefix="perfeng-materializer-check-") as directory:
                restored = Path(directory) / "tool"
                unpack(data, restored)
                subprocess.run(
                    [
                        str(restored / "runtime/bin/python3.12"),
                        "-IB",
                        str(restored / "materialize.py"),
                        "--help",
                    ],
                    cwd=directory,
                    env={},
                    check=True,
                    timeout=30,
                    stdout=subprocess.DEVNULL,
                )
                subprocess.run(
                    [
                        str(restored / "runtime/bin/python3.12"),
                        "-IB",
                        "-c",
                        "import sys; from pathlib import Path; "
                        "sys.path.insert(0, sys.argv[1]); "
                        "from scripts.contracts_distribution_tools import qualify; "
                        "qualify(Path(sys.argv[1]) / 'bin/gh')",
                        str(restored),
                    ],
                    cwd=directory,
                    env={},
                    check=True,
                    timeout=90,
                )
            with args.output.open("xb") as stream:
                stream.write(data)
            print(hashlib.sha256(data).hexdigest())
        else:
            restore(
                args.archive,
                args.bundle,
                args.verifier,
                json_data(local_bytes(args.pins, limit=16384)),
                args.target,
            )
    except (OSError, ValueError, EOFError, tarfile.TarError, subprocess.SubprocessError) as exc:
        print(f"contracts-tool-release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
