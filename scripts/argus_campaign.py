"""Aggregate explicitly inventoried hosted shadow artifacts without network access."""

import argparse
import hashlib
import json
import re
from pathlib import Path

LIMIT = 1 << 20
SHA = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"[a-f0-9]{64}")
PERF_RUN = re.compile(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")
REPOSITORY = {
    "provider": "github",
    "host": "github.com",
    "providerRepositoryId": "1354503944",
    "owner": "stanimirivanov",
    "name": "perfeng-control-plane",
}
KINDS = {
    "functional-api": "stanimirivanov/perfeng-control-plane",
    "performance-integration": "stanimirivanov/perfeng-environment",
}
KEYS = {"baseline-lifecycle", "integration-admission", "run-authentication", "run-lifecycle"}
OUTCOMES = {"passed", "failed", "error", "skipped"}
FAILURES = {"failed", "error"}


def require(condition):
    if not condition:
        raise ValueError("invalid campaign evidence")


def integer(value):
    return type(value) is int and 0 <= value <= 10**15


def read(path):
    require(not path.is_symlink())
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    require(len(raw) <= LIMIT)
    return json.loads(raw)


def revision(digest):
    return {"algorithm": "git-sha1", "digest": digest}


def results(attempt, expected, stage, observation, reference):
    head = observation["testRevision"]
    require(
        attempt["apiVersion"] == "argus.dev/execution-attempt/v1"
        and attempt["stage"] == stage
        and attempt["attemptId"] == observation["deliveryId"] + "-" + stage
        and attempt["manifest"] == reference
        and attempt["testRepository"] == REPOSITORY
        and attempt["testRevision"] == revision(head)
        and attempt["adapter"] == {"id": "generic-http", "version": "1.0.0"}
    )
    rows = attempt["results"]
    require(len(rows) == len(expected) and {r["testKey"] for r in rows} == expected)
    require(
        all(
            r["suiteKey"] == "control-plane-api"
            and r["outcome"] in OUTCOMES
            and integer(r["durationMs"])
            for r in rows
        )
    )
    outcomes = {row["outcome"] for row in rows}
    if "error" in outcomes:
        aggregate = "error"
    elif "failed" in outcomes:
        aggregate = "failed"
    elif "skipped" in outcomes:
        aggregate = "incomplete"
    else:
        aggregate = "passed"
    require(attempt["outcome"] == aggregate)
    return {r["testKey"]: r for r in rows}


def functional(directory, entry):
    observation = read(directory / "observation.json")
    delivery = f"github-{entry['runId']}-{entry['runAttempt']}"
    require(
        observation["schemaVersion"] == 1
        and observation["testRevision"] == entry["headSha"]
        and SHA.fullmatch(observation["baseRevision"])
        and observation["deliveryId"] == delivery
        and integer(observation["pullRequest"])
        and observation["pullRequest"] > 0
    )
    if (
        observation["selected"]["status"] not in {"observed", "empty"}
        or observation["full"]["status"] != "observed"
    ):
        require(observation["selected"]["status"] in {"observed", "empty", "missing", "invalid"})
        require(observation["full"]["status"] in {"observed", "missing", "fallback"})
        return {"status": "unpaired"}
    manifest, plan = read(directory / "manifest.json"), read(directory / "plan.json")
    change, catalog = manifest["change"], manifest["catalog"]
    require(
        manifest["apiVersion"] == "argus.dev/execution-manifest/v1"
        and manifest["family"] == "functional-api"
        and manifest["mode"] in {"targeted", "fallback"}
        and change["sourceRepository"] == catalog["sourceRepository"] == REPOSITORY
        and change["headRevision"] == revision(entry["headSha"])
        and change["baseRevision"] == catalog["revision"] == revision(observation["baseRevision"])
        and change["pullRequest"] == {"number": observation["pullRequest"]}
        and change["trigger"]["deliveryId"] == delivery
    )
    # The retained producer document preserves Argus v1 canonical field order.
    canonical = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    reference = {"apiVersion": manifest["apiVersion"], "sha256": digest}
    require(
        plan["apiVersion"] == "argus.dev/functional-api-execution-plan/v1"
        and plan["manifest"] == reference
        and observation["manifestSha256"] == digest
    )
    decisions = manifest["decisions"]
    require(len(decisions) == len(KEYS) and {d["test"]["key"] for d in decisions} == KEYS)
    require(
        all(
            d["testRepository"] == REPOSITORY
            and d["suite"]
            == {"key": "control-plane-api", "family": "functional-api", "adapter": "generic-http"}
            and d["outcome"] in {"RUN_REQUIRED", "SKIP_FOR_NOW"}
            for d in decisions
        )
    )
    selected_keys = {d["test"]["key"] for d in decisions if d["outcome"] == "RUN_REQUIRED"}
    stages = {"full-suite": KEYS}
    if selected_keys:
        stages["selected"] = selected_keys
    require(len(plan["jobs"]) == len(stages))
    require({job["stage"] for job in plan["jobs"]} == set(stages))
    for job in plan["jobs"]:
        require(
            job["groupKey"] == "control-plane-api"
            and job["adapter"] == "generic-http"
            and job["testRepository"] == REPOSITORY
            and job["testRevision"] == revision(entry["headSha"])
            and job["testCount"] == len(stages[job["stage"]])
        )
    selected = {}
    require(observation["selected"]["status"] == ("observed" if selected_keys else "empty"))
    if selected_keys:
        selected = results(
            read(directory / "selected.json"), selected_keys, "selected", observation, reference
        )
    full = results(read(directory / "full-suite.json"), KEYS, "full-suite", observation, reference)
    elapsed = {}
    for label, rows in (("selected", selected), ("full", full)):
        if rows:
            state = observation[label]
            require(integer(state["elapsedMs"]) and type(state["exitCode"]) is int)
            require(
                state["exitCode"]
                == (1 if any(r["outcome"] != "passed" for r in rows.values()) else 0)
            )
            elapsed[label] = state["elapsedMs"]
        else:
            elapsed[label] = 0
    missed = []
    failing = {key for key, row in full.items() if row["outcome"] in FAILURES}
    for key in sorted(failing):
        early = selected.get(key)
        if early is None or early["outcome"] not in FAILURES:
            missed.append(
                {"testKey": key, "reason": "not-selected" if early is None else "not-reproduced"}
            )
    caught = len(failing) - len(missed)
    duration = {
        label: sum(row["durationMs"] for row in rows.values())
        for label, rows in (("selected", selected), ("full", full))
    }
    return {
        "status": "paired",
        "pullRequest": observation["pullRequest"],
        "manifestSha256": digest,
        "baseRevision": observation["baseRevision"],
        "conservativeSelection": manifest["mode"] == "fallback",
        "selectedTestCount": len(selected),
        "selectedSkippedTestCount": sum(r["outcome"] == "skipped" for r in selected.values()),
        "fullSkippedTestCount": sum(r["outcome"] == "skipped" for r in full.values()),
        "fullTestCount": len(full),
        "selectedDurationMs": duration["selected"],
        "fullDurationMs": duration["full"],
        "durationReductionMs": duration["full"] - duration["selected"],
        "runnerElapsedReductionMs": elapsed["full"] - elapsed["selected"],
        "selectedFailureCount": sum(r["outcome"] in FAILURES for r in selected.values()),
        "fullFailureCount": len(failing),
        "caughtFailureCount": caught,
        "failureRecallBasisPoints": caught * 10000 // len(failing) if failing else None,
        "missedFailures": missed,
    }


def integration(directory):
    receipt = read(directory / "argus-shadow-receipt.json")
    require(receipt["schemaVersion"] == 1)
    status = receipt["status"]
    require(status in {"passed", "failed", "not-run", "invalid-evidence"})
    if status == "passed":
        require(
            PERF_RUN.fullmatch(receipt["referenceRunId"])
            and PERF_RUN.fullmatch(receipt["reviewedRunId"])
            and receipt["referenceRunId"] != receipt["reviewedRunId"]
            and DIGEST.fullmatch(receipt["reportSha256"])
            and DIGEST.fullmatch(receipt["projectionSha256"])
            and type(receipt["publicationLate"]) is bool
            and type(receipt["retainedAnalysisEvents"]) is int
            and receipt["retainedAnalysisEvents"] == 1
        )
    return {"status": status}


def aggregate(path):
    inventory = read(path)
    require(inventory["schemaVersion"] == 1)
    entries = inventory["observations"]
    require(isinstance(entries, list) and 0 < len(entries) <= 1000)
    seen, observations = set(), []
    root = path.absolute().parent
    for entry in entries:
        kind = entry["kind"]
        require(kind in KINDS and entry["repository"] == KINDS[kind])
        require(integer(entry["runId"]) and entry["runId"] > 0)
        require(integer(entry["runAttempt"]) and entry["runAttempt"] > 0)
        require(isinstance(entry["headSha"], str) and SHA.fullmatch(entry["headSha"]))
        identity = entry["repository"], entry["runId"], entry["runAttempt"]
        require(identity not in seen)
        seen.add(identity)
        relative = Path(entry["artifact"])
        require(not relative.is_absolute() and relative.parts and ".." not in relative.parts)
        directory = root / relative
        require(all(not parent.is_symlink() for parent in [directory, *directory.parents]))
        provenance = {
            key: entry[key] for key in ("repository", "runId", "runAttempt", "headSha", "kind")
        }
        provenance["url"] = (
            f"https://github.com/{entry['repository']}/actions/runs/{entry['runId']}"
        )
        try:
            outcome = (
                functional(directory, entry) if kind == "functional-api" else integration(directory)
            )
        except FileNotFoundError:
            outcome = {"status": "missing"}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            outcome = {"status": "invalid"}
        observations.append(provenance | outcome)
    observations.sort(key=lambda row: (row["repository"], row["runId"], row["runAttempt"]))
    paired = [row for row in observations if row["status"] == "paired"]
    failures = sum(row["fullFailureCount"] for row in paired)
    caught = sum(row["caughtFailureCount"] for row in paired)
    totals = {
        "pairedCount": len(paired),
        "incompletePairCount": sum(
            bool(row["selectedSkippedTestCount"] or row["fullSkippedTestCount"]) for row in paired
        ),
        "fullFailureCount": failures,
        "caughtFailureCount": caught,
        "failureRecallBasisPoints": caught * 10000 // failures if failures else None,
        "durationReductionMs": sum(row["durationReductionMs"] for row in paired),
        "runnerElapsedReductionMs": sum(row["runnerElapsedReductionMs"] for row in paired),
        "conservativeSelectionCount": sum(row["conservativeSelection"] for row in paired),
    }
    for metric in (
        "selectedTestCount",
        "fullTestCount",
        "selectedDurationMs",
        "fullDurationMs",
        "selectedFailureCount",
    ):
        totals[metric] = sum(row[metric] for row in paired)
    for status in ("missing", "invalid", "unpaired"):
        totals[status + "Count"] = sum(
            row["kind"] == "functional-api" and row["status"] == status for row in observations
        )
    receipts = [row for row in observations if row["kind"] == "performance-integration"]
    return {
        "schemaVersion": 1,
        "observationCount": len(observations),
        "functional": totals,
        "integration": {
            status + "Count": sum(row["status"] == status for row in receipts)
            for status in ("passed", "failed", "not-run", "invalid-evidence", "missing", "invalid")
        },
        "observations": observations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        report = aggregate(args.inventory)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps(report, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise SystemExit("Campaign inventory or output is invalid.") from None


if __name__ == "__main__":
    main()
