"""Emit a bounded advisory receipt without uploading private fixture files."""

import argparse
import json
import re
from pathlib import Path

RUN_ID = re.compile(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")
DIGEST = re.compile(r"[a-f0-9]{64}")
LIMIT = 64 << 10


def receipt(outcome, evidence):
    result = {"schemaVersion": 1, "status": "not-run"}
    if outcome != "success":
        return result | {"status": "failed"} if outcome == "failure" else result
    invalid = result | {"status": "invalid-evidence"}
    if not isinstance(evidence, dict) or any(
        evidence.get(key) != "passed" for key in ("productAcceptance", "cleanup", "servingCleanup")
    ):
        return invalid
    reference, reviewed, recovery = (
        evidence.get(key) for key in ("checkoutSmoke", "jointAcceptance", "recovery")
    )
    if not all(isinstance(item, dict) for item in (reference, reviewed, recovery)):
        return invalid
    reference_id, reviewed_id = reference.get("runId"), reviewed.get("runId")
    report, projection = reviewed.get("reportSha256"), reviewed.get("projectionSha256")
    if (
        reference.get("k6ExitCode") != 0
        or type(reference.get("k6ExitCode")) is not int
        or type(recovery.get("retainedAnalysisEvents")) is not int
        or recovery["retainedAnalysisEvents"] != 1
        or type(recovery.get("publicationLate")) is not bool
        or not all(
            isinstance(value, str) and RUN_ID.fullmatch(value)
            for value in (reference_id, reviewed_id)
        )
        or reference_id == reviewed_id
        or not all(
            isinstance(value, str) and DIGEST.fullmatch(value) for value in (report, projection)
        )
    ):
        return invalid
    return result | {
        "status": "passed",
        "referenceRunId": reference_id,
        "reviewedRunId": reviewed_id,
        "reportSha256": report,
        "projectionSha256": projection,
        "publicationLate": recovery["publicationLate"],
        "retainedAnalysisEvents": 1,
    }


def read_evidence(root):
    try:
        [directory] = list(root.glob("argus-perfeng-serving-*"))
        path = directory / "evidence.json"
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) <= LIMIT:
            return json.loads(raw)
    except (OSError, ValueError, RecursionError):
        return None
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outcome")
    parser.add_argument("private_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("summary", type=Path)
    args = parser.parse_args()
    evidence = read_evidence(args.private_root) if args.outcome == "success" else None
    observed = receipt(args.outcome, evidence)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(observed, indent=2) + "\n")
    with args.summary.open("a", encoding="utf-8") as summary:
        summary.write(f"## Argus shadow observation\n\nAdvisory result **{observed['status']}**.\n")
    if observed["status"] == "invalid-evidence":
        raise SystemExit("Shadow fixture success did not produce a verified safe receipt.")


if __name__ == "__main__":
    main()
