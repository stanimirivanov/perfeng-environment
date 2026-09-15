# ADR 0002: Preserve stateful identities during GitOps ownership migration

- Status: Accepted
- Date: 2026-09-15
- Owners: Performance platform maintainers

## TL;DR

- Move PostgreSQL first and SeaweedFS second; never change both ownership
  boundaries in one pull request.
- Argo CD must adopt the existing chart, release, StatefulSet, Service, Secret
  references and PVC identity without recreating any of them.
- Keep automated pruning and stateful self-healing disabled during adoption.
  Enable self-healing only in a later reviewed change after persistence and
  rollback acceptance passes.
- Record StatefulSet, PVC, PV and Secret UIDs before adoption and require them to
  remain unchanged through adoption, Pod restart and rollback.
- Keep credentials and PVCs outside direct Argo CD ownership. Preserve the
  existing `Retain` policy and never use namespace or cluster deletion as
  rollback.
- Roll back by reverting Git, orphaning the child Application, and reasserting
  the unchanged Helm release before checking the same persistence probe.

## Context

[ADR 0001](0001-gitops-environment-composition.md) moves environment
composition to Argo CD incrementally. The stateless sample SUT and control-plane
workloads now reconcile through child Applications. PostgreSQL and SeaweedFS
remain managed by preview-first Helm commands because an incorrect ownership
transition could preserve a healthy-looking Pod while replacing or detaching
the volume that contains its data.

Both local charts already use one-replica StatefulSets and
`persistentVolumeClaimRetentionPolicy` with `Retain` for deletion and scaling.
Their generated claims are bound through kind's `standard` local-path
StorageClass. Those controls reduce accidental deletion, but they are not a
backup and do not prove that an ownership transition reuses the same Kubernetes
objects and backing data.

The migration therefore needs an explicit contract and acceptance sequence
before either stateful Application is added to the active root Kustomization.

## Decision

### Migrate one service at a time

PostgreSQL moves first. It renders only a StatefulSet and headless Service, has
an established authenticated SQL persistence probe, and is easier to diagnose
than the two-container SeaweedFS workload. SeaweedFS moves only after the
PostgreSQL adoption and rollback sequence has passed.

Each service uses separate pull requests for:

1. adding its child Application with pruning and self-healing disabled;
2. recording live adoption, restart-persistence and rollback acceptance;
3. enabling stateful self-healing after the rollback path is proven.

The migration must never introduce a second StatefulSet, a second PVC, or a new
release name as a way to avoid adoption. Copying data into a replacement volume
is a separate data-migration design and is outside this decision.

### Preserve the complete resource identity

The first adoption uses the same repository-owned chart and the same Helm
release name. The following fields are non-replaceable migration invariants:

| Property | PostgreSQL | SeaweedFS |
|---|---|---|
| Namespace | `perf-platform` | `perf-platform` |
| Chart path | `charts/postgres` | `charts/seaweedfs` |
| Release name | `postgres` | `seaweedfs` |
| StatefulSet | `postgres` | `seaweedfs` |
| Headless Service | `postgres` | `seaweedfs` |
| Claim template | `data` | `data` |
| Bound PVC | `data-postgres-0` | `data-seaweedfs-0` |
| Storage class | `standard` | `standard` |
| Access mode | `ReadWriteOnce` | `ReadWriteOnce` |
| Requested storage | `2Gi` | `5Gi` |
| Deletion/scale retention | `Retain` / `Retain` | `Retain` / `Retain` |
| Workload credential | `perfeng-postgres-auth` | `perfeng-s3-server-auth-v3` |

Selectors, Pod labels, `serviceName`, container volume mounts and credential
references are part of the same contract. A proposed chart diff that changes an
immutable StatefulSet field or derived claim name blocks migration. It must not
be accepted by deleting and recreating the StatefulSet.

The existing Secrets remain separately provisioned immutable resources. Argo CD
references them but does not create, update, prune or rotate them. The generated
PVC remains controlled by the StatefulSet and its retention policy; no standalone
PVC manifest is added to the Application.

### Use conservative Argo CD policy during adoption

The initial stateful child Application uses:

- the exact existing chart path and release name;
- destination namespace `perf-platform`;
- `CreateNamespace=false`;
- automated synchronization enabled for the reviewed adoption;
- `prune=false`;
- `selfHeal=false`.

Automated synchronization lets the reviewed Git merge perform the ownership
transition. Disabled pruning prevents removal of live resources that are absent
from a revised chart. Disabled self-healing prevents Argo CD from immediately
reasserting a disputed state while the first adoption is being inspected.

This is a temporary safety posture, not the final operating model. A later pull
request may enable self-healing for one stateful Application only after that
service's persistence and rollback acceptance is recorded. Pruning remains
disabled until a separate retention and decommissioning decision exists.

Helm must not deploy or upgrade a service while its child Application is active.
Two reconcilers writing the same StatefulSet would make drift and rollback
ambiguous even when their rendered manifests currently match.

## Required acceptance sequence

### 1. Establish a recoverable starting point

Before merging an adoption:

- require the service health check to pass;
- take and verify an external backup when the local data matters;
- create the existing documented persistence probe;
- record the StatefulSet UID and generation;
- record the PVC UID, bound PV name, storage class, access mode and requested
  size;
