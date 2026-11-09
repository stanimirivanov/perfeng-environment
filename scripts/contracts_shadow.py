"""Exercise the exact published tracer against the existing offline consumer."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.contracts_distribution_tools import local_bytes
from scripts.contracts_inspect import json_data
from scripts.contracts_tool_release import restore
from scripts.kubernetes_benchmark_contract import ROOT, document, validate

LOCK = Path("contracts/kubernetes-benchmark-lock.json")
SCHEMA = Path("contracts/kubernetes-benchmark-evidence.schema.json")
EXAMPLE = Path("examples/kubernetes-benchmark-evidence/accepted-preflight.json")


def verify_parity(projected_schema: bytes, fixture: bytes) -> None:
    """Require the consumer's exact bytes and expected valid and invalid outcomes."""
    if projected_schema != (ROOT / SCHEMA).read_bytes():
        raise ValueError("Projected schema differs from the authoritative snapshot")
    valid = document(fixture)
    invalid = {**valid, "scope": None}
    with tempfile.TemporaryDirectory(prefix="perfeng-tracer-validation-") as directory:
        projected = Path(directory)
        (projected / LOCK.parent).mkdir()
        (projected / LOCK).write_bytes((ROOT / LOCK).read_bytes())
        (projected / SCHEMA).write_bytes(projected_schema)
        for root in (ROOT, projected):
            validate(valid, root)
            try:
                validate(invalid, root)
            except ValueError:
                continue
            raise ValueError("Kubernetes evidence validator accepted invalid tracer evidence")


def shadow(assets: Path, verifier: Path) -> dict[str, str]:
    """Restore the released tool and compare a temporary projection without network fetches."""
    lock = json_data((ROOT / "contracts/distribution.lock.json").read_bytes())
    assets = assets.absolute()
    with tempfile.TemporaryDirectory(prefix="perfeng-tracer-shadow-") as directory:
        scratch = Path(directory)
        tool, store = scratch / "tool", scratch / "store"
        restore(
            assets / "tool/materializer.tar.gz",
            assets / "tool/materializer.sigstore.jsonl",
            verifier.absolute(),
            lock["tool"],
            tool,
        )
        policy, selection = scratch / "policy.json", scratch / "selection.json"
        policy.write_text(json.dumps(lock["tracer"]), encoding="utf-8")
        selection.write_text(json.dumps(lock["selection"]), encoding="utf-8")
        store.mkdir(mode=0o700)
        subprocess.run(
            [
                str(tool / "runtime/bin/python3.12"),
                "-IB",
                str(tool / "materialize.py"),
                "--manifest",
                str(assets / "tracer/manifest.json"),
                "--archive",
                str(assets / "tracer/tracer.tar.gz"),
                "--bundle",
                str(assets / "tracer/manifest.sigstore.jsonl"),
                "--policy",
                str(policy),
                "--selection",
                str(selection),
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
        pointer = local_bytes(store / "current", limit=65).decode("ascii")
        if not re.fullmatch(r"[a-f0-9]{64}\n", pointer):
            raise ValueError("Invalid tracer projection pointer")
        projection = store / pointer[:-1]
        verify_parity((projection / SCHEMA).read_bytes(), (projection / EXAMPLE).read_bytes())
    return {
        "status": "passed",
        "toolRelease": lock["toolRelease"],
        "tracerRelease": lock["tracerRelease"],
        "sourceRevision": lock["selection"]["sourceRevision"],
        "toolArchiveSha256": lock["tool"]["archiveSha256"],
        "tracerManifestSha256": lock["tracer"]["manifestSha256"],
        "schemaParity": "exact",
        "fixtureParity": "valid accepted and invalid rejected",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--verifier", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(shadow(args.assets, args.verifier), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"contracts-shadow: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
