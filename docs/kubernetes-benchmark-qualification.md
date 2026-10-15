# Local Kubernetes benchmark qualification

## TL;DR

- Use `task kubernetes:qualify` to inspect the existing cluster; no Kubernetes resources are changed.
- This is a local integration cohort, not a calibrated production-performance laboratory.
- Preview/validation are offline; observation requires `--execute` and dedicated kind credentials.
- Each capture creates new qualification and environment JSON files under ignored `results`.
- Missing/stale metrics remain UNKNOWN; pressure and concurrent/unreviewed workloads reject qualification.
- Preflight does not reserve a target, launch kube-burner, approve a baseline or complete M04.

## Ownership and profile

This workflow consumes `kubernetes-benchmark-evidence/v1` from contracts bundle
0.22.0 through a checksum/source-pinned local snapshot under `contracts`.
Ordinary validation requires no sibling or network. The pinned integration tier
checks the exact source Git revision separately. Environment composition owns
policy/collection; `k8s-infrastructure` retains cluster lifecycle ownership.
See [ADR 0011](decisions/0011-qualify-local-kubernetes-benchmarks.md).

`profiles/kubernetes/local-kind-integration.json` selects the existing named
three-node kind cluster, Linux AMD64, Kubernetes v1.35.0 and the infrastructure
lock's exact node image. Names and `workload` labels must match the reviewed
control-plane/generator/SUT placement. Cordoned or extra/missing nodes fail.

| Condition | Integration policy |
|:--|:--|
| Allocatable capacity per node | At least 500 CPU millicores and 1 GiB memory |
| Observed usage per node | At most 75% allocatable CPU and 85% allocatable memory |
| Metrics freshness | At most 120 seconds old; never future-dated |
| Provider measurement window | Positive, at most 60 seconds |
| Active namespaces | kube-system, local-path-storage, argocd, perf-platform, perf-sut and monitoring |

Pending, Running and Unknown-phase Pods count as active. Active generator Pods
or workloads in other namespaces fail interference. Only Pod placement/phase/
namespace columns are read, never Pod environments or Secrets. These are local
safety/capture budgets, not statistically calibrated quality thresholds. Change
policy through a reviewed version; never edit a receipt to satisfy it.

`local-path-storage` is included explicitly for the storage provisioner shipped
in the selected kind node image. Its manifest was inspected on the verified
named node during live acceptance. This is not automatic adoption of whatever
namespace happens to be running; namespace allowance is not workload isolation.

All kind nodes share a Docker host. Placement labels do not isolate CPU, memory,
disk or network. Node metrics/namespace checks cannot rule out contention from
host programs or other containers. Explicit integration purpose prevents these
captures being presented as production-performance qualification.

## Preview and collect

From this repository, validate or preview without external access or writes:

```powershell
uv run --locked python -m scripts.kubernetes_benchmark validate
uv run --locked python -m scripts.kubernetes_benchmark check
```

Collect through the opt-in task; it chooses new filenames automatically:

```powershell
task kubernetes:qualify
```

Or explicitly choose a new result filename:

```powershell
uv run --locked python -m scripts.kubernetes_benchmark check --execute --output results/m04-preflight.json
```

Only new JSON files directly under ordinary ignored `results` are allowed.
Existing files, links/junctions and redirected paths are refused before cluster
reads. No PowerShell timestamp or shell redirection is necessary.

Four stages identify Docker/kind/API, inventory, metrics and evaluation. Commands
share a 120-second budget; Kubernetes requests use 10-second timeouts. Resource
access waits until local Docker and the dedicated kubeconfig match the named
kind cluster. The actual kube-system UID is checked again after observation;
changed/unverified incarnation prevents publication. Ambient context is ignored.

Exit 0 means six PASS checks and accepted integration evidence. Exit 1 means a
retained UNKNOWN/REJECTED capture. Exit 2 means an input/inspection/publication
error; Ctrl+C exits 130. No exit code grants dispatch or cleanup authority.

## Retained bytes and identity

The expected environment descriptor is written first; the qualification receipt
is the completion marker. Both appear atomically, create-only. Failed publication
may retain a complete descriptor but never an accepted partial receipt.

The descriptor records selected integration conditions. Actual node facts and
mismatches remain in qualification evidence. Its fingerprint binds exact profile
bytes, the selected node image, actual cluster incarnation and observed stable
node inventory (UIDs, versions, architecture and allocatable resources); transient load and
timestamps do not change it. Recreated clusters need a new qualification/review.
The receipt's environment SHA-256 hashes exact persisted descriptor bytes, not
the qualification report. Use each separately printed artifact hash correctly.

Fingerprint inputs are `profileSha256`, `nodeImage`, `kubeSystemUid` and
`observedNodes`. Sort nodes by name, omit conditions, serialize with sorted
object keys, compact separators and JSON ASCII escapes, then SHA-256 the UTF-8
bytes. The descriptor itself uses two-space JSON plus a trailing LF. Profile
and selected node-image changes during capture prevent publication.

This is preflight, not measurement-scoped Run evidence. It records one observation
per node, not sustained monitoring. Original fresh provider windows are retained;
the capture window includes that lookback and the collector period. Kubernetes
CPU quantities become integer millicores, memory becomes bytes. Capacity rounds
down and usage rounds up conservatively. Unavailable usage never becomes zero.

## Failure and recovery

- Unverified Docker/cluster/credentials: inspect `task status` and dedicated
  `.local/perfeng-local.kubeconfig`; no adoption, restart or recreation occurs.
- UNKNOWN usage/interference: inspect node Metrics API availability/access,
  provider timestamps and clock. Check [the selected metrics prerequisite](local-resource-metrics.md)
  with `task kubernetes:metrics-health`. Its separate restricted GitOps Application
  supplies verified-TLS metrics; this collector still installs or repairs nothing.

- FAIL readiness/pressure: inspect actual node conditions, without weakening gates.
- FAIL placement/capacity: inspect names, labels, cordons, image, version,
  architecture and budgets against the selected profile.
- FAIL interference: finish/explicitly stop other benchmarks and inspect usage
  or unreviewed workloads. This collector never stops workloads.
- Existing receipt: retain it and use the default task for a new filename.

For an UNKNOWN usage check, these dedicated-context commands are read-only:

```powershell
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local --request-timeout=10s get apiservice v1beta1.metrics.k8s.io
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local --request-timeout=10s get --raw /apis/metrics.k8s.io/v1beta1/nodes
```

M03 remains deferred pending a suitable laptop. This cohort does not waive its
Windows gates. M04 stays incomplete until dispatch, verified artifacts, approved
baseline reporting and safe recovery pass separate acceptance.

## Harness, rollout and rollback

Run `make verify`, `make validate` and `make release-selection-check`. The latter
requires the exact contracts commit; normal verification uses the local snapshot.
Tests cover offline preview, unknown data, budgets/units/freshness, source drift,
foreign credentials, stable identities and create-only/race-safe output.
Run the explicit live task after merge and retain the summary; fixtures cannot
qualify a cluster. Rollback stops future use while retaining all receipts,
credentials, namespaces, volumes and workloads. No infrastructure mutation is
part of this workflow or rollback.
