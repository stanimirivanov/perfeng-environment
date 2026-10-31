# Local Kubernetes infrastructure release selection

- Status: Active provider; ownership cutover accepted
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
- The initial cutover passed provider health, complete Perfeng health, stateful
  identity inspection and a five-artifact smoke Run on 2026-09-17. Superseded
  cluster and Argo CD installer source has been removed from this repository.
- Roll back a provider release by selecting a previously accepted immutable
  provider commit. There is no second local lifecycle owner.

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
The infrastructure repository is not added to the Perfeng workload AppProject.
Its [resource-metrics chart](local-resource-metrics.md) is explicitly selected through
a separate restricted add-on Project, never through workspace-derived authorization.
The selected revision also exposes generic read-only metrics health through the
isolated runtime wrapper; certificate mutation commands are not exposed there.

`cluster.py` exposes only Perfeng namespace bootstrap and shared safe command
utilities, while `gitops.py` manages consumer credentials, Applications and
workload health. Neither contains a cluster or Argo CD controller lifecycle.

## Selected metrics normalization and retained Redis correction

The selected provider revision is
`59b175492aad9d4b0900d03fe04dd0b2ff60c8eb`, merged in infrastructure PR #52.
It selects metrics chart 1.0.1, omitting the default-false APIService TLS flag to
match Kubernetes readback. Its [Redis decision](https://github.com/stanimirivanov/k8s-infrastructure/blob/59b175492aad9d4b0900d03fe04dd0b2ff60c8eb/docs/decisions/0011-pin-local-argocd-redis-image.md)
keeps Argo CD v3.5.2 and selects canonical Docker Hub Redis at the same
multi-platform content digest as the prior ECR Public image, with `IfNotPresent`.
Both pinned CI checkouts and the metrics Application select that provider
revision together. Metrics image 0.9.0, live TLS health rules and Application values
are unchanged. Cluster/context/node image, topology, namespaces and credentials
retain their existing contracts.

The consumer validates the exact approved image transformation and patch from
the pinned Git commit. Partial or arbitrary overrides fail. The prior exact
official-only bootstrap shape remains valid for an explicitly selected rollback;
there is no automatic provider fallback. Restore the lock, CI and metrics pins
together when rolling back. Historical request examples retain their committed
bytes and hashes; the existing disposable publisher calculates fresh hashes for
each new current-selection request.

This selection does not execute substrate bootstrap. Apply later through the
supported explicit owner path and require bounded controller readiness. Redis's
Pod may be replaced; no namespace, Secret or ledger deletion is part of the
change. The metrics Application retains its existing sync policy and public CA.
No bootstrap or Redis files changed between the prior selection and this promotion.
Docker Hub still has pull limits, and actual cold-pull and complete
release acceptance remain live gates.

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

## Release acceptance

Run this acceptance after every provider promotion reaches `main`. It mutates
the local environment and may reuse retained data in the existing named cluster:

```sh
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

For the current rebuilt cluster, use the staged root and metrics acceptance in
[metrics recovery](local-resource-metrics.md#after-merge-acceptance) first. Full
health and smoke require first-install runtime preparation through the existing
`task kubernetes:prepare`, policy publication and manual activation; this selection
alone does not complete those gates.

The initial ownership cutover completed this sequence on 2026-09-17. The
existing PostgreSQL and SeaweedFS StatefulSets and PVCs retained their older
creation identities, all Applications and workload checks passed, and the smoke
Run produced five verified artifacts.

## Release rollback

Provider release rollback selects the previous accepted provider commit in the
lock and matching CI checkout. Do not edit the sibling checkout, point the lock
at a mutable branch, or restore the removed environment-owned installer from
Git history. If no accepted provider revision is compatible with the live
cluster, stop and review a forward recovery; do not introduce a second owner.

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

```sh
uv run --locked python scripts/infrastructure_runtime.py cluster health --execute
uv run --locked python scripts/infrastructure_runtime.py argocd health --execute
```

Use the [troubleshooting runbook](troubleshooting.md) for bounded Kubernetes
inspection. Do not delete the cluster as a diagnostic step.
