"""Run the repository-local engineering harness pilot."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
CHECK_MANIFEST = "harness/checks.toml"
CHECK_TIMEOUT_SECONDS = 120


class Check(TypedDict):
    id: str
    stage: str
    invocation: list[str]
    guide: str


def load_checks() -> list[Check]:
    """Read and validate every check before allowing any dispatch."""
    try:
        with (ROOT / CHECK_MANIFEST).open("rb") as manifest:
            checks = tomllib.load(manifest).get("checks")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot load TOML ({error})") from error

    if not isinstance(checks, list) or not checks:
        raise ValueError("register at least one check using a [[checks]] table")

    for index, check in enumerate(checks, start=1):
        entry = f"checks[{index}]"
        if not isinstance(check, dict):
            raise ValueError(f"{entry} must be a [[checks]] table")
        if check.get("stage") != "offline":
            raise ValueError(f"{entry}.stage must be 'offline'")
        for field in ("id", "guide"):
            value = check.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{entry}.{field} must be a nonblank string")
        invocation = check.get("invocation")
        if (
            not isinstance(invocation, list)
            or not invocation
            or any(not isinstance(arg, str) or "\0" in arg for arg in invocation)
            or not invocation[0].strip()
        ):
            raise ValueError(
                f"{entry}.invocation must be a list of strings with a nonblank first argument "
                "and no NUL characters"
            )
    return cast(list[Check], checks)


def main() -> int:
    """Run checks for the requested stage in manifest order."""
    parser = argparse.ArgumentParser(
        prog="python -m harness",
        description="Run the offline engineering harness from the repository root.",
        epilog="For full verification, run make verify and make validate.",
    )
    try:
        checks = load_checks()
    except ValueError as error:
        parser.error(f"{CHECK_MANIFEST}: {error}")
    parser.add_argument(
        "stage",
        choices=["offline"],
        help="run " + ", ".join(check["id"] for check in checks),
    )
    parser.parse_args()

    for check in checks:
        try:
            result = subprocess.run(
                [sys.executable, *check["invocation"]],
                cwd=ROOT,
                check=False,
                timeout=CHECK_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            print(
                f"{check['id']}: exceeded {CHECK_TIMEOUT_SECONDS}s; see {check['guide']}",
                file=sys.stderr,
            )
            return 124
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
