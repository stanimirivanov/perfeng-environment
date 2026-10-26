# Deploy the local Kubernetes benchmark runtime

## TL;DR

- This selects `local-kind-integration/1.0.1`, not a production-performance cohort.
- On the rebuilt cluster, stop at [fresh-cluster recovery](#fresh-cluster-recovery):
  guarded first-install preparation is still required before runtime activation.
- For an already installed runtime, prepare the reviewed branch with `task kubernetes:prepare`.
  This is **mutating**: it pauses reconciliation and stops the API/worker before migrations.
- Publish the new pod-startup policy once, then merge the reviewed selection.
- Resume the root with `gitops.py bootstrap --execute`, wait for benchmark authority,
  then manually Sync the control-plane child in Argo CD, with pruning disabled.
- Run GitOps health and the existing checkout smoke, then follow
  [M04 decision-loop, cancellation and recovery acceptance](kubernetes-benchmark-acceptance.md).
  Deployment readiness alone does not complete M04.

## Selection and prerequisites

The [deployment lock](../deployments/local-control-plane.json) selects control-plane
source `2a74a20f69773fd9d43072e41668087612cdbf9e`, image digest
`e251a378be0915c68f30c7c1e5fa523ecd6d3efaae996b430933718c19e9c3b3`,
and `perfeng-k8s` merge commit `4c762262422420a1a223e1d56c2c469ab1c38c89`.
The chart stays at 0.4.0. The registry contains pod-startup contracts 0.22.0 and
checkout contracts 0.13.0, each selected per entry by `PERFENG_CONTRACTS_VERSION=registry`.
The mounted descriptor and both execution templates come from the exact pinned commit.
Native acquisition/authentication remains disabled; M03 is still deferred.

Bundle `bundles/local-kind-integration/1.0.1` and package
`packages/runtime-inputs/local-kind-integration-1.0.1` select the freshly qualified
cluster descriptor. Package provenance is `04e4de13c72f3bc5d61f8ecb4aff7eb22b57183b`.
The registry ConfigMap is `perfeng-runtime-registry-37bfeb2a30f6`; runtime ConfigMap
`perfeng-control-plane-runtime-315a2eede8ff` and its policy remain unchanged.
Profile/environment reference version stays 1.0.0; this is a bundle/package release.

Keep the adjacent `perfeng-k8s` and `perfeng-control-plane` repositories available
with those commits. Validation reads Git objects without changing their checkout.
Use the [staged foundation](local-gitops.md#fresh-cluster-staged-bootstrap) first
if PostgreSQL, SeaweedFS, namespaces or storage access do not exist. Resource
metrics must already pass [verified-TLS health](local-resource-metrics.md).

The promoted descriptor binds the current cluster incarnation: `kube-system` UID
`257a288d-9e0f-4956-a0e3-9c4104de6476`. Check it before preparation:

```powershell
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local `
  --request-timeout=10s get namespace kube-system -o jsonpath='{.metadata.uid}'
```

A different UID needs a new qualification and reviewed bundle promotion, not an
edited evidence file. Do not recreate the cluster to repair deployment.

## Fresh-cluster recovery

The rebuilt cluster has no installed control-plane Deployment. Selecting these
inputs does not create the database schema, runtime credentials, migrations or
published policies. The maintenance path below requires an existing owned
Deployment and deliberately refuses a missing one. Do not fabricate a zero-replica
Deployment, adoption receipt or current-release pointer to bypass that check.

After this selection merges, reconcile the staged root and accept metrics as
described in [metrics acceptance](local-resource-metrics.md#after-merge-acceptance).
A separate guarded first-install preparation task is required before runtime
activation; keep control-plane auto-sync disabled and do not manually Sync it yet.
The accepted upstream qualification supports this descriptor's provenance, not
permanent admission or a baseline approval. Preserve historical receipts and
obtain fresh qualification for subsequent benchmark acceptance. Deleted database
and object-store data are not recovered by selecting a new bundle.

## Prepare the maintenance window before merging

The [staged Argo maintenance checkpoint](release-maintenance-checkpoint.md) can
observe an already paused current selection on a disposable fixture. It does
not replace this pause, authorize migration, create a backup or resume the
service. Keep the following supported operator path until live parity and
recovery acceptance permit a reviewed cutover.

Stop new submissions and let current Runs finish; inspect retained active Runs
and Jobs before stopping the worker. Schedule downtime and retain an appropriate
database backup before applying migrations. No automatic backup/restore is provided.
Do not run another operator sync or rollout concurrently.

Set the package token in the current PowerShell session if a pull Secret is missing:

```powershell
$env:PERFENG_REPOSITORIES_READ_TOKEN = Read-Host "GHCR package-read token" -MaskInput
uv run --locked python scripts/control_plane_maintenance.py pause
uv run --locked python scripts/execution_registry.py deploy
task kubernetes:prepare
```

The two previews are offline. The Task then validates the pin, ownership-checks
and pauses the root Application first and control-plane child second, scales only
the existing `control-plane` Deployment to zero and waits at most 180 seconds for
its Pods to stop. Optimistic UID/resourceVersion tests reject racing changes.
Active syncs, ApplicationSet ownership and unexpected sources are refused.
Missing objects on a fresh cluster are not adopted or fabricated.

The migration command independently refuses writes unless both Applications are
paused, the owned Deployment has zero replicas and no matching Pods remain.
Its pinned Job runs migrations through 0018. Runtime grants include only
SELECT/INSERT/UPDATE on `benchmark_target_reservations`, explicitly **no DELETE**.
No native-worker grants or operator permissions are enabled.

Preparation creates or verifies the new content-addressed ConfigMaps, retaining
the previous maps and existing database/authentication/storage credentials. The
execution pull Secret is separate: `perfeng-execution-registry` in `perf-generators`.
It verifies the selected k6, kube-burner and analysis manifest digests before any
creation. Existing owned immutable credentials are reused and verified, never
copied from another Secret, rotated or attached to the default ServiceAccount.
Health never prompts or creates. Credential creation uses private stdin.

If preparation fails, the root/child remain paused and the API may remain stopped.
Inspect the exact failed phase and retry the Task after resolving it. It never
force-deletes Pods, replaces a failed migration Job or rolls back a schema.
Remove the token when finished:

```powershell
Remove-Item Env:PERFENG_REPOSITORIES_READ_TOKEN
```

Publish `pod-startup-k8s/1.1.0` as described in [policy publication](policy-publication.md).
Reuse the existing unchanged checkout policy. Publication is conditional-create,
not an overwrite: an existing policy key needs verification, not another upload.
Complete source review and merge only after preparation/publication succeed.

## Activate after merge

From updated `main`, resume only the root reconciliation:

```powershell
uv run --locked python scripts/gitops.py bootstrap --execute
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local `
  --request-timeout=30s -n argocd wait application/perfeng-local-benchmark-authority `
  --for=jsonpath='{.status.sync.status}'=Synced --timeout=180s
```

The root adopts the updated child definition with automatic sync **disabled**.
The child has two sources pinned to the same `perfeng-k8s` commit: the chart and
the versioned non-secret runtime-input package. Inspect the Argo CD diff for
the complete child before sync; the two ConfigMaps must have the reviewed names,
immutable bytes and existing ownership, with no unexpected Secret or persistent
resource changes. Stop on any mismatch rather than forcing adoption.
The Deployment is intentionally OutOfSync/zero until explicitly synced. In the
[Argo CD UI](local-gitops.md#health-and-inspection), open
`perfeng-local-control-plane`, refresh, confirm both source revisions and image
match the lock, then choose **Sync**. Leave **Prune**, **Force** and **Replace** off;
sync the complete Application, not a selected subset. Do not enable auto-sync.
The reviewed chart restores the single replica only after the migration window.

Verify the complete live selection and compatibility path:

```powershell
uv run --locked python scripts/execution_registry.py health --execute
uv run --locked python scripts/gitops.py health --execute
uv run --locked python scripts/control_plane_smoke.py --execute
task kubernetes:qualify
```

Healthy means the root, authority and workload Applications are Synced/Healthy;
the API has one ready replica with the selected immutable mounts; exact static
RBAC and the cluster UID agree; migrations and runtime privileges verify; and
checkout completes with its five artifacts. Fresh qualification must independently
report six PASS checks. Promotion evidence is not current admission or a reservation.

## Authority, failure and rollback

The separate `perfeng-local-benchmark-authority` Project allows only this repository
and four RBAC resource kinds. Argo owns the static controller authority, not dynamic
per-Run namespaces, quota tombstones, ServiceAccounts, bindings or input ConfigMaps.
Pruning and self-healing of this authority are disabled. Controller cleanup retains
namespace/quota safety barriers; the environment must not delete them as repair.

This controller can create Pods and namespaces across the **dedicated disposable**
cluster and manage per-Run RBAC. Ordinary RBAC cannot constrain creates by name
prefix. Controller compromise can therefore compromise that cluster despite the
absence of Secret access, bind/escalate verbs and namespace deletion. This selection
must never be used on a shared/cloud cluster. See [ADR 0013](decisions/0013-select-local-benchmark-runtime.md).

Pause again before investigating an unsafe rollout. Preserve the migration Job,
database, old ConfigMaps, reservations and evidence. A Git revert alone is **not**
a safe rollback across cleanup migration 0018: old workers do not understand retained
cleanup holds. Keep the service stopped, review forward repair (preferred) or an
explicit backed-up database/application recovery together, and never start old/new
workers concurrently. See [troubleshooting](troubleshooting.md#m04-runtime-cutover).
