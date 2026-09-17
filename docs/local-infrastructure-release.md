# Local Kubernetes infrastructure release selection

- Status: Selected executable revision; live acceptance pending
- Selected repository: `stanimirivanov/k8s-infrastructure`
- Selected profile: `local-kind/perfeng-local`

## TL;DR

- `deployments/local-infrastructure.json` pins the exact reviewed
  `k8s-infrastructure` commit that owns the local kind and Argo CD lifecycles.
- `scripts/infrastructure_runtime.py` validates that commit, materializes a
  temporary Git archive, and invokes only its selected lifecycle entry point.
  It never executes the sibling checkout's branch or uncommitted files.
- `task bootstrap`, `task status`, and `task destroy` delegate substrate work to
  that immutable provider. `perfeng-environment` retains Perfeng namespaces,
  credentials, Applications, workloads, and product acceptance.
- Run the after-merge acceptance below before removing the retained legacy
  cluster and Argo CD source from this repository.
- If the ownership handoff itself must be reversed, use the bounded namespace
  rollback command below; do not run two Argo CD installers concurrently.

## Purpose and ownership boundary

The source workspace follows development branches, while an environment must
consume a reviewed immutable infrastructure revision. The lock selects the
provider commit and records the compatibility values that Perfeng relies on:

- profile `local-kind/perfeng-local`;
- cluster `perfeng-local` and context `kind-perfeng-local`;
- dedicated kubeconfig `.local/perfeng-local.kubeconfig`;
- the reviewed Kubernetes node image and three-node placement topology; and
- Argo CD `v3.5.2` in namespace `argocd`.

`k8s-infrastructure` now owns cluster create/reuse, health and destruction, plus
Argo CD validation, installation and controller readiness. This repository
owns the four Perfeng namespaces, external credentials and durable inputs, the
root and child Applications, workload health, smoke tests, and the roadmap.
The infrastructure repository is not an Argo CD workload source and is not
added to the Perfeng AppProject.

The legacy implementations remain checked in temporarily as rollback evidence.
Their public command surfaces no longer own cluster or Argo CD operation:
`cluster.py` exposes only Perfeng namespace bootstrap, while `gitops.py` manages
consumer credentials, Applications and workload health.

## Immutable execution

Validate the selection without Docker, Kubernetes, network access, or importing
provider code:

```sh
uv run --locked python scripts/infrastructure_release.py validate
```

Preview the selected provider entry points without contacting the local cluster:

```sh
uv run --locked python scripts/infrastructure_runtime.py cluster ensure
uv run --locked python scripts/infrastructure_runtime.py argocd validate
uv run --locked python scripts/infrastructure_runtime.py argocd bootstrap
```

For every invocation, the wrapper:

1. verifies the sibling checkout's canonical GitHub origin and the complete
   compatibility contract at the selected commit;
2. reads a bounded `git archive` of that exact revision;
3. rejects traversal, links, special files, oversized archives and invalid
   source paths;
4. executes the selected module from a temporary directory with isolated Python
   import resolution; and
5. binds provider state to this environment repository so the existing
   `.local/perfeng-local.kubeconfig` identity is preserved.

The sibling checkout may be on another branch or contain local changes. The
selected object must exist locally, but its working tree is never executed.

## Promotion workflow

An infrastructure promotion is a reviewed change to the lock, CI checkout and
compatibility evidence together:

1. merge and validate the provider change in `k8s-infrastructure`;
2. select its resulting `main` commit, never a branch name;
3. update the lock and CI checkout to that exact commit;
4. run offline validation against a checkout containing the selected object;
5. review every compatibility change explicitly; and
6. retain the prior commit as the rollback selection.

The lock intentionally does not follow `main`. Workspace bootstrap may clone
`main` for development, but mutable branches are not deployment inputs.

## After-merge acceptance

Run this acceptance only after the consumer cutover reaches `main`. It mutates
the local environment and assumes its data may be retained by the existing
named cluster:

```powershell
uv run --locked python scripts/infrastructure_release.py validate
task bootstrap
task status
task smoke
```

Acceptance requires:

- the existing cluster and dedicated kubeconfig to be reused, or a missing pair
  to be created by the selected provider;
- the `argocd` namespace to carry the exact `k8s-infrastructure` ownership
  identity;
- provider cluster and Argo CD health to pass;
- all Perfeng Applications and workload checks to pass; and
- one control-plane Run to complete with five verified artifacts.

Record the non-secret PostgreSQL and SeaweedFS StatefulSet, PVC and PV
identities before and after acceptance when reusing a stateful cluster. Run the
existing persistence probes from the stateful runbooks. A passing smoke Run is
not permission to ignore changed persistent identities.

Only after this acceptance is recorded should a separate PR remove the retained
legacy cluster and Argo CD implementation files and their obsolete tests.

## Rollback of the ownership handoff

Routine release rollback selects the previous reviewed provider commit in the
lock. Do not edit the sibling checkout or point the lock at a mutable branch.

If provider bootstrap fails specifically because the existing Argo CD namespace
handoff must be reversed, first inspect the cluster and provider diagnostics.
Then preview and explicitly restore only the reviewed legacy namespace labels:

```powershell
uv run --locked python scripts/infrastructure_runtime.py restore-legacy-owner
uv run --locked python scripts/infrastructure_runtime.py restore-legacy-owner `
  --execute --confirm-owner perfeng-environment
```

The executable rollback first requires provider cluster health, requires the
current complete `k8s-infrastructure` namespace identity, server-side applies
the retained legacy namespace manifest, and verifies the resulting
`perfeng-environment` identity. It does not uninstall Argo CD, rotate a Secret,
change an Application, delete a workload, or alter durable data. After rollback,
use only the reviewed legacy recovery procedure; never run both installers.

## Failure and recovery

### The sibling checkout or selected revision is missing

Run workspace bootstrap or fetch through the normal repository workflow, then
rerun validation. Do not replace the lock with the current branch merely to
make a check pass.

### The origin is rejected

Inspect `git remote get-url origin`. HTTPS and standard GitHub SSH spellings are
accepted when they identify the configured repository. Never add credentials
to the lock or remote URL.

### Compatibility validation fails

Compare the selected files at the locked commit with the reported contract.
Correct an unintended provider regression or promote an explicit compatibility
change with its migration plan. Do not relax the validator or fall back to an
unselected working tree.

### Provider execution fails

Run the corresponding selected health command directly through the wrapper:

```powershell
uv run --locked python scripts/infrastructure_runtime.py cluster health --execute
uv run --locked python scripts/infrastructure_runtime.py argocd health --execute
```

Use the [troubleshooting runbook](troubleshooting.md) for bounded Kubernetes
inspection. Do not delete the cluster as a diagnostic step.
