# Local platform troubleshooting

## TL;DR

- The selected infrastructure release `59b175492aad...` uses digest-pinned
  Docker Hub Redis with `IfNotPresent` after two prior CI jobs reported
  ECR Public pull throttling. Existing clusters receive this bootstrap change
  only through explicit application by the supported owner. Inspect the running
  image/policy and bounded event evidence before choosing a remedy; registry
  quotas and cold-pull availability still require live acceptance. Provider
  rollback restores the lock, CI and metrics source pins together; preserve
  request history, certificates, credentials and retained resources.
- Trusted-main `missing-read-grant` is an intentional readiness refusal. The
  pinned native Application observer returns 64 with the exact Forbidden get
  diagnostic; Python preflight's wrong-digest case returns 2. The earlier
  verifier expected 1 and rejected the correct native refusal in run
  40207959980. Preserve the denial and restore only the fixture's existing
  RoleBinding through its explicit correction step. Do not grant broader access
  or accept arbitrary nonzero exits to repair an evidence-check mismatch.
- Trusted-main `[PREREQUISITE:argocd-bootstrap]`: inspect
  `argocd-startup.failure.json` for fixed startup categories and allowlisted Pod
  status and UID-correlated event categories. `rate-limit` suggests checking
  registry throttling; `http-authorization` requires checking the selected
  registry access; `registry-unavailable`, `network` and `tls` require checking
  the corresponding registry/node path. These are diagnostic hints, not proof
  that a permission or image change is required. `events: unavailable` means
  event capture failed. Raw messages/logs are withheld. `diagnostics: unavailable`
  means capture failed, not that Redis is healthy. Preserve the original rollout failure;
  choose a remedy from evidence before changing pins, credentials or deadlines.
- CRD readiness no longer calls the null-sensitive condition wait before the
  naming controller initializes status. Exact accepted-kind readiness precedes
  `Established=True`; both CRDs share the original 90-second outer bound. A
  timeout still means bootstrap failed; do not treat initialized names alone as
  an established CRD.
- A completed `bucket-ci-observer` during later retention checks previously came
  from its 900-second lifetime. Its bound now matches the approved 60-minute
  trusted-main job budget. Rerun through the owned disposable workflow; do not
  replace an observer in a retained environment as an implicit repair.

