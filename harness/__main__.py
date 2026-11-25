"""Run the repository-local engineering harness pilot."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
CHECK_TIMEOUT_SECONDS = 120


class Check(TypedDict):
    id: str
    stage: str
    invocation: list[str]
    guide: str


def main() -> int:
    """Run checks for the requested stage in manifest order."""
    with (ROOT / "harness/checks.toml").open("rb") as manifest:
        checks = cast(list[Check], tomllib.load(manifest).get("checks", []))
    offline_checks = [check for check in checks if check["stage"] == "offline"]
    parser = argparse.ArgumentParser(
        prog="python -m harness",
        description="Run the offline engineering harness from the repository root.",
        epilog="For full verification, run make verify and make validate.",
    )
    parser.add_argument(
        "stage",
        choices=["offline"],
        help="run " + (", ".join(check["id"] for check in offline_checks) or "registered checks"),
    )
    parser.parse_args()
    if not offline_checks:
        parser.error("register an offline check in harness/checks.toml")

    for check in offline_checks:
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
