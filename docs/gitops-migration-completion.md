# Local GitOps migration completion

- Status: Complete
- Completed: 2026-09-16
- Scope: Supported `perfeng-local` kind environment

## TL;DR

- Argo CD is the sole active reconciler for the sample SUT, PostgreSQL,
  SeaweedFS and control-plane workloads.
- ADR 0001's local-spike exit criteria have passed; M02 — Declarative Local
  Operations is complete for the supported local environment.
- Remaining Python commands create the cluster, establish external identities,
  prepare immutable inputs, validate health or execute product acceptance. They
  do not compete with Argo CD for active workload ownership.
- PostgreSQL and SeaweedFS retain dormant Helm rollback commands under ADR 0002;
  they may run only after the corresponding Application is removed with orphan
  propagation through the documented rollback procedure.
- Automated pruning remains disabled. Production topology, external secrets,
  backup/restore and infrastructure extraction remain separate decisions.
- Use `task bootstrap`, complete the explicit control-plane prerequisites, then
  use `task status` and `task smoke` as the supported operator path.

## Completion decision

The migration selected by [ADR 0001](decisions/0001-gitops-environment-composition.md)
is complete for the local environment. Reviewed Git state now selects all
supported workload resources, Argo CD continuously reconciles them, and the
Taskfile exposes the bounded operator workflow. The former sample-SUT and
control-plane Helm deployment paths and the redundant GitOps deploy action have
been removed.

Completion does not mean every Python command disappears. Commands remain when
their responsibility necessarily sits outside an in-cluster workload reconciler
or when moving it would obscure a product acceptance boundary. Those retained
operations are classified below.

## ADR 0001 exit criteria

| Exit criterion | Repository and acceptance evidence | Result |
|---|---|---|
| Pinned Argo CD on a new kind cluster | The versioned Kustomize bootstrap, offline validation and `task bootstrap` create or validate the named cluster and install Argo CD. | Passed |
| Declarative root and child Applications | The root manages the AppProject, four namespaces and child Applications for the sample SUT, PostgreSQL, SeaweedFS and control plane. | Passed |
| Private repository access without credentials in Git or arguments | An immutable repository-credential Secret is created through private stdin from a read-only environment token and reused without rotation. | Passed |
| Healthy sample SUT and control plane | `task status` waits for all Applications and validates workload rollout, immutable release identity and operational endpoints. | Passed |
| Visible drift and controlled self-healing | All four workload Applications enable self-healing with pruning disabled; stateful services passed staged adoption before self-healing was enabled. | Passed |
| Immutable runtime promotion | The deployment lock selects content-addressed runtime and registry ConfigMaps; prior identities remain available for Git rollback. | Passed |
| Actionable operational status | Bounded waits print the exact Application, sync, health, revision, conditions and operation message; the troubleshooting guide maps failures by dependency layer. | Passed |
| Milestone 1 smoke and post-restart retrieval | The authenticated k6-to-analysis smoke and retained-run restart retrieval paths validate the GitOps-managed control plane and durable artifacts. | Passed |

The evidence above combines deterministic repository checks with the live
acceptance recorded during the incremental M02 adoption, rollback, re-adoption,
self-healing, smoke and restart steps. A new machine or recreated cluster must
still run the documented bootstrap and live acceptance; completion is not a
claim that offline validation proves a particular cluster is healthy.

The final completion audit on 2026-09-16 ran `gitops.py health --execute` against
the existing local cluster. The root and all four workload Applications were
`Synced` and `Healthy`; PostgreSQL, SeaweedFS, migration, runtime-input and
control-plane endpoint checks passed; existing credentials were reused without
rotation. The object-store check retained its bounded diagnostic Job for local
inspection.

## Retained imperative boundaries

| Boundary | Owner and reason it remains outside workload reconciliation | Safety classification |
|---|---|---|
| Workspace clone/status | Developer workspace bootstrap cannot be owned by an in-cluster reconciler. | Local source operation |
| kind create, validate and destroy | The cluster and reconciler must exist before Argo CD can act; destroy is explicit and destructive. | Bootstrap/destructive |
| Namespace pre-bootstrap | Creates only the exact namespace resources the root later adopts, allowing external Secrets to be established first. | Bootstrap |
| Argo CD install and root bootstrap | Installs the reconciler, injects private repository access and creates its root Application. | Bootstrap |
| PostgreSQL and S3 credentials | Secret values remain outside Git and are reused without implicit rotation. | External identity |
| GHCR pull and Git repository credentials | Private registries and repositories require external read-only identities. | External identity |
| S3 bucket initialization | Bucket existence is durable object-store state, not a Kubernetes workload. | External state |
| Database roles and credentials | Database authorization state is intentionally external to the workload chart. | External identity/state |
| Schema migration Job | A versioned one-shot data transition is validated separately from continuous workload reconciliation. | Data migration |
| Runtime Secrets and content-addressed ConfigMaps | Secret material stays outside Git; immutable non-secret inputs are staged before the Git-selected rollout. | External identity/release input |
| Policy publication | Exact reviewed bytes require explicit digest approval and durable object publication. | Product data publication |
| Health and diagnostics | Checks establish semantic health beyond Kubernetes readiness without reconciling desired state; object-store health creates a bounded retained diagnostic Job. | Non-reconciling diagnostic |
| Smoke, persistence and upload workflows | These create or verify product Runs and evidence; they are product acceptance, not environment desired state. | Mutating acceptance |
| PostgreSQL and SeaweedFS Helm commands | Dormant rollback mechanism required by ADR 0002; forbidden while the corresponding Application is active. | Explicit stateful rollback |

No retained command imperatively deploys the sample SUT or control-plane
workloads. The stateful rollback commands are not concurrent reconcilers: normal
operation leaves their historical Helm metadata dormant while Argo CD owns the
live StatefulSets.

## Supported operating path

Use the high-level workflow for ordinary local operation:

```sh
task bootstrap
# Complete reviewed policy publication and control-plane prerequisite stages.
task status
task smoke
```

`task bootstrap` may create the named cluster and performs bounded external
credential and state initialization. `task status` never applies desired state
and requires the complete platform to be healthy; its object-store check creates
a bounded diagnostic Job retained for inspection. `task smoke` creates a retained
end-to-end Run only after status succeeds. Use the underlying Python commands for
previews, focused diagnosis and partial recovery.

After bootstrap, committed desired-state changes reconcile automatically. There
is no `gitops.py deploy` action. Roll back stateless workload or release changes
through a reviewed Git revert. Follow ADR 0002's orphan-and-Helm procedure only
for an explicit PostgreSQL or SeaweedFS ownership rollback.

## Deliberately deferred work

These items do not block the completed local migration:

- automated pruning and a retained-resource decommissioning policy;
- production topology, promotion and disaster recovery;
- external secret management and credential rotation;
- PostgreSQL and object-storage backup and restore;
- Argo CD SSO, high availability and capacity planning;
- deciding whether reusable local infrastructure becomes a separate project;
- reducing `perfeng-environment` after that repository-boundary decision; and
- broader operator telemetry tracked by M05 — Operator Visibility and
  Diagnostics.

The next planning step is to evaluate the infrastructure-project split against
the retained-boundary inventory above. That evaluation must not move
performance-product behavior, cross-repository contracts or environment-specific
release selection into a generic infrastructure repository by accident.
