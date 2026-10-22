# Staged M04 maintenance quiescence checkpoint

## TL;DR

- `perfeng-release-maintenance-checkpoint-v2` verifies an already paused M04 control plane using reviewed release preflight and bounded read-only observations.
- It requires owned, paused root/child Applications, no active sync, a current zero-replica Deployment and no matching Pods, including terminating Pods.
- Its versioned result is observation evidence, not a promotion receipt, backup proof, migration authorization or an ongoing lock.
- Keep the [manual M04 maintenance and recovery path](kubernetes-runtime-deployment.md). This staged checkpoint does not pause, migrate, resume, sync or repair anything.
- A [trusted-main CI phase](release-workflows-integration.md#complete-maintenance-parent-composition)
  exercises this unchanged complete parent using the real disposable UID and
  exact Git request after full preflight. Fixture Applications remain synthetic;
  live composition acceptance is pending its post-merge main run.

## Inputs and execution

```text
reviewed request + pinned current selection -> existing preflight
  -> root, child and Deployment reads -> bounded empty PodList check
  -> reread resource identities/versions -> recheck cluster UID
  -> PerfengMaintenanceCheckpoint v1
```

The parent takes the same four inputs as [release preflight](release-preflight-workflow.md): `request-revision`, `request-path`, `request-sha256` and `selection-revision`. The request digest uses `sha256:<64 lowercase hex characters>`. Select the reviewed lock corresponding to the currently installed child sources, rather than assuming a future candidate has been activated. The checkpoint verifies the exact selected lock bytes against preflight and the detached Git checkout against the selected commit. The child Application's two repository/revision/path tuples must match that lock. Root source remains the existing repository, local composition path and `main`; this does not prove that the root has reconciled the selected commit or establish promotion provenance.

Schedule downtime, stop submissions, inspect/drain active Runs and retain a suitable backup through the existing M04 process. Use its explicit host pause command first. Apply the separately reviewed `release-workflows-bootstrap/maintenance-application-access.yaml` grant before syncing the staged release Application. Submit using the [preflight submission procedure](release-preflight-workflow.md#disposable-kind-verification), replacing `--from` with `workflowtemplate/perfeng-release-maintenance-checkpoint-v2`. Do not redirect `task kubernetes:prepare` or the active local root to this pilot.

The parent reuses the local release mutex and has a 900-second deadline. The checkpoint container has a 180-second deadline, makes eight fixed HTTPS GETs with ten-second request timeouts and one-MiB response limits, and never polls or repairs resources. It reads root, child and Deployment; lists at most one matching Pod; rereads those three objects; then checks the `kube-system` UID. An observed resource version or UID change refuses success. Pod pagination, missing list metadata, any matching Pod, malformed responses, redirects and API/authorization failures also refuse success.

## Identity and permission boundary

Both Applications must have the environment owner label, the existing project/destination/source identities, no owner references or deletion, explicitly disabled automatic sync and no pending/running/terminating sync operation. The Deployment must retain its name, namespace, workload labels, selector and ServiceAccount. Its observed generation must be current, desired replicas zero and all reported replica counts zero. Absent status counts may mean zero only with that current observed generation and the independent empty PodList check.

The dedicated `release-maintenance-checkpoint` identity can get only the two named Applications, one named Deployment, `kube-system` and the existing `release-git-read` credential; it can list Pods in `perf-platform` and publish Argo task results. The Application read grant lives in explicit bootstrap, keeping `argocd` outside the staged Project destinations. No workload, Application, Secret or Job writes are granted. Pod list permission exposes namespace Pod data; the fixed workload label selector is a request filter, **not** an RBAC boundary. This is a trusted-operator pilot on the dedicated local cluster, not M06 multi-user admission.

The shared two-module stdlib verifier package is mounted from immutable `perfeng-release-verifiers-v2`. Argo owns preflight sequencing, the mutex, deadlines and final output. The helper owns the bounded observation predicate needed to prove Pod absence, which the pinned native resource template cannot express as a named single-resource condition. It uses the mounted ServiceAccount token and verified cluster CA, disables proxies and redirects, logs no response bodies or credentials, and invokes no subprocess. It runs in the existing digest-pinned Python image with non-root identity, dropped capabilities, read-only root filesystem and scratch storage. Published script/template versions cannot be edited in place; add a new version instead.

## Evidence, failure and recovery

The [checkpoint schema](../deployments/maintenance-checkpoint-v1.schema.json) binds the request digest/commit, selection commit, cluster UID, Application/Deployment UIDs and resource versions, and PodList resource version. The output is written only after all checks pass. No successful evidence is emitted on failure. It proves no backup, migration history, active-Run drain, image/configuration identity, schema compatibility or forward-repair readiness. The [readiness workflow](release-readiness.md) remains a separate check for a running release.

The bracketed reads detect changes during sampling; they are not an atomic Kubernetes transaction or protection against later activity. The shared workflow mutex does not serialize host operators, other workflow identities or Argo CD. A migration or other mutation must independently verify current quiescence immediately before writing. Do not reuse successful nodes with `argo retry`, cache the predicate, or treat a saved result as authorization. Submit a fresh Workflow for each new observation.

On refusal, inspect the failing node and existing owner diagnostics. Active sync must finish or be resolved through its owner before another manual pause. For nonzero replicas or remaining Pods, inspect the Deployment/termination cause and wait through the existing bounded maintenance procedure; do not force-delete Pods. Changed versions require inspection followed by a fresh Workflow, never relaxed assertions. A failed preparation may leave root/child paused and the API stopped. Preserve Jobs, volumes, credentials and evidence. Follow the existing M04 forward-repair or explicitly approved database/application recovery procedure; migration 0018 is not undone by a Git revert. Resume root and manually sync the child only at the existing reviewed cutover point.

## Verification and remaining migration work

`make validate` checks the schema, refusal paths, bounded reader, output ordering, RBAC and rendered ConfigMap references. `make workflow-lint` resolves this parent, its versioned child and preflight together. These are offline checks, not live maintenance acceptance.

Before operational replacement, use an isolated disposable kind fixture to prove first/repeated success after a manual pause; refusal for unpaused/foreign Applications, active sync, a running/terminating Pod, stale Deployment status, wrong cluster UID and missing permission; and absence of workload/Application patch, Pod deletion, Job creation and Secret enumeration rights. Retain failed Workflow identities and prove a fresh submission observes corrected state. No live fixture is selected by this PR. Automated pause, migration, prior-release receipt/adoption, recovery writes, broader health parity and retirement remain separate reviewed work.

## Verifier package lifecycle

The separate [kind integration gate](release-workflows-integration.md) exercises
this unchanged verifier child under the real Argo controller and Kubernetes
RBAC, including refusal and fresh-attempt recovery. Its synthetic preflight
and Application fixtures do not satisfy full maintenance-parent, release
admission or operational-cutover acceptance. The separate trusted-main extension
uses actual complete preflight followed by this parent: it checks first/repeat,
preflight refusal before observation, unpaused/revoked-read refusal and fresh
correction. It retains nested preflight evidence without confusing that output
with a failed parent's checkpoint. No operational cutover follows from fixture
composition alone.

The staged entrypoint uses the [v2 verifier package](release-verifier-package.md).
Request and result data schemas remain v1. Published v1 source is recoverable
from the retirement index; installed v1 objects are retained with pruning off.
Use a fresh Workflow after upgrading; do not resume successful v1 nodes as v2
verification. The active operator path and outstanding live acceptance gates
remain unchanged.
