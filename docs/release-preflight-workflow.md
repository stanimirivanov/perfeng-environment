# Read-only release preflight pilot

## TL;DR

- The staged [WorkflowTemplate](../gitops/release-workflows/release-preflight.yaml)
  checks a reviewed v1 request against exact Git checkouts, deployment-lock
  bytes and the observed `kube-system` UID. Its output is a versioned Workflow
  parameter, not a release receipt or permission to deploy.
- The [GitOps bootstrap](../gitops/release-workflows-bootstrap/) is deliberately
  absent from the active local root. Use it only on a disposable kind cluster
  after the pinned Argo Workflows substrate and a dedicated private Git read
  credential are available. The retained M04 cutover remains manual.
- The staged [external-state preparation](release-external-state.md) calls this
  preflight before creating any Job and has a separate, bounded ServiceAccount.
- A request is committed at a **different** revision from the release selection
  it names. One commit cannot contain its own Git hash.

## Input and ownership

The request file lives at `deployments/release-requests/<name>.json` in a
reviewed `perfeng-environment` commit. Submission passes that commit, its path,
the SHA-256 of its exact bytes, and the selected environment commit. The
request's `selection.revision` must equal the selected commit. The request
commit must contain the unchanged v1 schema, whose hash is pinned in the
WorkflowTemplate. Argo's Git artifact driver fetches each private checkout
with the namespaced `release-git-read` Secret. That Secret has `username` and
`password` keys and must contain a credential restricted to read this one
repository. It is provisioned outside Git; this pilot neither creates nor
copies a credential from Argo CD.

The workflow first applies the v1 JSON Schema using the pinned Sourcemeta
JSON Schema CLI. It then reads the target cluster UID using an Argo resource
`get`, verifies both Git checkout commits, rejects duplicate JSON keys,
compares the raw request hash, and checks each selected lock's raw bytes.
Images are pinned by OCI digest. The ServiceAccount can get only the named Git
credential Secret and `kube-system` Namespace, plus create/patch
`workflowtaskresults` for the Argo executor. It cannot apply workloads, write
Secrets, run a migration or sync Argo CD.

Only trusted operators may create Workflows in `argo-workflows` during this
disposable-kind pilot. Kubernetes does not restrict a Workflow creator from
selecting another ServiceAccount in the same namespace. Before a shared or
non-local deployment, enforce submission admission and per-operator identity
so a user cannot run arbitrary code under this Git-reading ServiceAccount.
This pilot grants no general multi-user deployment authority.

The pilot's GitOps Application has **manual** sync and is not included in
`gitops/environments/local`. This prevents a merge from installing it on the
retained cluster before the Argo Workflows substrate is selected. In a
disposable cluster, register the bootstrap Project and Application, inspect
the diff, then manually sync the child once the namespace and CRDs exist.
Argo CD owns the resulting template and RBAC. The active root and current
infrastructure lock are unchanged.

## Disposable-kind verification

Use the exact kubeconfig and context of a disposable `perfeng-local` cluster on
an isolated host or runner, separate from the retained cluster.
Install Argo Workflows from the reviewed `k8s-infrastructure` v4.1.5 selection
first. Provision the read-only Git credential through the approved secret
channel, then inspect and manually sync the staged Application. A release
request for this disposable cluster must use its observed `kube-system` UID;
the historical schema fixture has a fake UID and `expectedCurrent: null` and
is not a live submission.

Submit an already reviewed request with the Argo CLI, naming the exact
request and selection commits:

```powershell
$env:KUBECONFIG = (Resolve-Path .local/perfeng-local.kubeconfig).Path
argo --context kind-perfeng-local -n argo-workflows submit `
  --from workflowtemplate/perfeng-release-preflight-v1 `
  -p request-revision=<reviewed-request-commit> `
  -p request-path=deployments/release-requests/<name>.json `
  -p request-sha256=sha256:<exact-request-bytes-hash> `
  -p selection-revision=<reviewed-selection-commit> --watch
```

Record the Workflow name and inspect `argo get <name> -o json` and bounded
step logs. A successful run exposes `preflight`'s `result` output in Workflow
status under the [v1 result schema](../deployments/release-preflight-result-v1.schema.json)
with `schemaVersion: 1`, the request digest, both Git revisions,
environment, cluster UID, lock digests and `status: identity-verified`.
Repeating the exact submission should yield the same identity values and
create no workload changes. Submit a modified lock digest or wrong cluster
UID as a negative test; it must fail with no result. Do not submit against the
retained cluster to satisfy this pilot gate.

`argo lint --offline gitops/release-workflows/release-preflight.yaml`,
`make verify` and `make validate` are the offline gates. The live two-run gate
requires Docker and a disposable kind cluster. No live acceptance is implied
by an offline pass.

## Result, recovery and limits

Workflow status and logs are operational evidence in the cluster. The separately
staged [adoption ledger](release-adoption.md) can record an explicitly reviewed
baseline; this preflight neither writes that ledger nor adopts an environment.
Cluster deletion removes both Workflow evidence and the cluster-local ledger
unless separately backed up. A preflight success proves the named bytes and observed
UID at that moment; it does not validate artifact registry provenance,
approve promotion, compare a prior receipt with Argo CD state, authorize
migration, or activate a workload. Existing lock-specific semantic validators
remain part of review until their checks have a workflow owner.

On a failed step, retain the Workflow and inspect its failing node and the
exact request/selection commits. Correct the reviewed input or prerequisite,
then submit a new Workflow using the same exact bytes for an exact retry. A
changed request is a new release identity. Do not weaken the schema, alter a
submitted request, grant broad permissions or use `expectedCurrent: null` to
adopt a running environment.