- record the referenced credential Secret UID without reading its data;
- render and validate the chart offline;
- confirm the proposed Application has pruning and self-healing disabled.

UIDs are observation evidence, not desired configuration and must not be
committed to Git. Store them in the issue or pull-request acceptance record after
checking that no sensitive value is present.

### 2. Adopt without replacement

Merge the child Application and allow the root Application to create it. The
child must become `Synced` and `Healthy` without deleting the StatefulSet. Then
require:

- unchanged StatefulSet UID;
- unchanged PVC UID and bound PV name;
- unchanged Secret UID;
- unchanged non-replaceable fields from the identity table;
- successful service health;
- successful read of the pre-adoption persistence probe.

A Pod restart caused by an ordinary template reconciliation is acceptable only
when the same PVC remains mounted and the probe survives. A StatefulSet or PVC
UID change is a failed migration even if readiness becomes green.

### 3. Prove restart persistence

Restart only the adopted StatefulSet, wait for readiness, and read the same
probe again. PostgreSQL uses the SQL fixture documented in
[Local PostgreSQL](../local-postgres.md). SeaweedFS uses one conditionally
created S3 probe and verifies its checksum with the printed probe ID as described
in [Local S3 artifact storage](../local-object-store.md).

Do not create a new probe after restart and call that persistence. The evidence
must have been written before adoption and read after restart.

### 4. Prove rollback without deleting data

Rollback is an explicit ordered operation:

1. merge a Git revert that removes the stateful child Application from the root
   Kustomization;
2. wait for the root Application to reconcile that change;
3. delete only the child Application with orphan propagation so its resources
   remain running;
4. verify the StatefulSet, PVC, PV and Secret UIDs are unchanged;
5. run the existing preview-first Helm deployment for that service to reassert
   the unchanged release;
6. run health, restart persistence and probe verification again.

Deleting the Application before the Git revert reaches `main` allows the root
Application to recreate it. Foreground cascading deletion, Helm uninstall,
namespace deletion, PVC deletion, `cluster.py down`, Docker storage cleanup and
manual finalizer removal are not rollback procedures.

### 5. Re-adopt deliberately

After a successful rollback rehearsal, reintroduce the same reviewed Application
through Git and repeat the unchanged-UID and persistence checks. Only then may a
separate pull request enable self-healing for that service.

## Automated preflight

`scripts/validate_cluster.py` renders both charts and fails unless their
StatefulSet names, Services, selectors, Pod labels, `serviceName`, single `data`
claim template, derived PVC names, storage class, access mode, requested size and
deletion/scale retention match this decision. It also verifies the external
credential references and that no standalone PVC or Secret is rendered.

The preflight is necessary but not sufficient. Rendering cannot prove that a
live API-server apply will retain object UIDs or data, so every ownership change
still requires the live acceptance sequence above.

## Stop conditions

Stop the migration and leave the current Helm ownership in place if:

- the rendered or live diff changes an immutable StatefulSet field;
- Argo CD proposes deletion, pruning or a replacement resource;
- the existing StatefulSet, PVC, PV or credential identity is unclear;
- any recorded UID or PV binding changes unexpectedly;
- the pre-adoption probe cannot be read after adoption or restart;
- rollback requires credential rotation, data copying, PVC recreation or
  namespace deletion;
- both Helm and Argo CD appear to be actively reconciling the service.

Do not weaken a stop condition to make the Application report healthy. Diagnose
the difference and revise or supersede this decision first.

## Consequences

The staged approach requires more review and two ownership transitions during a
rollback rehearsal. In return, it provides direct evidence that GitOps adoption
does not silently replace local data or credentials, and it keeps every step
reversible before stateful self-healing is enabled.

Old Helm release metadata remains during Argo CD ownership as rollback evidence.
It is not an active second reconciler unless a Helm command is run. Removing that
metadata, enabling pruning, automating backups, introducing production storage,
or defining data-retention expiry all require later decisions.

This decision does not make kind local-path storage durable beyond the kind node,
Docker storage or host. Existing backup limitations remain unchanged.

## Alternatives rejected

- **Recreate StatefulSets and reattach the PVCs:** unnecessary for identical
  resource names and too easy to get wrong; adoption must preserve the live
  object directly.
- **Create new PVCs and copy data:** this is a data migration, not an ownership
  migration, and needs separate integrity and rollback design.
- **Enable pruning and self-healing immediately:** this gives the new reconciler
  destructive authority before retention and rollback have been observed.
- **Move both services together:** a failure would combine database, object-store
  and reconciliation variables and make rollback harder to attribute.
- **Keep permanent dual Helm and Argo CD ownership:** multiple active writers make
  desired state and incident recovery ambiguous.

## References

- [ADR 0001: Use GitOps for environment composition](0001-gitops-environment-composition.md)
- [Local GitOps foundation](../local-gitops.md)
- [Local PostgreSQL](../local-postgres.md)
- [Local S3 artifact storage](../local-object-store.md)
- [Local platform troubleshooting](../troubleshooting.md)
- [Kubernetes StatefulSet PVC retention](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#persistentvolumeclaim-retention)
- [Argo CD automated synchronization](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/)
