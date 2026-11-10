"""Prepare the benchmark schema from the reviewed signed distribution offline."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.contracts_distribution_tools import checked_bytes
from scripts.contracts_inspect import METADATA_LIMIT, json_data
from scripts.contracts_restore import HISTORY_LOCK, materialize_selection
from scripts.contracts_shadow import SCHEMA
from scripts.contracts_tool_release import restore
from scripts.kubernetes_benchmark_contract import DIGEST, REVISION, ROOT, SOURCE, validate_lock


def project(assets: Path, verifier: Path) -> bytes:
    """Restore only the reviewed current distribution and read its selected schema."""
    lock = json_data(HISTORY_LOCK.read_bytes())
    selection = lock["selection"]
    expected = {"destination": SCHEMA.as_posix(), "sha256": DIGEST}
    if selection["sourceRevision"] != REVISION or selection["members"].get(SOURCE) != expected:
        raise ValueError("Distribution selection differs from the benchmark schema pin")
    assets = assets.absolute()
    with tempfile.TemporaryDirectory(prefix="perfeng-contracts-update-") as directory:
        scratch = Path(directory)
        tool = scratch / "tool"
        restore(
            assets / "tool/materializer.tar.gz",
            assets / "tool/materializer.sigstore.jsonl",
            verifier.absolute(),
            lock["updateTool"],
            tool,
        )
        projection, receipt = materialize_selection(
            tool,
            scratch,
            assets / f"history/{REVISION}.manifest.json",
            assets / f"history/{REVISION}.tar.gz",
            lock["history"],
            selection,
        )
        if receipt["release"] != lock["historyRelease"]:
            raise ValueError("Distribution release differs from the reviewed history release")
        return checked_bytes(projection / SCHEMA, DIGEST, limit=METADATA_LIMIT)


def update(assets: Path, verifier: Path, *, execute: bool, root: Path = ROOT) -> dict[str, str]:
    """Preview or atomically apply the authenticated snapshot to the working tree."""
    validate_lock(root)
    content = project(assets, verifier)
    if hashlib.sha256(content).hexdigest() != DIGEST:
        raise ValueError("Projected benchmark schema checksum differs from the contract lock")
    target = root / SCHEMA
    if target.parent.is_symlink() or target.is_symlink():
        raise ValueError("Benchmark schema destination must not use symbolic links")
    status = "unchanged"
    if not target.exists() or target.read_bytes() != content:
        status = "preview"
        if execute:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                try:
                    output.write(content)
                    output.flush()
                    os.fchmod(output.fileno(), 0o644)
                    os.fsync(output.fileno())
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
            status = "applied"
    return {
        "status": status,
        "path": SCHEMA.as_posix(),
        "sourceRevision": REVISION,
        "sha256": DIGEST,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--verifier", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(update(args.assets, args.verifier, execute=args.execute), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"contracts-update: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
