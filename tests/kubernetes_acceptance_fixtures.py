"""Synthetic acceptance fixtures; they never establish real cluster qualification."""

import copy
import hashlib
import json
from pathlib import Path
from uuid import UUID

from scripts import control_plane_release as release
from scripts import kubernetes_benchmark_profile as cohort
from scripts.kubernetes_acceptance import evidence
from scripts.kubernetes_acceptance.selection import Selection

ROOT = Path(__file__).resolve().parents[1]
RUN = "perf-20261016-120000-abcdef01"
CANCELLED = "perf-20261016-120000-abcdef02"
CANDIDATE = "perf-20261016-120000-abcdef03"
UID = "11111111-1111-4111-8111-111111111111"
NS_UID = "22222222-2222-4222-8222-222222222222"
POD_UID = "33333333-3333-4333-8333-333333333333"


def fixture():
    """Return an isolated selection and complete nine-artifact object graph."""
    profile = cohort.load()
    nodes = [
        {
            "name": name,
            "uid": str(UUID(int=index + 100)),
            "kubernetesVersion": "v1.35.0",
            "containerRuntimeVersion": "containerd://2.2.0",
            "architecture": "amd64",
            "allocatable": {"cpuMilliCores": 4000, "memoryBytes": 8589934592},
        }
        for index, name in enumerate(profile.nodes)
    ]
    environment, _ = cohort.environment(profile, UID, nodes)
    identity = {"id": "pod-startup-contained", "version": "1.0.0", "sha256": "a" * 64}
    producer = {
        "name": "perfeng-analysis",
        "version": "0.8.1",
        "image": "example.invalid/analysis@sha256:" + "b" * 64,
    }
    raw_producer = {
        "name": "kube-burner",
        "version": "2.8.5",
        "artifact": {"kind": "oci-image", "image": "example.invalid/burner@sha256:" + "c" * 64},
    }
    policy = {
        "metadata": {"name": "pod-startup-k8s", "version": "1.1.0"},
        "spec": {
            "mode": "observe",
            "rules": [
                {
                    "id": "pod-ready-regression",
                    "metric": {"name": "k8s.pod.ready.duration", "statistic": "p95", "unit": "ms"},
                    "quality": {"minSamples": 10},
                    "regression": {
                        "reference": {
                            "baselineId": "approved-local-pod-startup-contained",
                            "version": "1.0.0",
                        }
                    },
                }
            ],
        },
    }
    entry = {
        "testId": "pod-startup-k8s",
        "contractsVersion": "0.22.0",
        "profile": "smoke",
        "catalogue": {"id": "platform-tests", "version": "1.2.0", "sha256": "d" * 64},
        "environment": environment,
        "workload": identity,
        "dataset": {"kind": "none"},
        "normalizerProducer": producer,
        "reportProducer": producer,
        "rawProducer": raw_producer,
        "candidateImages": ["registry.k8s.io/pause@sha256:" + "e" * 64],
        "policy": "policies/pod-startup.json",
        "benchmark": {"clusterId": "perfeng-local", "clusterUid": UID, "templateSha256": "f" * 64},
    }
    request = {
        "testSuite": entry["testId"],
        "catalogue": entry["catalogue"],
        "profile": "smoke",
        "candidate": {"gitSha": "a" * 40, "image": entry["candidateImages"][0]},
        "environment": {key: environment[key] for key in ("id", "version", "sha256")},
        "policy": {"id": "pod-startup-k8s", "version": "1.1.0", "sha256": "b" * 64},
    }
    selected = Selection(release.load_lock(), entry, policy, request)
    window = {"start": "2026-10-16T12:00:10Z", "end": "2026-10-16T12:00:20Z"}
    samples = []
    for at, end in ((11, 10), (20, 20)):
        for node in nodes:
            samples.append(
                {
                    "nodeUid": node["uid"],
                    "observedAt": f"2026-10-16T12:00:{at:02d}Z",
                    "conditions": {
                        "ready": "TRUE",
                        "memoryPressure": "FALSE",
                        "diskPressure": "FALSE",
                        "pidPressure": "FALSE",
                    },
                    "usage": {
                        "source": "metrics-api",
                        "windowStart": "2026-10-16T12:00:00Z",
                        "windowEnd": f"2026-10-16T12:00:{end:02d}Z",
                        "cpuMilliCores": 100,
                        "memoryBytes": 1048576,
                    },
                }
            )
    qualification = {
        "schemaVersion": 1,
        "kind": "KubernetesBenchmarkEvidence",
        "scope": {
            "kind": "measurement",
            "runId": RUN,
            "testId": entry["testId"],
            "workload": identity,
        },
        "profile": profile.identity(),
        "environment": environment,
        "target": {"clusterId": "perfeng-local", "kubeSystemUid": UID},
        "captureWindow": {"start": "2026-10-16T12:00:00Z", "end": "2026-10-16T12:00:21Z"},
        "status": "ACCEPTED",
        "checks": [
            {"id": key, "status": "PASS", "reason": "Synthetic fixture."}
            for key in (
                "cluster-identity",
                "node-readiness",
                "placement",
                "capacity",
                "resource-pressure",
                "interference",
            )
        ],
        "nodes": nodes,
        "samples": samples,
    }
    values = {
        "kubernetes-benchmark-evidence/v1": qualification,
        "kubernetes-benchmark-inputs-json": {
            "runId": RUN,
            "workload": identity,
            "candidateImage": request["candidate"]["image"],
            "templateSha256": "f" * 64,
            "substitutions": {"namespace": "perfeng-benchmark-" + RUN},
        },
        "runner-log": "Synthetic log\n",
        "kube-burner-job-summary-json": [{"uuid": RUN, "passed": True}],
        "kube-burner-pod-latency-json": [
            {"uuid": RUN, "jobIteration": index} for index in range(10)
        ],
        "kube-burner-pod-latency-quantiles-json": [{"uuid": RUN}],
    }
    claims = {}
    contents = {}

    def add(kind, value):
        payload = (
            value.encode()
            if isinstance(value, str)
            else json.dumps(value, allow_nan=False).encode()
        )
        claim = {
            "id": str(UUID(int=len(claims) + 1000)),
            "runId": RUN,
            "kind": "raw",
            "uri": f"s3://perfeng-artifacts/runs/{RUN}/{len(claims)}.json",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "sizeBytes": len(payload),
            "mediaType": "text/plain" if kind == "runner-log" else "application/json",
            "format": kind,
        }
        if kind in {"normalized-result/v1", "analysis-result/v1"}:
            claim["kind"] = "normalized"
        claims[kind], contents[kind] = claim, payload

    for kind, value in values.items():
        add(kind, value)
    raw = {
        "createdAt": "2026-10-16T12:00:20Z",
        "schemaVersion": 2,
        "kind": "RawResult",
        "runId": RUN,
        "testId": entry["testId"],
        "contractsVersion": "0.22.0",
        "workload": identity,
        "measurementWindow": window,
        "producer": raw_producer,
        "artifacts": list(claims.values()),
    }
    values["raw-result/v2"] = raw
    add("raw-result/v2", raw)
    normalized = {
        "schemaVersion": 1,
        "kind": "NormalizedResult",
        "createdAt": "2026-10-16T12:00:21Z",
        "runId": RUN,
        "testId": entry["testId"],
        "contractsVersion": "0.22.0",
        "workload": identity,
        "measurementWindow": window,
        "producer": producer,
        "sourceArtifacts": [claims["kube-burner-pod-latency-json"]],
        "results": [
            {
                "schemaVersion": 2,
                "runId": RUN,
                "metric": {
                    "name": "k8s.pod.ready.duration",
                    "unit": "ms",
                    "direction": "lower-is-better",
                },
                "distribution": {"samples": 10, "cv": 0.2},
            }
        ],
    }
    values["normalized-result/v1"] = normalized
    add("normalized-result/v1", normalized)
    report = {
        "schemaVersion": 1,
        "kind": "AnalysisResult",
        "createdAt": "2026-10-16T12:00:22Z",
        "runId": RUN,
        "testId": entry["testId"],
        "contractsVersion": "0.22.0",
        "producer": producer,
        "policy": {**request["policy"], "mode": "observe"},
        "blocking": False,
        "candidateArtifact": claims["normalized-result/v1"],
        "referenceArtifacts": [],
        "evaluations": [
            {
                "ruleId": "pod-ready-regression",
                "metric": policy["spec"]["rules"][0]["metric"],
                "quality": {"status": "PASS", "reasons": [], "samples": 10},
                "slo": {"status": "NOT_EVALUATED", "reasons": []},
                "regression": {"status": "INCONCLUSIVE", "reasons": ["No approved baseline."]},
            }
        ],
    }
    values["analysis-result/v1"] = report
    add("analysis-result/v1", report)
    assert set(claims) == evidence.FORMATS
    return selected, claims, values, contents


def reservation(run_id=CANCELLED, *, released=False):
    """Build a synthetic stable hold or complete two-minute cleanup attestation."""
    row = {
        "runId": run_id,
        "targetId": "perfeng-local",
        "clusterUid": UID,
        "namespaceUid": NS_UID,
        "planSha256": "c" * 64,
        "startedAt": "2026-10-16T12:00:00Z",
        "fencedAt": "2026-10-16T12:00:01Z",
        "notBefore": "2026-10-16T12:02:01Z",
        "releasedAt": None,
        "proof": None,
    }
    if released:
        row["releasedAt"] = "2026-10-16T12:02:02Z"
        row["proof"] = {
            "schemaVersion": 1,
            "runId": run_id,
            "planSha256": row["planSha256"],
            "clusterUid": UID,
            "namespaceUid": NS_UID,
            "executorStopped": True,
            "workloadStopped": True,
            "authorityRemoved": True,
            "namespaceFenced": True,
        }
    return copy.deepcopy(row)
