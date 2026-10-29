"""Reuse the guarded Role fault for the separately approved grant authority."""

import sys
from pathlib import Path
from typing import Any

import schema_migration_denial as denial
import yaml


def source() -> dict[str, Any]:
    return next(
        obj
        for obj in yaml.safe_load_all(
            Path(
                "gitops/release-workflows/rbac/runtime-grants/platform-observation.yaml"
            ).read_text()
        )
        if obj["kind"] == "Role"
    )


def main() -> None:
    mode, directory = sys.argv[1:]
    denial.execute(mode, Path(directory), "grant-denied", source())


if __name__ == "__main__":
    main()
