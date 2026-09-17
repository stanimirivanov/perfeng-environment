# Local Kubernetes infrastructure release selection

- Status: Pinned and validated; ownership not transferred
- Selected repository: `stanimirivanov/k8s-infrastructure`
- Selected profile: `local-kind/perfeng-local`

## TL;DR

- `deployments/local-infrastructure.json` pins the exact reviewed infrastructure
  commit used for the forthcoming local ownership cutover.
- Validation reads files from that Git commit, not from the checkout's current
  branch or working tree, and performs no network or cluster access.
- The lock proves the existing cluster, kubeconfig, node topology and Argo CD
  compatibility identities are preserved.
- It also proves both selected lifecycle entry points remain dormant.
- `perfeng-environment` remains the active owner until a separate cutover issue
  changes both repositories and passes live acceptance.

## Purpose and boundary

The source workspace follows development branches, while an environment must
consume a reviewed immutable infrastructure revision. The local infrastructure
lock separates those concerns. It selects the repository commit and records the
compatibility values that Perfeng relies on:

- profile `local-kind/perfeng-local`;
- cluster `perfeng-local` and context `kind-perfeng-local`;
- dedicated kubeconfig `.local/perfeng-local.kubeconfig`;
- the reviewed Kubernetes node image and three-node placement topology; and
- Argo CD `v3.5.2` in namespace `argocd`.

This lock is not an Argo CD source authorization and does not add the
infrastructure repository to the Perfeng AppProject. It does not select consumer
Applications, credentials, workloads or component releases.

## Offline validation

The sibling infrastructure checkout may be on another branch or contain local
changes. The selected commit only needs to exist in its object database. Run:

```sh
uv run --locked python scripts/infrastructure_release.py validate
```

The validator verifies the checkout's canonical GitHub origin, reads the exact
commit with bounded read-only Git commands, and validates:

- strict lock structure with no unknown or duplicate fields;
- immutable 40-character revision and repository-relative source paths;
- cluster name, API binding, node image, roles and placement labels;
- lifecycle actions and the pre-cutover execution refusal;
- the official semantic Argo CD install-manifest URL;
- the infrastructure-owned Argo CD namespace; and
- the dormant Argo CD lifecycle boundary.

It does not import either module, execute repository code, render the remote
Kustomization, fetch a revision, switch a branch or contact Kubernetes.

CI checks out the selected private revision with the existing read-only Contents
token and runs the same validator. The checkout does not persist credentials.

## Promotion workflow

An infrastructure promotion is a reviewed change to the lock, CI checkout and
compatibility evidence together:

1. merge and validate the infrastructure change in `k8s-infrastructure`;
2. select its resulting `main` commit, never its feature-branch name;
3. update the lock and CI checkout to that exact commit;
4. run offline validation against a checkout containing the selected object;
5. review any compatibility change explicitly; and
6. retain the prior commit as the rollback selection.

The lock intentionally does not follow `main`. Workspace bootstrap may clone
`main` for development, but mutable branches are not deployment inputs.

## Current ownership and cutover

Pinning is a prerequisite, not an ownership transfer. The selected lifecycle
scripts reject live execution, and `Taskfile.yml` continues to invoke the active
`perfeng-environment` cluster and GitOps commands.

The later cutover must coordinate both repositories so that there is never more
than one active owner. It must preserve the cluster, context, kubeconfig,
namespace, controllers, Secrets, Applications, volumes and retained data. It
must prove infrastructure health followed by Perfeng GitOps health, smoke and
persistence acceptance before the old source is removed.

## Failure and recovery

### The sibling checkout is missing

Run workspace bootstrap or clone `k8s-infrastructure` at its documented sibling
path. Validation never clones automatically.

### The selected revision is missing

Fetch through the normal repository workflow, then rerun validation. Do not
replace the lock with the current branch or an unrelated local commit merely to
make the check pass.

### The origin is rejected

Inspect `git remote get-url origin`. HTTPS and standard GitHub SSH spellings are
accepted when they identify the configured repository. Do not add credentials
to the lock or remote URL.

### Compatibility validation fails

Compare the selected files at the locked commit with the reported contract.
Either correct an unintended infrastructure regression or promote an explicit
compatibility change with its migration plan. Do not relax the validator or
execute the dormant lifecycle as a workaround.

### Rollback before cutover

Revert the lock-selection pull request. No live resource changed, so no cluster
rollback is required. After cutover, use the separately reviewed operational
rollback rather than changing the lock alone.
