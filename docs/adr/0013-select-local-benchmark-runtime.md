# ADR-0013: Select the dedicated local Kubernetes benchmark runtime

- Status: Proposed
- Date: 2026-10-15
- Milestone: M04 — Kubernetes Benchmark Decision Loop

## TL;DR

- Select the promoted integration bundle and corrected registry-scoped control plane.
- Retain per-entry contracts versions; do not relabel checkout artifacts as 0.22.0.
- Isolate reviewed benchmark controller authority in a separate Argo Project.
- Require an explicit migration pause and manual control-plane activation.
- Provision execution pull credentials independently and never rotate them implicitly.

## Context

The contained benchmark requires current admission metrics, durable exclusive
target reservations, cleanup barriers, exact environment bytes and three private
execution packages. Existing environment selection predates those capabilities.
Cleanup migration 0018 requires stopped old workers. Default rolling deployment
and merge-triggered activation cannot establish that boundary.

## Decision

Select bundle `local-kind-integration/1.0.0` from the pinned k8s revision and the
corrected control-plane image/source recorded by the deployment lock. Use explicit
registry mode and require the exact entry-version set {0.13.0, 0.22.0}; preserve
checkout semantics. Mount the environment descriptor and both execution templates
in the immutable content-addressed registry ConfigMap.

Grant the local controller only the API operations needed by the implemented
admission, per-Run authority and cleanup protocol. A distinct AppProject owns the
four static RBAC objects. Do not expand the general workload or metrics Project.
No Secret, namespace-delete, token issuance, bind/escalate or wildcard resource/verb
permission is added. Namespace and Pod creation nevertheless make this high-trust
authority suitable only for the explicitly approved disposable kind cluster.

The migration runtime grant adds SELECT/INSERT/UPDATE on benchmark reservations,
never DELETE. M03 native acquisition and authentication remain disabled.

Before migrations, explicitly pause the root then child, stop the owned control
plane and verify quiescence. Keep automatic sync disabled on the desired child.
After preparation and merge, resume the root and manually sync the full child.
Re-enabling automatic sync requires separate review and operational acceptance.

Create/verify `perfeng-execution-registry` only through an explicit operator command.
Verify all selected execution-package digests first. Existing owned credentials
remain unchanged; missing credentials come from the process environment through
private stdin, not another Secret or a default ServiceAccount.

## Alternatives considered

- General cluster-admin or a widened workload Project: unnecessary authority and
  unclear ownership; rejected.
- Prefix-contained ordinary RBAC: create operations cannot enforce this boundary.
  Admission-policy enforcement is required before any shared/cloud deployment.
- Automatic rolling migration/activation: old workers can race new cleanup state;
  rejected for this migration-bearing release.
- Uniformly bump checkout contracts: changes artifact meaning without evidence;
  rejected in favor of per-entry registry semantics.

## Consequences, compatibility and operations

The existing k6 path remains available after cutover. Local downtime and one manual
sync are deliberate. The pause changes no data or credentials; a failed phase leaves
reconcilers paused for inspection. Existing credential identities, ConfigMaps, PVCs,
policy objects and per-Run cleanup tombstones are retained.

Controller compromise could compromise the whole disposable cluster. Lack of
explicit Secret API access does not make arbitrary Pod creation a safe tenant
boundary. No production/shared/cloud safety claim is made.

Schema migration is not undone by reverting Git. Do not resume a pre-cleanup worker
against migrated state. Use reviewed forward repair or a coordinated backup restore.
This PR selects deployment, not completion of M04 decision-loop acceptance or M03.

## Validation

Offline regression tests cover exact versions/mounts, RBAC expansion, wrong cluster,
immutable credentials, denied manifest access and quiescence-before-write behavior.
Run `make verify`, `make validate` and `make release-selection-check`. Live gates are
documented in [the deployment guide](../kubernetes-runtime-deployment.md).

References: [Kubernetes RBAC delegation and create limitations](https://kubernetes.io/docs/reference/access-authn-authz/rbac/),
[RBAC security guidance](https://kubernetes.io/docs/concepts/security/rbac-good-practices/),
and [Argo CD automatic-sync controls](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/).
