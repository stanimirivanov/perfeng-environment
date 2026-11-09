"""Rehearse historical restoration from local signed release assets."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.contracts_distribution_tools import LOCK as TRUST_LOCK
from scripts.contracts_distribution_tools import checked_bytes, local_bytes
from scripts.contracts_inspect import METADATA_LIMIT, json_data
from scripts.contracts_materialize import verify_generation
from scripts.contracts_shadow import EXAMPLE, ROOT, SCHEMA, verify_parity
from scripts.contracts_tool_release import restore

HISTORY_LOCK = ROOT / "contracts/distribution-restore.lock.json"
SHADOW_LOCK = ROOT / "contracts/distribution.lock.json"
TOOL_ENTRY = Path("materialize.py")


def verify_projection(store: Path, members: dict[str, dict[str, str]]) -> str:
    """Check restored bytes against reviewed destination hashes."""
    pointer = local_bytes(store / "current", limit=65).decode("ascii")
    if not re.fullmatch(r"[a-f0-9]{64}\n", pointer):
        raise ValueError("Invalid restored projection pointer")
    generation = pointer[:-1]
    expected = {
        mapping["destination"]: checked_bytes(
            store / generation / mapping["destination"],
            mapping["sha256"],
            limit=METADATA_LIMIT,
        )
        for mapping in members.values()
    }
    verify_generation(store / generation, expected)
    return generation


def restore_selection(
    tool: Path,
    scratch: Path,
    manifest: Path,
    archive: Path,
    policy: dict[str, str],
    selection: dict,
) -> dict[str, str]:
    """Recreate one selected projection in a fresh store using the released tool."""
    store = scratch / "store"
    store.mkdir(parents=True, mode=0o700)
    policy_path, selection_path = scratch / "policy.json", scratch / "selection.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    subprocess.run(
        [
            str(tool / "runtime/bin/python3.12"),
            "-IB",
            str(tool / TOOL_ENTRY),
            "--manifest",
            str(manifest),
            "--archive",
            str(archive),
            "--bundle",
            str(manifest.parent / "manifest.sigstore.jsonl"),
            "--policy",
            str(policy_path),
            "--selection",
            str(selection_path),
            "--target",
            str(store),
            "--execute",
        ],
        cwd=scratch,
        env={},
        check=True,
        timeout=120,
        stdout=subprocess.DEVNULL,
    )
    generation = verify_projection(store, selection["members"])
    projection = store / generation
    verify_parity((projection / SCHEMA).read_bytes(), (projection / EXAMPLE).read_bytes())
    descriptor = json_data(checked_bytes(manifest, policy["manifestSha256"], limit=METADATA_LIMIT))
    return {
        "sourceRevision": selection["sourceRevision"],
        "release": descriptor["releaseId"],
        "manifestSha256": policy["manifestSha256"],
        "archiveSha256": descriptor["archiveSha256"],
        "projectionSha256": generation,
    }


def restore_retained(retained: Path, verifier: Path) -> dict:
    """Verify retained releases and return a receipt only after both restores succeed."""
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, timeout=10):
        raise ValueError("Cold restoration receipts require a clean consumer checkout")
    consumer_revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10
    ).strip()
    history = json_data(HISTORY_LOCK.read_bytes())
    previous = json_data(SHADOW_LOCK.read_bytes())
    trust = json_data(TRUST_LOCK.read_bytes())
    retained = retained.absolute()
    revision = history["selection"]["sourceRevision"]
    with tempfile.TemporaryDirectory(prefix="perfeng-restore-") as directory:
        scratch = Path(directory)
        tool = scratch / "tool"
        restore(
            retained / "tool/materializer.tar.gz",
            retained / "tool/materializer.sigstore.jsonl",
            verifier.absolute(),
            previous["tool"],
            tool,
        )
        current = restore_selection(
            tool,
            scratch / "current",
            retained / f"history/{revision}.manifest.json",
            retained / f"history/{revision}.tar.gz",
            history["history"],
            history["selection"],
        )
        rollback = restore_selection(
            tool,
            scratch / "rollback",
            retained / "tracer/manifest.json",
            retained / "tracer/tracer.tar.gz",
            previous["tracer"],
            previous["selection"],
        )
        if (
            current["release"] != history["historyRelease"]
            or rollback["release"] != previous["tracerRelease"]
        ):
            raise ValueError("Restored release ID differs from the reviewed selection")

    return {
        "schemaVersion": 1,
        "status": "passed",
        "purpose": "historical-reproduction",
        "freshSecurityAdmission": False,
        "consumer": "perfeng-environment",
        "consumerRevision": consumer_revision,
        "command": "make contracts-restore-check",
        "toolRelease": previous["toolRelease"],
        "toolArchiveSha256": previous["tool"]["archiveSha256"],
        "trustRootSha256": trust["trustRootSha256"],
        "verifierSha256": trust["verifierSha256"],
        "rollbackScope": "distribution-path; environment contract revision unchanged",
        "current": current,
        "rollback": rollback,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retained", type=Path, required=True)
    parser.add_argument("--verifier", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(restore_retained(args.retained, args.verifier), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"contracts-restore: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