- For adoption ledger conflicts or interrupted creation, use
  [release adoption recovery](release-adoption.md#partial-failure-and-recovery).
  Preserve receipts and pointer; do not apply, patch, delete or infer empty state.

- For trusted Windows-worker API/S3 access or search-target failures, follow the
  [native connectivity checklist](native-worker-connectivity.md#failure-and-recovery).
  TLS/readiness is not proof of signed upload, internal read-back or acquisition.

- For a failed staged maintenance checkpoint, follow the
  [quiescence recovery boundary](release-maintenance-checkpoint.md#evidence-failure-and-recovery).
  It repairs nothing; keep manual pause/migration/sync and retained evidence.

- For the staged `perfeng-release-maintenance-pause-v1`, a baseline refusal
  means no workload write was authorized; inspect the exact adopted request,
  selected readiness, receipt and pointer. A later failure can leave root/child
  paused or the Deployment stopped. A guard conflict requires inspection, not
  relaxed tests or cached-node retry. Preserve ledger/data/Secrets and use the
  [pause recovery boundary](release-maintenance-pause.md#failure-recovery-and-remaining-acceptance).
  Check the separately installed bootstrap grants for named Application patch
  and ledger get access on `release-maintenance-pause`. It has no ledger write
  authority. Local execution and host-command replacement remain deferred.

- Run every command from the `perfeng-environment` repository and use
  `.local/perfeng-local.kubeconfig`; the similarly named context in the global
  kubeconfig may point to an earlier kind API-server port.
- Diagnose in dependency order: Docker, kind, Argo CD, Applications, stateful
  prerequisites, control-plane workload, then the end-to-end smoke path.
- Stop at the first failing layer. A passing smoke run proves the product path,
  but it does not waive a persistent GitOps health failure.
- Use `task status` for the complete platform and the first failing underlying
  Python command for focused diagnosis or recovery.
- Commands marked **read-only** inspect or wait. Commands marked **mutating**
  intentionally change the local environment. Run no **destructive** command
  unless loss of the disposable cluster and all cluster-local data is intended.
- Never paste Secret YAML, credential environment variables, decoded tokens or
  unreviewed logs into an issue.

## Disposable Argo CD acceptance

For `changed-request` and `upgrade-request`, inspect the scenario's generated
request/parameter files and safe `.outcome.json` digest/UID reports. Require
successful nested Git preflight/readiness followed only by plan exit 2. Changed
bytes must report the current-pointer conflict; upgrade intent must report
`request/readiness identity differs or adoption is not initial`. Wrong Git path,
digest, readiness or quota failures do not qualify. Original receipt/pointer
objects must remain unchanged and candidate receipts absent; no later worker or
adoption result may appear. Never rewrite a request's bytes to retry or use
adoption to advance a pointer. See
[request boundary acceptance](release-workflows-integration.md#adoption-request-boundary-acceptance).

For `missing-receipt` and `replaced-receipt` adoption cases, require successful
readiness/root/ledger reads and only `plan` failure with exit 2 and
`current pointer conflicts or references a missing/replaced receipt`. Inspect
the safe `.outcome.json` UID report alongside Workflow/worker evidence. The
pointer must retain its original UID, bytes and receiptUID; replacement must
preserve payload/provenance but have a different UID. No result or downstream
worker may appear. The approved destructive fixture runs only after retention
proof and leaves the conflict intact for cluster teardown. Never apply this
delete/recreate sequence as a retained-environment repair. A conditional DELETE
conflict, unexpected create conflict or unrelated refusal must fail acceptance;
do not retry deletion without preconditions or patch the pointer. See
[receipt identity refusal acceptance](release-workflows-integration.md#receipt-identity-refusal-acceptance).

For receipt-only recovery, choose the scenario-specific trusted-main artifact.
Require a successful readiness/receipt/pointer-plan prefix and only
`create-pointer` failure with exit 1 and quota exhaustion naming
`ci-adoption-receipt-only`, `count/configmaps` and `perfeng-local-current`.
Unexpected root changes, missing quota initialization or unrelated worker errors
are failures. Preserve the immutable receipt and absent pointer. The fixture may
raise only its original quota after UID/resourceVersion/old-limit tests; a fresh
parent must reuse the receipt and create only the pointer. Do not delete history,
rewrite provenance or retry successful nodes. Ref cleanup must preserve any
cross-scenario or moved ref for inspection. See
[receipt-only acceptance](release-workflows-integration.md#receipt-only-recovery-scenario).

For complete adoption-parent CI, inspect `adoption-parent-*.workflow.json`, worker
projections, results and constant outcomes. The deliberate root-read refusal must
follow successful readiness and name `release-adoption`, `perfeng-local-root`,
`applications`, `argocd` and `Forbidden`; unrelated API/image/artifact failures
are not expected refusal. Restore only the checked-in root-read grant before a
fresh submission. A root change during planning fails conservatively; preserve
records/evidence and inspect competing syncs or merges rather than pausing the
controller, retrying successful nodes, patching the ledger or weakening policy.
Record snapshots and raw logs remain private. See
[complete adoption acceptance](release-workflows-integration.md#complete-existing-release-adoption-parent).

The adoption `root-drift` case must finish readiness and root/current/receipt
reads, then fail only at `plan` with main exit 2 and the exact stale-root
diagnostic. Require the actual root's only difference to be the `perf-sut`
Namespace label, still `ci-drift`, with selected revision, manual policy and six
child skips preserved. Unrelated conditions, worker errors or storage changes
are failures, not this expected refusal. The fixture repairs through a fresh
selected-commit root sync and submits a fresh adoption parent; it never edits
ledger records or fabricates status. Inspect the existing private snapshots and
safe `adoption-parent-root-*` evidence to distinguish refusal from repair.

For concurrent exact retries, inspect `adoption-lock-held.workflow.json`,
`adoption-lock-queued.*`, the completed holder, both `adoption-parent-concurrent-*`
cases and `adoption-lock.result.json`. Both parents must wait for the actual
holder's exact namespaced mutex without workers/results. A missing wait state,
holder timeout, changed UID, overlapping controller execution or changed ledger
is a failure. The fixture resumes only its native suspended holder; do not remove
production synchronization, alter status, rewrite records or retry until green.
Normal owned-cluster teardown cleans up on failure. This tests exact retries
against an existing baseline, not initial-create races or external operators.

For the final bounded root phase, inspect `root-*.application.json`,
`root-*.children.json` and the constant Namespace-drift outcome. Check the exact
selected revision, fresh operation marker, complete fourteen-object inventory
and all six child skip annotations. Root may restore committed foundation specs,
but must not activate child operations or change workloads/data. A moving `main`,
missing skip, unexpected operation or retention mismatch is a failure; do not
enable auto-sync, unskip a child, filter Git sources or repair storage to pass.
Raw snapshots remain private. Root-only convergence is not full-tree health.
See [bounded root acceptance](release-workflows-integration.md#bounded-root-reconciliation).

For SeaweedFS Application adoption, inspect `seaweedfs-application-*.json` and
`seaweedfs-chart-adopt-*` native/peer evidence. A timeout, comparison failure or
unexpectedly reachable private port is a failure, not the deliberate Service
name drift. Confirm exact chart revision, real proxy access and the existing
repository authorization before changing configuration. Preserve credentials,
proxy data, PVC/PV and sentinel evidence; do not force/replace a StatefulSet,
recreate storage, enable root/automatic sync or upload raw logs/Secret snapshots.
See [SeaweedFS Application acceptance](release-workflows-integration.md#seaweedfs-application-adoption).

For PostgreSQL Application adoption, inspect `postgres-application-*.json` and
`postgres-chart-adopt-*.workflow.json`/`*.client.json`. Check the selected source
commit and exact environment repository access before changing manifests. A
comparison, installation or timeout error is not the deliberate Service-name
drift. Do not enable automatic/root sync, force/replace a StatefulSet, rotate
credentials or recreate a PVC to pass adoption. Preserve private snapshots and
inspect any state mismatch; never upload them or repository-server logs. See
[PostgreSQL Application acceptance](release-workflows-integration.md#postgresql-application-adoption).

For complete readiness-parent CI, inspect `readiness-parent-*.workflow.json`,
worker projections and constant outcomes. The wrong digest must fail only at
`verify-identities`; a deliberately revoked Application-read grant must fail
only at `application`, after successful preflight/foundation observations.
Artifact/image/network/timeout errors are unexpected failures, not evidence of
these refusals. Restore only the checked-in readiness Application-read bootstrap
grant before a fresh submission; never broaden RBAC, substitute observations,
retry successful nodes or change a published verifier to obtain a result.
See [complete parent acceptance](release-workflows-integration.md#complete-release-readiness-parent).

For disposable trusted-main Argo CD acceptance, inspect `argocd-*.failure.json`
for safe sync/health/operation phases and condition types. Verify the selected
bootstrap and private repository access before changing manifests. Installation,
source and timeout failures do not count as expected drift. Deliberate Service
drift must remain `OutOfSync` until explicit manual correction. Never enable root
or automatic sync, or upload repository-server logs, Secret snapshots or token
files as a repair. See [actual Argo CD convergence](release-workflows-integration.md#actual-argo-cd-convergence).

## Scope and command safety

For the staged release-readiness Workflow, inspect the failing observation or
verification node. Native waits stop at their deadlines; a missing resource
fails immediately. A final verification failure can mean stale generations,
wrong release/configuration or an invalid operational endpoint response even
when Kubernetes reports ready replicas. Use the existing owner diagnostics and
M04 recovery procedure, then resubmit the same exact request only after the
prerequisite is corrected. This gate cannot refresh/sync Argo CD, change a
workload or issue a successful result before its HTTP probes pass. See
[release readiness and evidence limits](release-readiness.md).

For a staged release-workflows sync denied by its AppProject, run **offline**
`make render-check`. The rendered script ConfigMap needs core `ConfigMap` in
`release-workflows-bootstrap/project.yaml`'s namespaced allowlist and
`perf-platform` in its destinations. Keep the explicit allowlists; do not grant
wildcard access to bypass the failure. The gate also reports duplicate GitOps
identities and Job ConfigMap references missing from the Job's namespace or
missing required data keys. Review the owned source, synchronize the separately
applied bootstrap Project through its approved channel, then manually synchronize
the staged Application before submitting a Workflow. See the
[layout and validation guide](../gitops/README.md). Rendering does not apply a
Project or change cluster state.

For the staged external-state Workflow on a **disposable kind cluster**, inspect
the Workflow UID, failing step and the Job name in its output or node status.
`task-selection` failure means the reviewed control-plane lock differs from the
v1 task selection. Compare its literal `expected-lock-digest` with the raw lock
bytes at the reviewed selection commit. A lock promotion must include review of
the preparation children and a matching stable-parent pin; `make verify` checks
that relationship against the checked-in lock. Preserve exact-byte refusal and
submit a fresh Workflow after the reviewed correction. Never copy an unreviewed
lock digest from a request into the approval pin. A `task-selection` refusal creates no
preparation Job and is not a bucket, SQL or registry failure.
A bucket Job failure may mean a denied lookup rather than an absent
bucket; do not treat all `head-bucket` errors as permission to create. A role
Job failure can indicate an unsafe existing role, a missing credential Secret
or a password mismatch; restore the matching Secret and inspect role state
without rotating credentials. A registry Job failure can indicate a missing
pull Secret, denied GHCR access or an unavailable digest. Retain failed Jobs,
repair only the confirmed prerequisite and submit a new Workflow with the same
request bytes. See [staged preparation and recovery](release-external-state.md).

For missing Kubernetes resource metrics, run **read-only** `task kubernetes:metrics-health`
and follow [the resource-metrics guide](local-resource-metrics.md). The command checks the
exact live Application/Project and public API CA, then delegates health to the pinned provider.
Missing external TLS material or unissued kubelet serving CSRs need explicit provider setup,
not Helm deployment, automatic approval or a TLS bypass. Ensure the Argo CD Contents-read
credential includes `k8s-infrastructure`. Newly generated CA values need a reviewed selection;
never rotate a Secret or edit evidence to make health pass. Final GitOps health includes
this new prerequisite; staged stateful bootstrap deliberately does not wait for it.

For M04 qualification, run **read-only** `task kubernetes:qualify` and follow the
[benchmark qualification guide](kubernetes-benchmark-qualification.md). Missing,
stale or denied Metrics API observations remain UNKNOWN, not zero load. Known
pressure, placement/capacity failures or competing workloads reject the capture.
Retain both result files and inspect the failed check. No metrics-server install,
Docker restart, credential change or workload cancellation happens implicitly.
Preflight is not a target reservation or production-performance qualification.

For native gateway failures, start with **read-only** `task native:doctor`.
It reports independent tool/checksum/build, private-state, Docker, dedicated API,
resource, port and trust checks. `RELEASE_UNSELECTED` means the first audited
serving binary has not been published/selected, not that Docker needs resetting.
`TOOL_VERSION` includes expected/observed metadata and executable path; a manual
build may print `unknown`. Do not recompile by hand or change PATH: use the
[managed setup and release selection](native-worker-connectivity.md#managed-setup).
`PORT_CONFLICT` requires inspection of the exact listener, not killing every
Caddy/kubectl process. Stop your old independent sessions before `native:up`.
`RELEASE_TOOLCHAIN` means the selected gateway asset predates the reviewed
Go 1.27.3 serving profile. Its recorded checksum/compiler provenance must stay
truthful. Publish and select a patched release through the existing explicit
handoff; do not relabel the asset, downgrade the gate or delete installed/CA
state. See [ADR 0029](decisions/0029-require-patched-native-gateway-toolchain.md).

`HOST_SECURITY_TIMEOUT` means the fixed Windows PowerShell operation exceeded
its 40-second budget, including cold startup. Inspect host load and gateway state
before repeating a permissions action: it may have applied before timing out.
The operation is never retried automatically and vendor output remains private.
This budget stays below the doctor's 45-second command budget; a timeout is not
evidence of an ACL violation. The explicit ACL checks remain unchanged.

`STATE_PERMISSIONS`/`HOST_SECURITY` require review of the gateway directory's
Security settings; `task native:permissions` is a separately prompted local
permission change, never an implicit doctor repair. Previously exposed CA keys
require a separate trust/CA review, not an automatic rotation. API/cluster probes
have bounded command budgets; failures retain all cluster data. `native:up`
stops its own children on failure, interruption or one-hour expiry and retains CA.


For image-backed target failures, use [the local native target guide](local-native-target.md).
Run `task native:target-health` before `task native:target`; candidate package
access, ready platform digest and UID controller ownership must agree. Restart
the target's fourth foreground forward after Pod replacement and restart an old
gateway to apply the proxy-only route. A missing target/tunnel fails closed,
never back to checkout files. Retain pull credentials, CA and rejected evidence.

For candidate image build/publication or rejected browser-host qualification,
follow the [candidate guide](native-browser-candidate.md). GUID braces/case are
equivalent power-plan identity; hardware, OS, display and calibration mismatches
still require review. Never edit a rejected report to qualify a run. Image
functional verification does not replace base/runtime advisory review.

For the four-core 4K laptop, select
`--profile profiles/browser/windows-four-core-4k.yaml` as documented in the
[host guide](windows-browser-host.md#select-a-comparison-cohort). Use it for both
the live host check and environment assembly, and match the runner's profile
identity/fingerprint. A missing selection still uses mainstream and will reject
the different hardware. OS build 22621 or background CPU 20% still rejects the
laptop profile; resolve the independent gate before making a fresh capture.
Never alter the earlier report, reuse its hash as environment evidence or mix
the two cohorts' baselines. These checks do not modify host settings.

This runbook diagnoses the supported local Windows and kind environment. It is
not a production incident procedure. Start PowerShell in the repository root:

```powershell
Set-Location C:\Source\perf-eng\perfeng-environment
$kubeconfig = (Resolve-Path .local/perfeng-local.kubeconfig).Path
```

The labels used below mean:

| Label | Meaning |
|---|---|
| **read-only** | Reads local or cluster state, or waits for a condition |
| **mutating** | Creates, restarts or reconciles an explicitly named local resource |
| **destructive** | Deletes durable or broad local state |

Preview-first repository commands remain previews until `--execute` is supplied.
The cluster command does not deploy workloads. The control plane has no separate
Helm deployment command; Argo CD is its only workload reconciler. Use
`control_plane_health.py --execute` for its focused read-only check.

## Operator task boundaries

Use `task bootstrap` for a fresh or already-owned local cluster, then complete
the policy and control-plane preparation documented in the
[GitOps guide](local-gitops.md). Use `task status` only when checking the complete
platform: it is expected to fail during the intentional staged-bootstrap gap.
Use the individual Python command from the first failing layer below when a task
needs diagnosis or partial recovery.

If `task` is not recognized, install Task 3 and open a new terminal so the
executable is on `PATH`. Do not replace it with an unrelated executable also
named `task`. `task --list` must show `bootstrap`, `status`, `smoke` and
`destroy` from this repository's `Taskfile.yml`.

`task smoke` is mutating because it creates retained Run evidence. `task destroy`
is destructive and requires an interactive confirmation; it removes all local
database, object-store and run state with the named kind cluster.

## Quick health checklist

Run the checks in order and stop at the first failure.

| Layer | Command | Healthy result |
|---|---|---|
| Workspace | `uv run --locked python scripts/workspace.py status` | Every repository is `existing`; no destination is missing or conflicting |
| Docker | `docker context show` and `docker version` | Context is `desktop-linux`; client and Linux server respond |
| Infrastructure selection | `uv run --locked python scripts/infrastructure_release.py validate` | The selected provider commit and compatibility contract validate |
| kind | `uv run --locked python scripts/infrastructure_runtime.py cluster health --execute` | The named cluster, kubeconfig, nodes, labels and CoreDNS pass |
| Argo CD | `uv run --locked python scripts/infrastructure_runtime.py argocd health --execute` | The infrastructure-owned namespace and every selected controller pass |
| Applications | `kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd get applications -o wide` | Root and all five workload Applications are `Synced` and `Healthy` |
| PostgreSQL | `uv run --locked python scripts/postgres.py health --execute` | PostgreSQL health completes successfully |
| Object storage | `uv run --locked python scripts/object_store.py health --execute` | SeaweedFS health completes successfully |
| Database identities | `uv run --locked python scripts/control_plane_database.py health --execute` | Owned roles and credentials validate without rotation |
| Migration | `uv run --locked python scripts/control_plane_migration.py health --execute` | The locked migration Job and runtime grants validate |
| Runtime inputs | `uv run --locked python scripts/control_plane_runtime.py health --execute` | The selected Secrets and immutable ConfigMaps validate |
| GitOps workload | `uv run --locked python scripts/gitops.py health --execute` | All Applications and both managed workloads pass |
| Product path | `uv run --locked python scripts/control_plane_smoke.py --execute` | A Run completes with five verified artifacts |

The product-path command is **mutating**: it creates a Run, Kubernetes Jobs and
immutable evidence. Most health checks are read-only. Object-store health creates
a bounded `s3-health-*` diagnostic Job and retains it for inspection, so
`task status` does not reconcile desired state but is not strictly read-only.
Prerequisite discovery contacts Docker, kind or Kubernetes.

## Docker or kind does not respond

Symptoms include `kind get clusters` hanging, an empty Docker container list
after cleanup, or a connection failure before any Kubernetes check.

**Read-only** checks:

```powershell
docker context show
docker version
kind get clusters
```

Docker Desktop must be running with Linux containers. After a Docker Desktop
restart, wait until `docker version` reports both client and server before
retrying kind. Removing images only makes the next bootstrap slower; it does not
invalidate the repository configuration.

If `perfeng-local` is genuinely absent, preview and then create it:

```powershell
# read-only preview
uv run --locked python scripts/infrastructure_runtime.py cluster ensure

# mutating
uv run --locked python scripts/infrastructure_runtime.py cluster ensure --execute
```

For the supported end-to-end bootstrap, run `task bootstrap` instead. It uses
the idempotent ensure boundary and continues through the stateful GitOps
foundation. If a cluster exists without its dedicated kubeconfig, or the
kubeconfig exists without the named cluster, ensure stops for operator diagnosis
instead of adopting or deleting either side.

After intentional removal of all Docker containers and volumes, the old cluster's
persistent data and external metrics TLS Secret are gone. Preserve host credentials and
receipts; verify the cluster is absent before archiving an orphaned dedicated kubeconfig.
Rebuild through staged bootstrap, then follow
[metrics trust recovery](local-resource-metrics.md#recovery-after-docker-storage-removal).
The new CA needs a reviewed Application selection; do not bypass TLS or compete with
Argo CD using Helm. Cold image pulls may exceed a readiness deadline: inspect events and
resume the failed supported stage after the prerequisite becomes ready. A recreated
cluster also needs fresh qualification and reviewed benchmark-bundle UID promotion before
runtime activation; old receipts must not be edited to fit it. Recreating infrastructure
does not restore deleted PostgreSQL or object-store data.

The reviewed recovery selection now uses bundle/package 1.0.1 for kube-system UID
`257a288d-9e0f-4956-a0e3-9c4104de6476` and metrics chart 1.0.1 with the retained
public CA/Secret. The chart omits the APIService default-false TLS flag, resolving
readback-only drift without ignore rules or TLS bypasses. After merge reconcile
the staged root and verify metrics Synced/Healthy before qualification. A Missing
control-plane Deployment is supported by the existing `kubernetes:prepare` path:
it pauses existing owned root/child Applications and refuses any remaining
control-plane Pods, even when the Deployment is absent. There is no placeholder
or separate first-install command. Require API/foundation/metrics health and the
selected cluster UID before preparation; an API timeout blocks execution.
Complete preparation and policy publication before manually activating the child.
See [fresh-cluster recovery](kubernetes-runtime-deployment.md#fresh-cluster-recovery).

Do not use the provider `cluster down` action or `task destroy` as a repair.
Either is **destructive** and permanently removes all PostgreSQL, SeaweedFS and
Run state stored in the cluster.

## Plain kubectl cannot connect

A message such as `dial tcp 127.0.0.1:<port>: connectex: ... actively refused`
usually means the global kubeconfig retained the API-server port of a deleted
kind cluster. The context name may still be `kind-perfeng-local`, so the name
alone is not proof that the endpoint is current.

Use the repository-owned kubeconfig explicitly:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local get nodes
```

For convenience, redirect only the current PowerShell process:

```powershell
$env:KUBECONFIG = $kubeconfig
kubectl --context kind-perfeng-local get nodes
```

Remove the process override when finished:

```powershell
Remove-Item Env:KUBECONFIG
```

The environment tools deliberately do not rewrite the user's global
`~/.kube/config`. Do not run `kind export kubeconfig` as routine project repair;
it broadens a local operation into global configuration maintenance.

## Argo CD controllers are unavailable

First run the selected provider's read-only health boundary:

```powershell
uv run --locked python scripts/infrastructure_runtime.py argocd health --execute
```

Inspect the controller workloads and recent warnings:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  get deployments,statefulsets,pods
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  get events --field-selector type=Warning `
  --sort-by=.metadata.creationTimestamp
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n kube-system `
  get pods -l k8s-app=kube-dns
```

`Pending`, `CrashLoopBackOff`, high restart counts, or DNS timeout events identify
an infrastructure failure rather than Application drift. Preserve the warning
output before recovery.

If CoreDNS is ready but a repo server or application controller repeatedly
reports transport or DNS failures, recreate only its Pod and wait for its owner
to restore readiness:

```powershell
# mutating; use only after recording the failure
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  delete pod -l app.kubernetes.io/name=argocd-repo-server `
  --wait=true --timeout=120s
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  rollout status deployment/argocd-repo-server --timeout=300s
```

Use the equivalent exact label and StatefulSet rollout only when the failing Pod
is `argocd-application-controller`. Do not restart every Argo CD component at
once; doing so removes the evidence needed to identify the failing boundary.
On a fresh cluster, use `infrastructure_runtime.py argocd bootstrap --execute`
before the consumer `gitops.py bootstrap --execute`. Do not use consumer
bootstrap to repair missing provider controllers.

## An Application is OutOfSync or Degraded

Inspect all Applications, then describe only the failing one:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  get applications -o wide
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  describe application perfeng-local-root
```

Interpret the result before changing anything:

- A primary Application wait timeout prints up to 20 reported out-of-sync
  resources and requests one normal comparison refresh before its single
  60-second grace period. This announced metadata mutation has a 10-second API
  timeout; it is not an explicit sync and leaves reconciliation policies
  unchanged. A refresh warning does not replace the original timeout.
- `operation=Succeeded`, `health=Healthy`, healthy child Applications and a
  recent expected revision usually indicate delayed aggregate status. Wait for
  one reconciliation interval and rerun GitOps health once. If the output says
  no individual drift was reported, the comparison may be stale; do not assume
  synchronization or reset the cluster.
- A revision older than the merged `main` commit points to delayed repository
  refresh or private-repository access. Inspect Application conditions and the
  repo-server Pod before rotating credentials.
- `Degraded` or a named resource with persistent drift is not transient. Inspect
  that resource and its events; do not run Helm or manually apply a competing
  manifest.
- A passing control-plane smoke run proves the execution path still works. Keep
  the GitOps health gate strict until its management-plane status also converges.

Check only that the repository credential exists; never print its data:

```powershell
# read-only; does not retrieve Secret content
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n argocd `
  get secret perfeng-github-repository-credentials -o name
```

For an expired or incorrectly scoped credential, follow the explicit immutable
Secret rotation procedure in [the GitOps guide](local-gitops.md). The token needs
read-only Contents access to the selected private repositories, not write access.

## A prerequisite health check fails

Run the prerequisite checks in the order shown in the quick checklist. A later
failure is often only a consequence of an earlier missing service.

- If PostgreSQL or SeaweedFS credentials are missing on a fresh cluster, run the
  corresponding `bootstrap` action before `gitops.py bootstrap --execute`.
  Bootstrap does not deploy a workload or rotate an existing identity.
- If either stateful workload is missing after credentials exist, inspect its
  child Application and use the staged procedure in the
  [GitOps guide](local-gitops.md#fresh-cluster-staged-bootstrap). Do not repair it
  with the retained Helm rollback command while Argo CD owns the Application.
- If the object-store workload is ready but bucket health fails, run
  `object_store.py initialize --execute`, then repeat object-store health.
- If control-plane database credentials are missing, deploy the database
  identities before the migration. Deployment never silently rotates an
  existing credential.
- If the migration Job has an unexpected identity, stop. Do not delete it until
  its image, arguments, ownership labels and deployment revision have been
  compared with the lock.
- If a selected content-addressed ConfigMap is missing, validate the release and
  runtime selection first. The control-plane child now selects both ConfigMaps
  from a pinned Kustomize package. For the supported M04 preparation path,
  pre-stage with `control_plane_runtime.py deploy --execute` before merging;
  inspect the child Application diff before its manual sync. If the rendered
  ConfigMap differs from a live immutable object, stop without Force or Replace.
- If an existing Secret or immutable resource has unexpected ownership or data,
  treat it as a conflict. The tooling intentionally refuses automatic adoption,
  overwrite or rotation.

The complete creation order and recovery boundaries are maintained in the
[staged GitOps guide](local-gitops.md#fresh-cluster-staged-bootstrap). The
control-plane Application may be `Degraded` during that bounded staged interval;
final GitOps health must not be accepted in that state. Stateful GitOps ownership
changes must also satisfy the unchanged-identity and rollback gates in
[ADR 0002](decisions/0002-stateful-gitops-ownership.md).

## The control-plane workload is unhealthy

Inspect the Deployment, Pods and warning events without reading Secrets:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  get deployment control-plane
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  get pods -o wide
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  get events --field-selector type=Warning `
  --sort-by=.metadata.creationTimestamp
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  logs deployment/control-plane --tail=100
```

Review logs locally for URLs, identifiers or other sensitive values before
sharing them. Common classifications are:

- `ImagePullBackOff`: validate the GHCR credential with
  `control_plane_registry.py health --execute`, then use the documented explicit
  rotation only if the credential is expired.
- `CreateContainerConfigError`: a selected Secret or ConfigMap is absent; run the
  prerequisite health checks rather than editing the Deployment.
- readiness failure: check PostgreSQL, SeaweedFS, migration and runtime inputs in
  order, then inspect the bounded application log.
- wrong immutable selection: compare the live `envFrom` ConfigMap name with
  `deployments/local-control-plane.json`; fix the Git selection or allow Argo CD
  to reconcile it rather than patching the Deployment.

Inspect the selected non-secret runtime identity with:

```powershell
# read-only
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  get deployment control-plane `
  -o jsonpath='{.spec.template.spec.containers[0].envFrom[0].configMapRef.name}'
```

## The end-to-end smoke run fails

The smoke command is **mutating** and intentionally retains its client Pod on
failure. Record the printed Pod name, then inspect that exact Pod:

```powershell
# read-only; replace the placeholder with the printed name
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  describe pod <retained-smoke-pod>
kubectl --kubeconfig $kubeconfig --context kind-perfeng-local -n perf-platform `
  logs <retained-smoke-pod>
```

Do not delete the Pod until its phase, events and logs have been inspected. A Run
that completes with five verified artifacts proves API admission, PostgreSQL
persistence, Kubernetes execution, SeaweedFS publication, normalization and
reporting for the selected runtime bundle.

## Validate immutable selections offline

Use these **read-only** checks before blaming live reconciliation:

```powershell
uv run --locked python scripts/control_plane_release.py validate
uv run --locked python scripts/control_plane_runtime.py validate
uv run --locked python scripts/infrastructure_release.py validate
uv run --locked python scripts/infrastructure_runtime.py cluster ensure
uv run --locked python scripts/infrastructure_runtime.py argocd validate
uv run --locked python scripts/gitops.py validate
```

They detect mismatched commits, infrastructure profiles, chart versions,
runtime bytes, content-addressed names and Application values without changing
the cluster. Infrastructure release validation reads the selected commit
without importing it. The two runtime previews then materialize and execute the
exact selected code without `--execute`, proving its CLI boundary without
contacting the cluster. All three require the commit to exist in the sibling
`k8s-infrastructure` object database and never switch or execute its working
tree. If the commit is missing, fetch it through the normal repository workflow
rather than changing the lock to the current branch. Never repair an identity
mismatch by editing or deleting an immutable ConfigMap in place. Create the new
reviewed identity, select it in Git and retain the previous identity for
rollback.

## M04 runtime cutover

Follow the [runtime deployment guide](kubernetes-runtime-deployment.md).
**Mutating** `task kubernetes:prepare` deliberately pauses the root and child and
stops the API/worker. A zero-replica Deployment and OutOfSync child are expected
during this window; repeated `health` cannot activate it. After successful
preparation, policy publication and merge, resume the root using **mutating**
`gitops.py bootstrap --execute`, then manually Sync the control-plane child in
Argo CD with pruning/force/replace off. Do not enable automatic sync as repair.

“Pause Application” or “not quiescent” means the migration guard refused before
Job/grant writes. Inspect active sync operations, replica counts and remaining
Pods. Retry the explicit pause after the active operation finishes; never
force-delete a stuck Pod to claim quiescence. Timeout leaves the paused state
intact. A racing UID/resourceVersion requires re-inspection, not a blind patch.

**Read-only** `scripts/execution_registry.py health --execute` verifies the three
selected execution digests and immutable pull identity in `perf-generators`.
Missing credentials need **mutating** `scripts/execution_registry.py deploy
--execute` with the package-read environment token; a foreign/mutable Secret or
denied package access needs deliberate credential recovery, not copying the
control-plane Secret or granting registry write scope. Health never rotates.

“Benchmark authority target incarnation differs” means `kube-system` UID does
not match the promoted descriptor. Requalify and promote the new incarnation;
never edit receipts or recreate the cluster to satisfy this lock. Authority
drift requires review of the four static RBAC resources in the separate
`perfeng-local-benchmark-authority` Application. Neither this workflow nor Argo
prunes dynamic Run namespaces, quota tombstones, bindings or retained evidence.

After migration 0018, do not Git-revert and restart an old worker against the
migrated database. Pause, preserve state and select forward repair or a separately
reviewed coordinated backup restore. No automatic down migration is provided.

## M04 benchmark acceptance

Follow [the acceptance guide](kubernetes-benchmark-acceptance.md). The default
`task kubernetes:acceptance` is an **offline preview**, not a healthy signal.
Confirmed execution is **mutating**: Run submissions, optional explicitly
authorized baseline transitions and one exact graceful controller Pod restart.
First finish the [runtime cutover](kubernetes-runtime-deployment.md); never use
acceptance as deployment, migration, image warming, certificate setup or repair.

Use the last printed phase/Run ID and ignored results journal to find the failed
boundary. Missing baseline consent fails before reference submission. Unknown or
rejected measurement eligibility, missing samples/CV, checksum/metadata drift,
wrong reference or INCONCLUSIVE report cannot count as a passing decision loop.
An evaluated observe-mode regression FAIL can still prove the integration path;
it does not mean performance passed.

A missed cancellation window requires inspecting and resolving the actual Run,
not injecting artificial delays or changing its workload. A stale Pod UID/RV,
foreign owner chain or unrelated active Run refuses restart. An unreleased hold,
missing cleanup proof, remaining workload/authority or open quota refuses
success even if the API says ABORTED. Preserve the two-minute drain and retained
namespace/quota barriers; never patch a reservation or force-delete to repair.

On failure/interruption, the client Pod, journal, database rows and artifacts
remain. Inspect the recorded idempotency intent before retrying an uncertain
submission; no automatic retry or whole-session resume is promised. The guide
describes explicit reuse of an exact completed source after baseline-transition
interruption. A new PASSED receipt is the full healthy signal. Source-only
verification leaves M04 live completion pending.

## Release preflight pilot fails

For SeaweedFS chart CI, inspect `seaweedfs-chart-<case>` native/peer/storage and
object observations. Main diagnostics target `seaweedfs-0`; PR child parity still
uses `bucket-ci-seaweedfs`. The peer must reach 8333 before private-port refusals
can count; a timeout, unreachable proxy or open backend fails acceptance. Do not
expose the backend or bypass HAProxy as a repair. The server-auth Secret is
independent of the deliberately denied client. Preserve both identities across
the retention restart and compare sentinel metadata/bytes and original PVC/PV.
Changed data or credentials must fail without bucket/PVC deletion or rotation.
Raw logs and Secret snapshots stay private. See the
[SeaweedFS chart boundary](release-workflows-integration.md#seaweedfs-chart-proxy-and-pvc-retention);
this does not prove production IAM policy, backup/restore or full readiness.

For PostgreSQL chart CI, inspect `postgres-chart-<case>` observation/storage/PV/
client artifacts and old/new Pod UIDs. A missing or incompatible `standard`
StorageClass is a fixture prerequisite failure, not permission to install a new
provisioner. Native rollout/socket readiness alone is insufficient: the bounded
client Job must authenticate through TCP before acceptance. A restart must retain
the bound claim and canary OID/bytes while replacing the Pod and converging a new
StatefulSet revision. Do not recreate a PVC or rotate credentials after failure.
Main diagnostics now target `postgres-0`; PR child parity still uses
`database-ci-postgres`. Logs and password/Secret snapshots remain private. See
[PostgreSQL chart retention](release-workflows-integration.md#postgresql-chart-and-pvc-retention);
this is not a backup/restore or retained-environment recovery procedure.

For selected server CI, inspect `server-<case>` native projections, worker exit
and Pod image/UID artifacts. A source-checkout failure requires the already
approved repository-read Secret and exact selected commit; do not forward that
token into kind or fall back to a copied manifest. A rollout failure can indicate
package access, missing runtime inputs or the selected server failing startup.
Do not replace the image or override its command. Stopped-server refusal requires
one HTTP main exit 2 and the exact `livez` diagnostic after the serving Pod is
absent; image/executor failures do not qualify. Correction must preserve the
Deployment/Service and create a fresh server Pod. Keep raw logs and credential
snapshots private. Operational health does not prove dependency recovery or full
release readiness. See the [server parity boundary](release-workflows-integration.md#selected-server-rollout-and-http-parity).

For runtime-grant CI, inspect `runtime-grants-<case>` Workflow, Job and worker
artifacts. Missing/excess grants require psql exit 3 and the exact privilege-matrix
error; a generic failed Job, image pull, connection or authentication error is
insufficient. Successful first/repeat/corrected attempts must preserve all table
data, ledger/OIDs, sentinel, credentials and role identities. Only fixture
administration reapplies the reviewed transaction after deliberate DELETE drift;
workers cannot repair grants. Keep raw logs, data digests and password/Secret
snapshots private. Do not run this fixture against a retained database. See the
[runtime grant matrix](release-workflows-integration.md#runtime-database-grant-parity).

For selected migrator CI, inspect `migration-image-<case>` artifacts. A failed
runtime-role case requires exit 1 **and** the matching PostgreSQL CREATE-denial
signal after a successful connection phase; generic failure is insufficient.
Worker waiting reasons identify image-pull failures, which may require existing
package-side repository Read access, never an anonymous fallback or credential
repair. Keep raw logs and Secret/hash snapshots private. Missing/gapped/checksum
mismatched ledger rows or changed OIDs/timestamps fail the fixture without repair.
Keep failed Jobs and inspect before submitting fresh evidence. Do not run this
test against retained data or interpret its success as backup/upgrade recovery.

For complete external-state CI, inspect `external-parent-<case>.workflow.json`,
the new-Job inventory and owning Job/worker exit artifacts. A bad request digest
must create no Jobs. Bucket refusal must leave database/registry unexecuted;
unsafe-role refusal must leave registry unexecuted. Truthful partial preflight
or created-Job names can survive failure and do not imply a completed parent.
The bucket v1 worker hides its underlying lookup stderr; its constant proves
only stage refusal. Bracketing 403 probes establish the test client but cannot
attribute that Job's failure to authorization rather than another lookup error.
Use safe registry classifications to distinguish package Read grant problems
from missing manifests, network or TLS errors; never upload raw logs or add an
anonymous fallback. Only fixture administration corrects its bad bucket client
or unsafe role, then submits a fresh Workflow. Preserve failed Jobs and the
original request. See the [external-state matrix](release-workflows-integration.md#complete-external-state-parent-composition).

For trusted-main maintenance composition, inspect
`maintenance-parent-<case>.workflow.json` and its safe refusal/result/projection
files. An observation refusal legitimately retains a successful nested
preflight result; only the final checkpoint must be absent. A bad request digest
must fail at `verify-identities` before `observe` runs. Expected observation
failures require main exit 2 and the exact policy/operational-response diagnostic;
Git, image, controller and timeout errors do not satisfy the case. The revoked
grant belongs only to the disposable fixture. Restore it through fixture
administration and submit a fresh Workflow; never retry successful nodes or
change retained-environment permissions to repair CI. See the
[composition matrix](release-workflows-integration.md#complete-maintenance-parent-composition).

For trusted-main full preflight acceptance, inspect the separate
`release-preflight-main.yml` artifact. `parent-request-ref.json` records the exact
run-owned temporary ref and expected commit. `parent-request-ref-cleanup.json`
with `status: absent` proves cleanup completed. Missing proof after runner loss
requires an authenticated exact-ref read; remove only that run's ref after
verifying its current SHA matches the recorded commit. A moved ref is an
ownership conflict, not permission to force repair. Do not rerun publication
blindly after an ambiguous API failure. Never upload the private Git Secret,
raw Git/API errors or fixture token files. The workflow requires main and job
contents write; PR validation intentionally cannot run this write boundary.

The staged Argo Workflows preflight is described in the
[release preflight guide](release-preflight-workflow.md). Inspect only the named
Workflow and its failing node in the disposable cluster. A Git artifact failure
usually means the exact request/selection commit is unavailable or the
namespaced `release-git-read` credential is absent or lacks repository read
access. Do not copy Argo CD's credential Secret or print a token to diagnose it.

A schema hash failure means the request delivery commit does not contain the
reviewed v1 schema. A request, lock or cluster identity mismatch is a failed
preflight, not a retryable controller glitch. Keep the failed Workflow and
compare the named commits and exact request bytes; a changed request requires
new review. The workflow has no repair, promotion or migration action. The
retained M04 cutover continues through its existing operator guide.

## Contributor GitHub delivery failures

The [automated contributor workflow](../CONTRIBUTING.md#development-workflow)
requires an authenticated `gh` executable, but offline verification does not.
If `gh` is absent from PATH, locate the installed executable and use its full
path; do not reinstall or change machine configuration implicitly. Use
`gh auth status` to inspect account/access without printing credentials. A
missing login or denied permission needs owner action, not a token pasted into
the issue, repository or command arguments.

If issue/PR creation times out, inspect existing task records with read-only
`gh issue list`, `gh issue view`, `gh pr list` or `gh pr view` before retrying.
Reuse an already-created record instead of duplicating it. Preserve a local
commit after a failed push and retry only after resolving the reported failure;
do not force-push, amend history, bypass protection or switch to a chat draft.
After PR creation, return the URL and wait for human review without further Git
commands. Delivered CI failures or owner-requested fixes use the same branch
and a structured `Fix Applied` PR comment. Never auto-merge.

## What to include in a defect report

Include:

- the exact failing command and complete redacted error;
- `git rev-parse HEAD` and `git status --short --branch`;
- Docker context name and whether `docker version` reaches the server;
- `kind get clusters` and the explicit-kubeconfig node summary;
- `kubectl ... -n argocd get applications -o wide`;
- Pod names, phases, readiness and restart counts in the failing namespace;
- bounded warning events and the name of any retained smoke Pod;
- whether retrying once changed a transient Application state.

Do not include Secret YAML, decoded Secret data, token environment variables,
database URLs, registry authorization headers, the Argo CD initial admin
password, or logs that have not been reviewed and redacted.

## Maintenance contract

This runbook owns the diagnostic order, expected healthy signals and routing to
recovery documentation. The focused guides own deployment details, credential
rotation, persistence guarantees and destructive procedures.

A pull request must update this runbook when it changes a supported operational
command, prerequisite, healthy signal, failure classification or recovery path.
New diagnostics default to read-only, use the dedicated kubeconfig and identify
their exact target. New mutations and destructive operations must be labeled and
linked to the document that defines their ownership and rollback boundary.

## Staged verifier package upgrade or retirement failure

For `release-workflows-kind.yml` failures, use the
[isolated CI fixture guide](release-workflows-integration.md). The fixture
requires GitHub-hosted Linux, the default local Docker socket and no existing
kind cluster; refusal on a local/self-hosted host is expected. Do not redirect
it to the retained environment or supply an environment credential. A Git
artifact failure is a fixture failure, not a successful negative assertion:
negative cases must prove the verifier itself exited with code 2. Inspect only
the case Workflow/checkpoint JSON and Pod summary. Correct the test or its
owning template/grant through review, then use a fresh CI run; do not broaden
production permissions or delete retained resources to fix a fixture.

The ledger phase uses generated fixture records and the production native
create/get templates. A failed Workflow alone does not prove an expected
conflict: its bounded resource-Pod diagnostic must report AlreadyExists and the
stored winner must remain unchanged. Inspect `ledger-*.workflow.json`, record
snapshots and filtered conflict/immutability diagnostics in the same artifact.
Missing-read defaults, unexpected create authority, accepted immutable patches
or changed/deleted records are failed assertions, not permission to repair a
retained ledger. The fixture deletes its labelled Workflows with foreground
propagation to test retention. After those assertions, its administrator deletes
and recreates only the test receipt to verify dangling/replaced-UID refusals;
the adoption identity cannot repair it. All fixture records disappear with the
owned disposable cluster.

For interrupted-recovery cases, inspect the suspension-barrier and terminated
Workflow artifacts plus `ledger-recovery-*.json`. The receipt create must have
succeeded before termination, and a fresh observation must preserve its bytes
and first-attempt provenance. A missing barrier, timeout or unrelated policy
exception fails the test. The runner's pure-planner reports use synthetic
readiness/root status; they are not adoption results or packet-level API-response
loss evidence. Do not copy these test-only recovery helpers into an operator path.

For native receipt response-loss acceptance, inspect
`ledger-response-loss.outcome.json`, its failed Workflow/worker projections and
the fresh direct read/retry artifacts. The CI-only TLS proxy must confirm a
matching upstream 201 and UID but forward no successful response bytes. A TLS,
image, mount, authorization or pre-commit failure is not sufficient proof. A
direct retry must report AlreadyExists while preserving the entire receipt;
the pure planner must request only the missing pointer. Coordinator interruption
uses an independent second receipt and remains a separate check. No GitHub token
is available to this fixture. Do not print private proxy configuration/logs or
TLS keys, disable verification, widen grants or reset ledger records to pass it.
See [the fixture boundary](release-workflows-integration.md#native-receipt-response-loss).
This does not establish complete-parent ambiguous-outcome recovery.

An empty writer worker list means Pod creation did not complete; inspect the
saved writer Workflow's `status.message` before diagnosing the proxy. In this
resource fixture, the PodSpec patch must include both the `fault` ConfigMap
volume and its mount. Argo's earlier volume-reference selection cannot discover
a mount introduced by a later patch. Offline Workflow lint alone does not prove
the generated Pod passes Kubernetes admission.
Argo's resource executor always appends `-o json`; do not use kubectl `--raw`,
which rejects that output flag before sending the create. The fixture serves
fixed discovery locally and uses normal create with no client OpenAPI lookup;
the exact object still undergoes Kubernetes API validation. The real loopback
kubectl test covers discovery, accepted create options and withheld response.
Its first `version --client` invocation has a separate 120-second startup budget;
flag validation and response-loss stages keep 30-second budgets, close stdin and
never retry the create. Timeout diagnostics name the stage and upstream-create
count without dumping client output. A startup or flag-validation timeout is not
TLS/response-loss evidence. The Windows CI delay was not reproduced locally;
its underlying runner/shim stall remains unproven. Do not add a kubectl request
timeout query to this exact-route proxy test: it changes the selected POST URL.

For database parity failures, inspect `database-*.workflow.json`, the named Job
JSON, the non-secret role summary and filtered refusal diagnostics. An expected
unsafe-role/password refusal must have a failed Job and `psql` exit 1; a pending
Pod, mount/image failure, timeout or controller error cannot count. Compare the
fresh correction case rather than resuming successful nodes. The fixture admin
intentionally changes only disposable role state and corrects it explicitly;
the Job must not repair unsafe attributes or rotate credentials. Never upload
its private Secret snapshots, password hashes or SQL reset files. This child
test does not authorize running the host fixture against retained PostgreSQL
or replacing the supported preparation path.

For bucket parity, inspect `bucket-*.workflow.json`, Job JSON, the fixed probe
summary, filtered refusal and bucket/object metadata. The observer must prove
404 with approved credentials and 403 with the denied pair before the native
refusal case; a network error cannot substitute for those outcomes. The owning
Job must exit 1 with the non-absence diagnostic and leave the bucket absent.
Only the fixture administrator replaces the deliberately wrong initial client
Secret; the new UID is expected, while good Secrets must remain unchanged
through first/repeat preparation. Never upload private credentials, profile
files, Secret snapshots or raw auth errors. The direct internal S3 listener is
not proof of production HAProxy, PVC, network isolation or complete release
acceptance, and this helper must not run against retained SeaweedFS.

If bucket creation succeeds but sentinel `PutObject` returns `InternalError`,
check the fixture's volume capacity before treating it as an AWS retry problem.
The pinned SeaweedFS grows seven single-copy volumes per collection; metadata
and artifact allocation share the cap. The fixture must retain the production
chart's sixteen-volume setting. A failed PUT saves only allowlisted allocation
signals/counts in `bucket-allocation.txt`, with bounded log input and the original
failure status preserved. An empty diagnostic file does not establish the cause;
other S3 failures still require investigation. Do not upload raw server logs or
relax the sentinel write/retention assertions.

For registry CI, inspect `registry-*.workflow.json`, Job JSON, digest evidence
and `registry-*.exit-codes.json` plus per-worker signal categories. Categories
cover authorization (including lowercase denials), missing manifests, TLS,
network and command errors. `unclassified` means the bounded private log did
not match a known category; it does not establish a package-access problem.
Only constant category names are published, never raw error context.
A first-access authorization signal requires checking
the package's **Manage Actions access** repository **Read** grant as well as the
job's `packages: read`; a successful Git checkout does not establish package
authorization. Confirm exact selected references and connectivity too. The
fixture does not alter package settings or host credentials. Do not upload raw
worker logs, token files or encoded Docker-config Secret snapshots. Denial must
come from the owning Job with a failed kube-burner worker and a specific auth
signal, not arbitrary image/controller/network failure. Only fixture admin
corrects the deliberately invalid test Secret before a fresh Workflow. Digest
access is not proof of layer downloads or complete manifest-byte validation.

For readiness CI, inspect `readiness-*.workflow.json`, success result JSON,
constant refusal lines and per-case endpoint counts. Wrong-image refusal must
precede all HTTP requests. A 503 stops at readyz; redirects and invalid metrics
stop at metrics, with zero redirect-target requests. The invalid cases require
the verifier's exit 2 and its exact policy/endpoint refusal, so artifact checkout,
image, executor and timeout failures must be diagnosed independently. Synthetic
observations and the fixture HTTP peer do not establish actual workload health.
Only fixture administration changes responder mode; correct the prerequisite
and use a new Workflow. Do not upload raw worker logs or HTTP bodies or relax
the absence-of-result checks to make a negative case pass. On failure Argo can
retain an output parameter's `valueFrom` declaration without publishing a
`value`. That declaration is not result evidence. The refusal gate rejects any
materialized `result.value`, including empty/null values, while retaining the
exit-code, exact-refusal and probe-count requirements.

For native observer CI, inspect `observer-*.workflow.json`, observation JSON and
constant refusal/pending markers. Degraded status must fail at the Application
resource's evaluated condition. Missing read access must produce Forbidden for
`release-readiness` on `applications`; an arbitrary failed Pod is insufficient.
Only fixture administration restores its disposable RoleBinding/status. The
paused Deployment case requires a logged false condition and a still-running
Workflow before scaling up; missing evidence fails after a bounded wait. Check
executor startup and native condition logs privately rather than shortening
the assertions or scaling early. Preserve resource UIDs and use a fresh Workflow
for refused cases. These synthetic workloads/status do not prove real services
healthy, and raw executor logs/API snapshots must remain outside artifacts.

For Git preflight CI, inspect `preflight-*.workflow.json`, success result JSON
and constant refusal files. Positive child results deliberately bind the
all-zero fixture UID, while `actual-cluster-uid` must match the native namespace
read. The complete production parent must refuse this same request for target
UID mismatch. Digest refusal belongs to `verify-identities`; unsafe-path refusal
belongs to `validate-schema`. Each requires main exit 2, the exact diagnostic and
no materialized result. Diagnose artifact authentication, schema image or
executor failures independently; do not count them as expected refusal. Keep
raw worker logs and Git Secret snapshots private. Restore the original parameter
file and submit a fresh Workflow for correction; never change the fixture to a
live UID or use child evidence as deployment approval.

The current staged entrypoints use `perfeng-release-verifiers-v3`; data schemas
remain v1. If a Pod cannot mount the package, inspect the staged Application
revision and the immutable ConfigMap in `argo-workflows`. Both Python files
must be present before syncing the v2 child templates and parents. Do not edit
a published ConfigMap or copy code into a template. See the
[package upgrade and rollback procedure](release-verifier-package.md).

For adoption authority CI, inspect `adoption-authority.tsv`: a complete proof
ends with the tab-separated `suite verified` row. A refusal must be Kubernetes
`Forbidden` for the exact impersonated ServiceAccount, operation, API group and
namespace. Timeout, NotFound, schema/admission rejection and an unexpected grant
are failures. Raw command output stays in the private fixture directory; do not
upload it. Check the selected Role/RoleBindings and object identities privately.
Create probes use manifests and the generic client path. Collection authorization
does not name the object; a batch resource can appear as `jobs.batch`. Do not add
per-generator or named-object prefixes to the checker. Inspect the six match
columns (marker, subject, verb, resource, group, namespace) and exit status in the
safe TSV. A zero indicates exactly which part failed. Independent probes continue
only as dry-run/name-only observations; the suite blocks downstream work on any
mismatch. The early smoke report detects create/list failures before Argo setup.
Do not widen permissions or remove `--dry-run=server` to obtain a pass. The helper
checks unchanged selected ledger snapshots and cluster UID; changed records need
independent investigation. Exec is review-only and never connects. Explicit
groups document the effective identity; Kubernetes already defaults those groups
for ServiceAccount impersonation. These checks do not prove token authentication.

For any disposable acceptance failure, inspect `stages.tsv` first. A stage with
`started` and no `succeeded`, followed by `failed`, identifies the owning boundary.
Use its safe case report; do not upload private diagnostics to obtain context.
`run-outcome.json` must bind the intended commit and a nonempty cluster UID, with
zero overall/cleanup exits. Two qualification passes require the same commit and
different UIDs. A passing parser unit test, a rerun of another commit or a run with
failed teardown does not qualify. The controller objects are collected only after
controller installation, so an early smoke failure does not request missing CRDs.

A source-retirement guard failure requires matching Git revision/content hashes
and active replacements in `deployments/release-workflow-retirements.json`.
Changing an existing published file or retirement entry is refused. Make a new
version; retain old installed objects and in-flight Workflows. A missing v1
source path in the current checkout does not authorize deleting that resource
or replaying completed nodes.


## Complete adoption receipt response-loss acceptance

For `root changed during adoption` from older v2 planners, compare retained
root samples before treating the refusal as semantic drift. Run 40227060836's
initial and receipt-only legs differed only in generation/resourceVersion and
reconciledAt from normal Argo CD refresh. Use the reviewed v3 package under
[ADR 0036](decisions/0036-tolerate-root-reconciliation-bookkeeping.md) and a fresh
Workflow; never patch a published verifier or edit ledger records to recover.
v3 validates both root samples and tolerates only those three paths. Any other
change still requires inspection. A root that becomes unhealthy, out of sync,
foreign or otherwise invalid now refuses at that validation before comparison.
Do not pause reconciliation or increase deadlines to avoid normal refresh.
Offline replay does not prove the full live adoption chain has passed.

If any create-fault scenario stops before resume, inspect
`adoption-<scenario>.barrier.json`. `terminal-parent` means the parent ended
before the required suspension; use the reported step, phase, native container
exit code and fixed failure categories to identify the failing prerequisite.
`api-read-failed` means the Workflow read failed; `barrier-timeout` means the
barrier was not positively observed within the unchanged 240-second bound.
`invalid-observation` refuses inconsistent evidence. `podsObserved: false`
means Pod diagnostics were unavailable, not that no worker ran. Unknown or
truncated entries are incomplete diagnostics. Raw messages and API responses
remain private, and categories alone do not prove authorization or startup
causes. Confirm the cause before changing permissions or deadlines. A missing
barrier report is a harness/reporting failure, never a successful fault case.

The separate `pointer-response-loss` scenario faults the later pointer write.
See [pointer response loss](release-workflows-integration.md#complete-parent-pointer-response-loss).
Require the typed pointer event, directly observed pointer data/receipt UID and
an exit-1 pointer worker after successful receipt readback and pointer planning.
Its fresh `pointer-recovered` submission must skip both creates and preserve
complete snapshots of both records. A receipt event, missing pointer, changed
receipt UID, timeout or successful downstream worker is a failure. Do not delete
the pointer to make the fixture resemble receipt-only recovery. Full-parent live
proof remains post-merge in the isolated main-only matrix leg.

If the helper refuses before setup, inspect its hosted-runner/scenario/ownership
guard and caller credential isolation. Trusted-main intentionally needs its
runner token for temporary-ref cleanup; the response helper must run in the
dedicated token-cleared subshell, with its inherited EXIT trap disabled. Do not
unset the caller's credential or weaken the helper guard. Both ordinary failure
and explicit exit must leave exactly one parent cleanup owner with its original
credential. Shared bookkeeping is recorded only after both fault and recovery
cases succeed.

The trusted-main `response-loss` leg is isolated from `initial` and `receipt-only`.
It uses a CI-only parent copy with a pre-create suspension and scoped receipt
transport; its recovering submission uses the unchanged production parent.
See [the scenario](release-workflows-integration.md#complete-parent-receipt-response-loss)
and [ADR 0031](decisions/0031-exercise-complete-adoption-response-loss.md).

A failed Workflow alone is not the expected healthy fault signal. Require
`adoption-response-loss.outcome.json`, a failed `create-receipt` worker after
successful readiness/plan, a single exact committed-response event, the matching
receipt UID/content and an absent pointer. Missing events, API denial, timeout,
TLS failure or downstream workers must fail acceptance. Configuration, raw proxy
events and worker diagnostics remain private in the owned runner state.

If barrier setup fails, inspect the private barrier Workflow and planner boundary
before changing the proxy. If fault proof fails, check the failed node, worker
projection and private event against the direct API read; do not classify generic
network failure as a committed write. Recovery must submit a fresh production
parent and preserve the full original receipt. It must skip receipt creation,
create only the pointer and pass existing retention/refusal checks. Never delete
or replace ledger history to make this case pass. Owned-cluster teardown removes
the fixture; retained environments have no authorization to use this test.

Offline verification does not run this main-only parent scenario. Its live proof
remains pending until the post-merge trusted-main leg succeeds.


## Complete adoption initial receipt-create conflict acceptance

The sibling `pointer-create-conflict` scenario pauses only after real receipt
creation/readback and pointer planning. Its fixture writer creates the exact
planned pointer; the unchanged native pointer create must report AlreadyExists
with exit 64, manifest-path context and the exact pointer name. Inspect
`adoption-pointer-create-conflict.outcome.json`. Preserve both records: a fresh
unchanged `pointer-recovered` parent must skip both creates and retain complete
bytes, UIDs and receipt provenance. Missing readiness, stale API snapshots,
wrong-object/Forbidden/timeout errors or proxy events cannot qualify. No proxy
should exist in either create-conflict case. Do not delete a winning pointer to
retry. See [pointer conflict acceptance](release-workflows-integration.md#complete-parent-initial-pointer-create-conflict).

The main-only `receipt-create-conflict` leg must reach the real pre-receipt
barrier, then create exactly the planned receipt through the owned fixture
writer before resumption. No response proxy should exist. The pinned native
Argo worker reports AlreadyExists with exit 64 and the context
`error when creating "/tmp/manifest.yaml"`, naming the selected ConfigMap.
Exit 1, timeout, Forbidden or an error for another object does not qualify.
Inspect the safe `adoption-receipt-create-conflict.outcome.json` and privately
retained worker evidence. Preserve the winning receipt; do not delete it to
retry. The fresh unchanged `recovered` parent must skip receipt creation and
create only the missing pointer, retaining the full original receipt snapshot.
See [initial receipt conflict acceptance](release-workflows-integration.md#complete-parent-initial-receipt-create-conflict).

A failure in an earlier prerequisite, including the selected migrator Job,
means this scenario has not run. Do not mark adoption qualified from its offline
tests or mask the prerequisite by relaxing deadlines or refusal checks.


## Selected migrator deadline and missing worker evidence

If trusted-main stops at `Selected migration image: runtime-role` with
`DeadlineExceeded`, an empty final worker list does not show whether the Pod
pulled its image, scheduled, started or reached PostgreSQL. The Job controller
may delete its Pod before the native Argo wait returns. Inspect the new
`migration-image-runtime-role.startup.json` alongside the Job and Workflow.

`ErrImagePull`/`ImagePullBackOff` with `authorization` warrants checking the exact
selected private package's Actions Read grant; `manifest-missing`, `tls` and
`network` are distinct diagnostic hints. `Unschedulable` indicates scheduling;
`CreateContainerConfigError` indicates startup configuration. Running/terminated
states and exit codes distinguish executable startup from Pending failures.
No raw vendor message or credential is retained in the public report. `Other`,
a failed watch, incomplete stream or no owned Pod observation means the evidence
is limited; do not infer authorization, execution or a successful refusal.

The report is diagnostic only. A timeout must not qualify as the expected
PostgreSQL CREATE denial, and increasing a deadline or changing credentials is
not an implicit repair. Inspect the classified boundary, establish the cause,
then make a scoped correction. Main run 40095134325 proves the previous
post-wait evidence-loss defect; it does not establish the migrator timeout cause.
See [selected migration-image parity](release-workflows-integration.md#selected-migration-image-parity).


## Early release-image prerequisite failures

Inspect the `registry-prerequisite` and `kubelet-image-prerequisite` stages
before diagnosing a later migration timeout. The first uses the same Docker
auth JSON that feeds the Kubernetes pull Secret and runs before kind creation.
The second proves that kind can pull with that data before controllers or the
database are installed. Safe artifacts are `selected-image-prerequisite.json`
and `selected-image-kubelet-prerequisite.json`; raw vendor errors and credentials
are withheld.

- `authorization`: verify effective `Packages: read` and the exact package's
  **Manage Actions access** entry for `perfeng-environment`. Read or Admin is
  sufficient. If Admin is already present, investigate the job token/config;
  do not increase privileges or substitute a personal token.
- `manifest-missing`: restore the locked immutable artifact or review a new
  selection; do not change to a mutable tag.
- `tls`, `network`, `timeout`: inspect certificate trust, runner DNS/connectivity
  and GHCR availability. Keep TLS verification enabled and native migration
  deadlines unchanged.
- `platform`: publish/select the reviewed `linux/amd64` variant.
- Runner pull passes, kubelet pull fails: inspect the pull Secret's data, type,
  namespace and Pod reference, plus node registry connectivity. This identifies
  a Kubernetes delivery boundary; another package Admin grant is not the next
  action. The failure occurs before expensive parent setup.

If the report instead has `exitCode: 0`, `imagePulled: false` and no classified
pull error, distinguish a premature observer result from registry denial. Run
40188553698 exposed this combination: kubectl 1.35's value-less JSONPath wait
accepted an existing empty image ID. The fixture now filters empty strings
before wait can succeed and still requires a fresh non-empty single-container
observation. Changing package grants or accepting exit 0 alone cannot repair or
qualify that boundary. The native-client regression covers empty-to-populated
watch events without registry or cluster access; live acceptance stays separate.

The probe has an empty database URL and no credentials/API token. Its process
may exit unsuccessfully; only the kubelet image ID is the prerequisite signal.
A passing pull never qualifies migration or adoption. Rerun the full workflow
after correcting the classified prerequisite. Attempt 1 of run 40107957676 showed
scheduled Pods followed by authorization-classified ErrImagePull/ImagePullBackOff
in all five legs. After the owner added Admin Actions access, attempt 2's initial
leg passed all three migration parity cases; it failed later at the runtime SQL
probe with exit 2. The rerun did not repeat that original image-pull failure.

`[PREREQUISITE:database-connection]` identifies psql exit 2 (connection failure
or loss), distinct from the expected SQL policy refusal at exit 3. Inspect the
runtime-url Secret delivery and libpq connection configuration, PostgreSQL
endpoint, authentication/TLS and availability. A passed migration is not proof
that this separate psql worker received usable connection settings. Do not
change registry roles or accept the connection failure as policy refusal.
`runtime-grants-<case>.failure.txt` retains only fixed safe guidance.

The original worker supplied a complete URI as `PGDATABASE`. A native-client
reproduction with the exact pinned image established that libpq treated it as a
literal database name and selected a local socket. The corrected worker
validates the existing canonical URI and exports separate `PGHOST`, `PGPORT`,
`PGDATABASE`, `PGUSER`, `PGPASSWORD` and `PGSSLMODE` values before executing psql.
Its password stays in the environment, never command arguments or diagnostics.
`[PREREQUISITE:database-input]` (worker exit 64) means that validation failed
before any SQL execution: inspect the Secret mapping against the reviewed local
URI contract and 43-character URL-safe password. Do not print the URI or rotate
credentials as an implicit repair. The Secret schema and SQL refusal criteria
are unchanged. A later exit 2 still requires connection diagnosis; neither this
mapping fix nor isolated endpoint-selection evidence proves live grants.

### Trusted-main maintenance pause or recovery failure

In the `initial` leg, inspect `stages.tsv` for `maintenance-pause` or
`maintenance-recovery`, then the saved pause Workflow, worker identity/status
projection and checkpoint. A pause failure can leave completed writes in place;
the fixture does not silently resume. Recovery starts only after successful
pause, quiescence and retention checks, using explicit child Argo CD sync.
Unexpected changes to ledger, Secrets, configuration, foundation Pods, storage,
data, workload specs or Application identities refuse the recovery baseline.
Only a different control-plane Pod under the same ReplicaSet is allowed, with
its generated ServiceAccount volume name normalized for spec comparison.
Original evidence is retained; never repair acceptance by rewriting it or
ignoring all Pod differences. These checks are disposable CI only; use the
[maintenance guide](release-maintenance-pause.md) for operational inspection.

`maintenance-missing-receipt` and `maintenance-replaced-receipt` are expected
baseline refusals in the initial job's existing receipt fault windows. Success
requires fresh preflight/readiness, exactly the baseline worker exiting 2, no
pause/checkpoint execution, unchanged retained state and a validated refusal
outcome. The production worker intentionally redacts policy details; CI binds
that fixed message to the exact native ledger observations through the unchanged
policy. A timeout, native Forbidden, malformed input, unrelated worker failure
or missing result file is not acceptance. Never rewrite the pointer, delete
another receipt, weaken retention or run recovery to make these cases pass.

The same redacted-message contract applies to adoption receipt/request refusal
gates. The old harness incorrectly required detailed policy reasons in worker
logs, which the runtime deliberately suppresses; synthetic logs concealed the
mismatch. The corrected matcher requires the exact published fixed line only
after exact-cause policy replay, retaining the sole planner exit 2 and all
no-write/no-result checks. Do not expose policy details in production logs or
accept arbitrary exit-2 failures as a workaround.

`maintenance-changed-request` and `maintenance-upgrade-request` must refuse at
the baseline planner after fresh candidate preflight/readiness, while the
accepted baseline remains healthy and intact. Compare the reviewed candidate
parameters/bytes and validated fixed outcome; raw request/API/log snapshots stay
private. A changed selection/cluster, other envelope edits, unexpected candidate
receipt, unrelated worker error, pause write or checkpoint is a failed gate.
These cases reuse the existing Git fixtures before receipt deletion and perform
no repair or recovery. Never publish another request, rewrite the pointer or
accept a timeout as the expected refusal.

### Disposable maintenance stale-guard refusal

For `maintenance-stale-root`, `maintenance-stale-child` or
`maintenance-stale-deployment`, inspect the safe barrier report first. An API
read failure, terminal parent or timeout before suspension is a failed setup,
not stale-patch acceptance. Do not resume a parent whose marker restoration,
UID/spec/generation or retained-state checks failed. Native JSON Patch rejection
uses the API's generic Invalid message and pinned executor exit 64; proof of
cause also requires the unchanged patch and captured equal UID/spec but stale
resourceVersion. Arbitrary exit failures or permission errors do not qualify.
Raw marker/API/log evidence stays private. Deployment uses only a top-level
fixture label: an annotation would increment generation and may propagate to
the ReplicaSet. Never change selectors, Pod templates, ledger records or
production recovery to make this fixture pass. Failed setup may leave its
marker in the owned disposable cluster until the existing cluster cleanup;
there is no automatic repair or recovery.

### Maintenance API authority audit

`maintenance-authority` must finish before real maintenance submission. Inspect
`maintenance-authority.tsv`: only a final `suite verified` passes. Allowed named
native patches must succeed with server dry-run; inspect the unchanged staged
maintenance RoleBindings and pinned resource ownership if one fails. An Invalid
stale-version response can cause at most three fresh read-only attempts only
when UID/spec/ownership remain equal and resourceVersion changed. Standalone
kubectl renders this as `The request is invalid: ...`; it does not use the
usual `Error from server (Invalid)` wrapper. Transport,
permission, unrelated Invalid or malformed positive responses cannot qualify.

A negative probe must prove Forbidden for the exact subject and API operation.
Unexpected allow requires reviewing the ServiceAccount's effective bindings;
never grant broader rights to make a probe pass. A GET/NotFound/client-validation
failure does not prove PATCH/DELETE denial. Manifest handles and the supported
`delete -o name` command keep probes at the intended API boundary. Raw
API/diagnostic files stay private. Every mutation probe includes server dry-run,
so a permission regression still must not persist a change. Do not remove that
flag, rotate credentials, connect with pod exec, repair records or advance
maintenance after an audit/retention failure. Pod exec evidence is a review only;
RBAC still permits arbitrary patch fields on the three allowed resources.

## Disposable maintenance partial-pause acceptance

For `maintenance-partial-root` or `maintenance-partial-child`, inspect the safe
barrier report and `stages.tsv` first. An outcome exists only after the real
native patch completed, its CI-only post-patch barrier was explicitly stopped,
and fresh retention passed. Raw Workflow, worker, stop/wait logs and API/Secret
snapshots remain private and must not be uploaded unredacted.

The expected terminal state is the same Workflow UID with `spec.shutdown: Stop`,
phase `Failed` and `Stopped with strategy 'Stop'` on the Workflow and suspension.
Preceding native workers must have succeeded as `release-maintenance-pause`;
later guard/write/checkpoint execution or a successful result is a failure.
Timeouts, `Terminate`, image pull errors and permission errors are not substitutes
for this evidence. Use safe barrier diagnostics for startup/dependency failures;
private stop/wait logs are for privileged diagnosis only.

A missing outcome or failed retention check must stop the job. Do not resume,
retry successful nodes, sync the child, repair workloads, change permissions,
rewrite ledger records or advance the recovered baseline to make acceptance pass.
Both Applications were already disabled, so these cases should replace no
workload Pod. Existing owned disposable-cluster cleanup remains the only cleanup.
See [maintenance pause](release-maintenance-pause.md) and
[ADR 0040](decisions/0040-exercise-maintenance-partial-pause.md). No local operation
or operational recovery is authorized by this fixture.

## Disposable Deployment interruption and recovery

For `maintenance-partial-deployment`, require the exact suspended-parent Stop
before investigating the 120-second read-only settlement phase. A changed UID,
deleting Deployment, changed desired replicas, API failure, stale generation or
remaining matching Pod must prevent checkpoint/recovery. Do not force-delete a
Pod or scale again. Raw PodList evidence must retain its resourceVersion and no
pagination; a generic kubectl List without that evidence cannot prove absence.

For `maintenance-partial-deployment-checkpoint`, require a distinct unchanged
read-only parent using the same pinned inputs and successful read-only workers.
A checkpoint from the interrupted parent, an old Workflow, wrong identity, worker
failure or mismatched cluster/resources cannot authorize the fixture sync. Keep
native and independent fixture resourceVersions separate; their sampling times
differ. Do not change native verifier guards to compare those separate reads.

`maintenance-partial-deployment-recovery` may sync only after verified Stop,
paused retention and independent checkpoint. A failed sync, HTTP/native check,
root convergence, same-ReplicaSet replacement or retained-state check must stop
before baseline advancement. Original evidence and both ledger records remain
unchanged. Later receipt faults may use only the fixed checked recovery snapshot;
never accept an arbitrary baseline path. Existing owned-cluster cleanup is the
only automatic cleanup. Raw logs/API/Secret snapshots stay private; public
artifacts contain verified outcomes and the validated independent checkpoint.
See [ADR 0041](decisions/0041-exercise-deployment-pause-interruption.md). This is
CI acceptance and adds no local or production recovery command.

### Maintenance lingering-Pod acceptance fails

Inspect the `maintenance-lingering-pod` stage and validated
`maintenance-lingering.refusal.json` report. Raw Workflow, Pod, log and API/Secret
snapshots remain private. A valid refusal requires the real preflight, only the
quiescence worker exiting 2, its generic policy-refusal line, no checkpoint
result, and independent policy replay proving that the sole gated fixture Pod
blocks otherwise valid paused snapshots. A timeout, missing worker, operational
error, changed Pod UID/spec/gate or retention mismatch is a failure, not proof.

Do not remove scheduling gates, force-delete Pods, retry successful nodes or run
manual recovery to make this test pass. Conditional cleanup is generated only
after valid refusal and uses the captured Pod UID/resourceVersion. A stale DELETE
stops; owned disposable-cluster teardown is the failure cleanup boundary. After
successful deletion, absence and zero replicas must precede a fresh unchanged
checkpoint; only its verified success permits the existing checked recovery.
This fixture does not authorize any local cluster action. See
[ADR 0042](decisions/0042-exercise-lingering-pod-checkpoint-refusal.md).

### Maintenance write-denial acceptance fails

Inspect `maintenance-write-denied-root`, `maintenance-write-denied-child` or
`maintenance-write-denied-deployment` stages and validated refusal/outcome reports.
Raw Role/API/log/Workflow/worker and Secret snapshots remain private. A valid case
requires original staged rules, exactly the intended effective PATCH denial with
all GET/other PATCH grants intact, real complete-parent readiness/guard success,
and sole selected native PATCH exit 64 with exact Forbidden subject/verb/group/
resource/namespace/name. GET denial, API timeout, stale patch, unrelated worker
failure, unexpected success or retention mismatch is a failing test.

Do not restore Roles manually, broaden grants, retry native writes, replace Pods
or sync workloads to make the case pass. Only verified refusal/retention emits a
restoration patch testing captured UID, fresh resourceVersion and narrowed rules.
A stale restoration or failed effective-grant check stops before the next case.
There is no EXIT repair; owned disposable-cluster teardown is the failure cleanup
boundary. This fault authorizes no local cluster or production permission change.
See [ADR 0043](decisions/0043-exercise-maintenance-write-denial.md).

### PATCH proxy rejects kubectl's preliminary Deployment GET

If the real-client regression records zero upstream writes and reports
`Forbidden ... (get deployments.apps control-plane)`, inspect the fixture's
named-GET query boundary. The copied `--request-timeout=10s` flag makes kubectl
send `?timeout=10s` on the GET and PATCH. The proxy accepts exactly that value,
preserves it upstream, and rejects duplicates, other timeout values and unknown
query keys. A route comparison against the query-free URL causes a local proxy
403 before any PATCH. The regression requires that the bounded GET reaches the
API and exactly one guarded PATCH commits before its response is dropped. Keep
the existing RBAC, token, TLS and payload checks intact. See
[ADR 0044](decisions/0044-exercise-maintenance-patch-response-loss.md).

### Maintenance PATCH response-loss acceptance fails

Inspect stages `maintenance-response-prepare`, `maintenance-response-checkpoint`
and `maintenance-response-recovery` and their validated fixed reports. Raw
Workflow, API, worker and proxy diagnostics remain private. Barrier setup must
prove real readiness/adoption planning and a fresh Deployment matching the real
guard. A stale version or setup failure stops before resume; do not refresh the
payload or retry the write to make acceptance pass.

After resume, require exactly one proxy event proving the expected PATCH committed
with HTTP 200 and no successful response forwarded, plus the native failed worker
and unchanged downstream/checkpoint boundary. TLS errors, forbidden/invalid API
responses, timeouts, wrong committed spec/UID/generation, repeat forwarding or a
failed Workflow without that event are failures, not evidence of ambiguity.

Do not retry the parent, resume successful nodes, repair records or sync workloads
to bypass missing proof. Fresh direct settled observations and retained data must
pass, then a new unchanged read-only checkpoint must succeed before the explicit
checked recovery. Later baseline advancement also requires unchanged adoption
records/result and exactly one approved same-ReplicaSet replacement. No EXIT
recovery is installed; owned disposable-cluster teardown is the failure cleanup
boundary. No local or production action is authorized. See
[ADR 0044](decisions/0044-exercise-maintenance-patch-response-loss.md).

### Root/child Application response-loss acceptance fails

Inspect `maintenance-root-response` or `maintenance-child-response` and their
validated barrier/outcome reports. A valid case requires real readiness/adoption
planning and a fresh selected guard before proxy setup, exactly one matching
HTTP 200 commit event, sole selected native PATCH failure, no later writes or
checkpoint, and unchanged running workloads/Pods/ledger/data. The Applications
are already disabled, so an unchanged resourceVersion can be correct. Wrong
UID/spec/ownership, an enabled baseline, changed child generation, wrong route,
API/TLS refusal or an arbitrary failed Workflow cannot qualify.

Each proxy instance accepts only its configured named Application. Do not widen
routes, alter native guards, retry a stale payload, resume successful nodes or
sync workloads to make acceptance pass. Setup collisions fail without replacement.
Fresh unchanged adoption is allowed only after verified fault/retention and must
reuse both records and the original adoption result. There is no automatic repair,
Pod replacement or baseline advancement. Keep raw diagnostics private; failure
cleanup remains owned disposable-cluster teardown. This authorizes no local or
production action. See
[ADR 0045](decisions/0045-exercise-application-patch-response-loss.md).

### Child transition acceptance fails or root becomes OutOfSync

During `maintenance-child-transition`, the disposable fixture deliberately enables
only the selected healthy control-plane child's auto-sync, with prune/selfHeal
false. Root remains disabled and may compare the child as OutOfSync while its
live spec differs from Git. The published child guard/PATCH must restore the
original disabled spec, with two generation increments across the case. Root
must then converge and strict running-state/data retention must pass before
fresh unchanged adoption. Inspect the private enable response, enabled read,
Workflow/workers and before/after snapshots; only validated `.outcome.json` is
published. Wrong identity/spec, active sync, native failure, stale guards,
timeout or changed Pods/ledger/data cannot qualify. Do not retry writes, enable
root, manually sync, repair, or advance the baseline to pass acceptance. A failed
case stops for owned disposable-cluster teardown, even if the child remains
enabled. This is CI leaf proof, not a supported local recovery command or full
parent transition acceptance. See
[ADR 0046](decisions/0046-exercise-child-auto-sync-transition.md).

### Maintenance unadopted-baseline acceptance fails

`maintenance-unadopted` runs before first adoption in the trusted-main initial
job, while both ledger records are naturally absent. The expected failure is
only the complete parent's `baseline` finish phase, after successful real
readiness and native ledger/root reads. An API authorization error, timeout,
missing readiness, non-finish phase, successful maintenance, later pause worker
or changed state cannot qualify. Check the private Workflow, worker projection,
fixed refusal log and before/after snapshots; the bounded outcome is published
only after exact refusal and retained-state verification. Do not create records,
retry maintenance, bypass retention or run recovery to make this gate pass.
First adoption remains blocked until the gate succeeds; existing disposable
cleanup owns teardown. This adds no supported local operation. See
[ADR 0047](decisions/0047-refuse-maintenance-before-initial-adoption.md).

### Receipt-only maintenance refusal blocks adoption recovery

In stage `maintenance-receipt-only`, a valid stored receipt with no current pointer
must fail only baseline finish, specifically for the missing stored pointer.
The parent must first pass real readiness and native root/ledger observations.
The receipt must exactly match the original partial-adoption snapshot, including
UID, bytes and provenance; the pointer remains absent. Invalid receipt, missing
root evidence, early worker failure, timeout or an empty ledger cannot substitute
for the expected refusal. Inspect private Workflow/worker/log and before/after
snapshots. The bounded outcome is published only after receipt and running/
persistent-state retention pass. The approved quota increase and fresh adoption
retry remain blocked until then. Do not rewrite records, bypass the gate, retry
maintenance or sync/recover workloads to pass acceptance. Existing disposable
cleanup owns teardown. No local action is authorized. See
[ADR 0048](decisions/0048-refuse-maintenance-during-receipt-only-recovery.md).

### Staged schema migration is refused or incomplete

`perfeng-release-schema-migration-v1` remains staged with live acceptance pending.
Wrong deployment lock, changed request bytes, migration mode none, automated
activation or migration ID other than 0018 must stop before the Job, as must
failed fresh quiescence. Verify the pinned request, immutable intent ConfigMap
and explicit schema-migration Application-read bootstrap before diagnosing Job
creation. Missing credentials require explicit provisioning through existing
procedures, not rotation or replacement. A Job API denial, image-pull failure,
timeout or SQL error cannot qualify as migration success. A lost create response
may still leave `perfeng-schema-<Workflow UID>` or committed SQL: preserve private
Workflow/Job/spec/provenance and schema-ledger evidence, keep the service paused,
and follow coordinated M04 recovery. Do not retry nodes, delete/recreate Jobs,
force sync or infer rollback from Git. Job Complete does not establish runtime
grants, schema parity, admission or activation. See the
[staged migration guide](release-schema-migration.md) and
[ADR 0049](decisions/0049-stage-pinned-schema-migration.md); this authorizes no
local execution or host-script cutover.

### Native target Docker build reports only an exit code

Older native-target build errors discarded captured Docker output and reported
only the exit code, so a historical failed runner's cause cannot be recovered
from that log. New build errors add fixed `BUILD_*` categories for recognized
storage, registry/rate-limit/auth/TLS/network, daemon/builder, context and
manifest signals. `BUILD_BASE_RESOLUTION` remains a broad metadata-resolution
failure; `BUILD_UNKNOWN` is explicitly unclassified. No raw backend output or
credentials are printed. A category is a diagnostic signal, not permission to
change a reviewed pin, rotate credentials or bypass verification. Check the
named boundary and the pinned manifest/platform; its availability elsewhere
does not establish runner connectivity. Keep heavy rebuilds on CI when the host
is busy. See [candidate build diagnostics](native-browser-candidate.md#build-failure-classification).

### Schema-parent repeat acceptance stops before recovery

`schema-parent-*` stages run only after verified pause in the initial trusted-main
job. Setup requires the schema already contains all 18 released migrations.
None-intent must fail at the actual pinned validator with semantic exit 2 before
quiescence/Job creation; a generic error or startup failure cannot qualify.
The valid cases reuse upgrade-request, pass fresh quiescence and must retain
two distinct native Jobs without any schema/role/ledger/data change.

Inspect bounded stage/Pod startup projections before private Workflow/Job/Pod,
SQL/schema/role/Secret and source snapshots. A missing Job or failed preflight,
intent, API wait or migrator is not success. Changed checksums, timestamps,
rows/sequences, provenance, credentials or paused resources block recovery.
Do not retry nodes, rewrite records, delete Jobs, relax comparisons or sync the
service to pass acceptance. Only all case/retention gates permit the existing
explicit recovery; failures remain paused for owned disposable cleanup.
See [ADR 0050](decisions/0050-exercise-paused-schema-migration-repeats.md).
Heavy checks stay on PR CI; live proof remains post-merge, with no local action.

### Schema-parent denied authority does not qualify

[ADR 0051](decisions/0051-exercise-schema-job-create-denial.md) requires
`schema-parent-denied-authority`, `schema-parent-denied` and
`schema-parent-denied-restoration` in the initial paused suite. Expected access
is create=no/get=yes/watch=yes while narrowed, and all yes after restoration.
An auth-check warning, transport failure or unexpected response is not denial.
Inspect private Role snapshots and the denied native worker's main log: real
preflight/selection/intent/quiescence must succeed, then only creation of the
planned migration Job must return Forbidden for release-schema-migration in
batch/perf-platform. Failed GET, image startup, timeout, unexpected Job/Pod,
parent output or any SQL/retention drift fails the case. Safe artifacts contain
only fixed diagnostics and validated outcomes, never raw logs or SQL/Secrets.

Do not manually restore the Role, retry workers or recover the service to turn
an unproved case green. Only proved refusal and retention permit the captured
rules' UID/resourceVersion/rules-guarded restoration. A stale patch or changed
Role stops acceptance for owned disposable cleanup. Heavy checks stay on CI;
live proof remains post-merge, with no local action.


### Complete schema first installation stops

`schema-install-setup` and `schema-install-parent` run only in the trusted-main
initial leg ([ADR 0052](decisions/0052-exercise-complete-schema-first-install.md)).
Setup must observe the naturally empty database and explicit paused bootstrap
objects. A preexisting schema, application relation, native migration Job or
fixed pull Secret is a refusal, never a reason to delete or reset anything.
Inspect bounded startup/stage projections, then the private complete-parent,
Job/Pod, role/resource and Secret snapshots. Missing Job, failed prefix/intent,
image startup, nonzero migrator, incomplete schema ledger or retained-state drift
cannot qualify. Raw credentials, SQL and logs must stay out of shared artifacts.

Server setup must reuse the exact early immutable pull Secret. A different UID,
credential data/type/metadata or missing observation stops before resource
creation; do not recreate or rotate it as a repair. The paused-suite baseline
must contain exactly the original install Job, and final retention must preserve
its Job/Workflow/Secret before aggregate success or checked recovery. Failure
stops for owned disposable cleanup, without fallback migrators, retries, SQL
repair or local infrastructure actions. Full checks stay on CI and live proof
remains post-merge; placeholder installation is not adopted server readiness.


### Schema SQL contention does not qualify

[ADR 0053](decisions/0053-exercise-schema-sql-contention.md) requires exact
catalog evidence before an expected failed native execution can qualify.
`schema-contention-holder` must show one owned granted lock on the exact key.
A partial owned-connection startup observation may be pending; a foreign holder,
wrong key/database or malformed observation is a failure. During the unchanged
parent, require a pre-exit waiting backend bound to the actual migration Pod IP,
role/application, PID/backend start, exact query and sole owned blocker.
A generic failed Job, missing sample, deadline, image or transport failure is
not SQL contention proof.

At `schema-contention-proof`, confirm absent schema/zero application relations,
retained state and the same idle holder after terminal native failure. Never
terminate database backends, alter roles/Secrets, release arbitrary sessions,
retry nodes or repair SQL to pass. Only proved refusal/retention permits normal
`\q` disconnect of the owned fixture session. A failed migration backend may
linger until that release; require its disappearance and continued schema
absence before fresh installation. Unknown/reused backend identities stop the
cleanup gate. Failed proof leaves cleanup to the owned disposable-cluster boundary.

Raw catalog, IP, log and credential observations stay private; artifacts contain
only bounded diagnostics and validated outcomes. Require retention of the failed
Job/Workflow alongside successful install/repeat evidence before recovery.
Heavy checks stay on CI and live proof remains post-merge. This is not proof of
partial-write rollback or ambiguous-commit recovery.


### Schema rollback proof or fixture cleanup stops

[ADR 0054](decisions/0054-exercise-schema-ddl-rollback.md) requires exact
PostgreSQL collision evidence after the complete parent's real prefix and
native failure. Check `schema-rollback-setup/parent/proof/cleanup` and private
catalog/row/log/Job/Pod snapshots. The error must name artifacts for the selected
migration identity/application within the Job/Workflow time window; an unrelated
relation, older message, startup failure or deadline cannot qualify. Require
unchanged fixture OIDs/owners/comments/columns/sentinel and no persisted migration
tables/ledger or remaining migration sessions/locks. Any retained-state drift
blocks cleanup and fresh installation.

Do not drop, recreate or repair objects manually to pass acceptance. Only proved
rollback generates the bounded transaction's table lock, exact catalog/sentinel
guards and DROP RESTRICT statements. A changed identity, extra dependency or
unexpected row aborts cleanup. No CASCADE, IF EXISTS, release-ledger edit or EXIT
repair is authorized. After normal cleanup, require the original empty catalog
and retained surrounding state before fresh installation. Both failed Jobs and
Workflows remain retained; live proof is post-merge and heavy checks stay on CI.
This does not establish ambiguous-commit recovery or authorize local execution.


### Staged runtime grant worker refuses input or authority

For `grant-parent-corrected`, first require the existing excess-DELETE runtime
probe to prove its exact policy refusal. The fresh ACL snapshot must differ from
first success only by the one added runtime DELETE on runs; other principals,
grantors, grant options, tables or schema ACL changes refuse before submission.
The unchanged owner parent must pass its real gates and fixed Job, preserve
non-ACL state and restore original ACL bytes, followed by the corrected runtime
TCP probe. Do not fall back to administrator SQL after a failed boundary. Final
retention must preserve the additional successful Job/Workflow and replay exact
correction evidence. [ADR 0060](decisions/0060-exercise-owner-grant-correction.md)
changes only the initial scenario; other five scenarios keep their existing path.

For `grant-rollback-fixture`, `grant-parent-rollback` or `grant-rollback-proof`,
inspect private `grant-rollback.*` catalogs, worker/server logs and retained
state. Expected refusal requires the unique fixture exception for the exact
owner/application/schema-USAGE statement during the real grant Pod's lifetime.
The read-only fixture function must observe USAGE inside the transaction, while
committed ACLs afterward match the ungranted baseline. An unrelated SQL error,
changed definition/owner/OID, active grant session or leftover key lock blocks
cleanup. Only complete rollback/retention proof generates guarded transactional
DROP RESTRICT; never use CASCADE, replace foreign objects or add EXIT repair.
Cleanup must restore the original catalog before fresh grants, and final
retention must find no fixture objects with successful runtime USAGE established.
See [ADR 0059](decisions/0059-exercise-runtime-grant-rollback.md); actual trigger
proof runs post-merge, while ambiguity and local execution remain outside scope.

For `grant-contention-holder`, `grant-parent-contention` or
`grant-contention-proof`, inspect the private catalog stream, exact worker/Job
evidence and timestamped PostgreSQL log. Expected refusal requires the actual
owner backend from the grant Pod waiting on the owned exact advisory key, then
the server's lock-timeout error/statement and native exit 1/Error. A failed Job,
deadline, wrong blocker, missing pre-exit wait or stale/foreign error cannot
qualify. ACL/schema/data/credential/storage retention must pass while the lock
is still held. Only that proof permits normal owned-session disconnect; both
sessions and key locks must disappear before fresh first/repeat grants. Never
terminate a database backend or add EXIT repair to get past this gate. Final
retention rechecks the failed Job/Workflow and fresh session absence. See
[ADR 0058](decisions/0058-exercise-runtime-grant-sql-contention.md); partial writes,
ambiguous commits and local execution remain outside scope.

For the initial fixture's `grant-denied-authority` / `grant-parent-denied` stages,
an expected refusal must come from the grant worker's own native Job CREATE,
after real input/quiescence success. Preliminary GET Forbidden, image failure,
timeout or generic authorization messages do not qualify. Inspect private
`grant-denied.log`, Workflow/worker evidence and before/after ACL, Job and retained
state snapshots. Preserve get/watch and all reads while denying only create.
Only verified refusal/retention allows guarded restoration of the captured Role;
any failed boundary stops with no EXIT repair or successful grant continuation.
Final retention must attest the restored Role and its effective rights as well
as the failed Workflow. [ADR 0057](decisions/0057-refuse-runtime-grant-job-create.md)
adds no SQL fault recovery or local execution.

For the trusted-main initial scenario, inspect the `grant-parent-first` or
`grant-parent-repeat` stage and private `grant-<case>` evidence. The unchanged
owner parent must complete real ordered input/quiescence gates and its exact
native Job; timeout or missing Job cannot count as success. Before/after state
and repeat ACL retention must pass before server setup. The final scenario gate
also re-reads both Jobs/Workflows and installed sources. Shared artifacts contain
only validated `grant-parent-<case>.outcome.json` identifiers, not SQL/credential
diagnostics. A failure leaves the fixture paused: do not replace Jobs, fall back
to administrator grants or infer that a failed response rolled back SQL.
[ADR 0056](decisions/0056-exercise-owner-runtime-grants.md) authorizes disposable
first/repeat acceptance only; no local execution, cutover or recovery is added.

[ADR 0055](decisions/0055-stage-native-runtime-grants.md) stages the owner-only
runtime-grants parent; no local/live execution or host cutover is authorized by
its staging PR. An input refusal occurs before psql when migration-url differs
from the reviewed local owner/host/port/database/password contract. Inspect the
Secret mapping through an explicitly authorized path; never print its URL or
rotate credentials as repair. Inherited libpq service/options/address overrides
are cleared before connection.

A fixed `[RUNTIME-GRANTS]` failure means the retained Job needs inspection;
public output deliberately withholds vendor diagnostics. Missing/wrong owners,
required tables, unsafe roles/memberships, existing column ACLs or unexpected
effective privileges abort the transaction. Keep the service paused and inspect
private Pod diagnostics and effective ACLs before a separately authorized fresh
parent. A failed response does not prove ACLs were unchanged. Do not retry nodes,
delete/replace Jobs, grant administrator authority, repair roles or sync workloads
to pass. The worker mounts no PostgreSQL administrator Secret. Complete live
first/repeat/refusal/data-retention proof remains a later scoped acceptance PR;
existing fixture-administrator parity does not establish this new parent.
