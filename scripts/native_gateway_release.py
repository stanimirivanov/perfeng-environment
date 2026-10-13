"""Build/audit the serving package or explicitly select its reviewed publication record."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

from scripts import native_gateway_tool as tool


def build(output: Path, revision: str, root: Path = tool.ROOT) -> dict[str, object]:
    """Create a new package from locked inputs; failed builds/audits never become serving selections."""
    if os.name != "nt" or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise tool.GatewayError(
            "BUILD_INPUT",
            "Serving builds require Windows and a full source revision",
            "use the Windows gateway workflow",
        )
    output = output.absolute()
    if output.resolve() != output or output.exists():
        raise tool.GatewayError(
            "BUILD_OUTPUT",
            "Build output must be a new ordinary directory",
            "choose a fresh output directory; retain previous evidence",
        )
    output.mkdir(parents=True)
    binary = output / "caddy.exe"
    source = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    if source != revision:
        raise tool.GatewayError(
            "BUILD_PROVENANCE",
            "Source revision differs from checkout HEAD",
            "build the reviewed checkout; no package was approved",
        )
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    )
    environment = {
        **os.environ,
        "GOTOOLCHAIN": "go1.26.9",
        "GOFLAGS": "-mod=readonly",
        "CGO_ENABLED": "0",
        "GOVULNDB": "https://vuln.go.dev",
    }
    commands = [
        (
            "Build locked standard Caddy",
            [
                "go",
                "-C",
                str(root / "native-gateway/build"),
                "build",
                "-trimpath",
                "-buildvcs=false",
                "-o",
                str(binary),
                ".",
            ],
        ),
        (
            "Audit the actual executable",
            [
                "go",
                "-C",
                str(root / "native-gateway/audit"),
                "tool",
                "govulncheck",
                "-mode=binary",
                "-show=verbose",
                str(binary),
            ],
        ),
        (
            "Adapt actual Caddyfile offline",
            [
                str(binary),
                "adapt",
                "--config",
                str(root / "native-gateway/Caddyfile"),
                "--adapter",
                "caddyfile",
            ],
        ),
    ]
    for stage, command in commands:
        print(f"{stage}...", flush=True)
        try:
            subprocess.run(
                command,
                cwd=root,
                env=environment,
                check=True,
                timeout=900,
                stdout=subprocess.DEVNULL if command[0] == str(binary) else None,
            )
        except (OSError, subprocess.SubprocessError):
            raise tool.GatewayError(
                "BUILD_CHECK",
                f"{stage} failed; no package was approved",
                "inspect the build/audit output and retained staging directory",
            ) from None
    tool.metadata(binary)
    record: dict[str, object] = {
        "schemaVersion": 1,
        "platform": "windows/amd64",
        "caddyVersion": "v2.11.7",
        "goVersion": "go1.26.9",
        "sourceRevision": revision,
        "development": dirty,
        "sha256": tool.digest(binary),
        "sizeBytes": binary.stat().st_size,
        "audit": {
            "scanner": "govulncheck/v1.7.0",
            "level": "symbol",
            "status": "passed",
            "scannedAt": dt.datetime.now(dt.UTC).isoformat(),
        },
    }
    (output / "build-record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2), flush=True)
    return record


def select(path: Path, root: Path = tool.ROOT, execute: bool = False) -> None:
    """Preview or write a reviewed publisher selection; never download, publish or modify local CA."""
    if path.stat().st_size > 16 * 1024:
        raise tool.GatewayError(
            "RELEASE_INVALID",
            "Selection document exceeds its limit",
            "use the publisher's release-selection.json",
        )
    document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=tool.unique_object)
    # Validate in memory using the same reader as setup without relaxing its contract.
    tool.parse_selection(document)
    print(json.dumps(document, indent=2), flush=True)
    if execute:
        (root / "native-gateway/serving-release.json").write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )
        print(
            "Serving selection updated. Review the Git diff and commit through the owner workflow. Local files, CA and cluster unchanged.",
            flush=True,
        )
    else:
        print(
            "Preview only; add --execute after reviewing the source, asset and checksum.",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    """Expose explicit build and selection boundaries separately from ordinary validation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "select"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "select":
            if args.file is None:
                raise ValueError("select requires --file")
            select(args.file, execute=args.execute)
        elif args.execute:
            if args.output is None or args.revision is None:
                raise ValueError("build requires --output and --revision")
            build(args.output, args.revision)
        else:
            print(
                "Preview only: build uses locked Go/Caddy inputs and network advisory data; nothing was built or published."
            )
    except (OSError, ValueError) as error:
        print(
            str(error)
            if isinstance(error, tool.GatewayError)
            else "Gateway release input failed; inspect the provided file/options. No vendor output printed."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
