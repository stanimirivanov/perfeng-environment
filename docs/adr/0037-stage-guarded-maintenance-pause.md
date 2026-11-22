# ADR-0037: Stage guarded maintenance pause for an adopted baseline

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-26
- Milestone: M06 — Production Readiness
- Extends: ADR 0014 orchestration ownership; ADR 0018 verifier budget remains unchanged

## TL;DR

- Give a separate staged identity get/patch access to exactly two owned Applications and one owned Deployment.
- Require selected readiness and both existing adopted-baseline ledger records before writes.
- Use native Argo resource operations and atomic UID/resourceVersion/spec tests; add no production Python.
- Leave maintenance in place and return only independently observed quiescence; no automatic resume or migration.
- Keep local execution deferred and require disposable acceptance before operational cutover or script retirement.

## Decision

The owner approved a staged maintenance-pause workflow with patch authority only on `argocd/perfeng-local-root`, `argocd/perfeng-local-control-plane` and `perf-platform/control-plane`. Existing checkpoint authority stays read-only. Bootstrap owns Application and ledger read grants; the staged release Kustomization owns the new workflow identity, selected platform reads, named Deployment patch access and existing cluster-identity/executor capabilities. There are no new GitHub rights, Jobs, request refs or credential changes.

Reuse the unchanged immutable v3 readiness, adoption `finish` phase and quiescence verifiers. `finish` verifies existing receipt/current pointer and cannot authorize a missing record. This parent never references ledger create operations. Only an already adopted healthy initial baseline with the same exact request bytes is supported; promotion and upgrade requests remain separate.

Native GET projections retain the full child/Deployment snapshot with the readiness view. Fresh guards before each write require the verified full spec and UID, unchanged ownership metadata, no deletion and no active Application operation. Fixed JSON Patch operations test the fresh UID/resourceVersion/spec atomically, then disable root/child automatic sync or set replicas to zero. Refuse concurrent changes; do not retry writes through the workflow. Bounded read-only checkpoint retries observe eventual status/Pod termination without increasing write authority. The existing v1 checkpoint is the only successful final output.

## Security and operational consequences

RBAC restricts resource identities, not patch fields. Trusted submitters who can use the ServiceAccount can bypass template-level restrictions; production multi-user admission is unresolved. Ledger access is get only. Pod list access covers the namespace. No Secret content or token is supplied as a workflow argument; existing pinned Git acquisition and verified in-cluster transport remain in use.

The shared mutex serializes cooperating workflows only. Automatic sync being disabled leaves comparisons and independently authorized manual operations possible; operator exclusion and drain/backup prerequisites remain mandatory. There is no atomic transaction across the three writes. Partial failure leaves completed writes in place, produces no successful checkpoint and requires inspection and explicit existing M04 recovery. There is no automatic reversal or fresh-install path. A successful observation is not a durable lock, backup, drain, schema or migration authorization.

Native jq projections/JSON Patch replace orchestration responsibilities without extending the two-module/708-line Python budget or editing published templates. New v1 helper/parent names are immutable once merged. The [operating guide](../release-maintenance-pause.md) defines rollout, failure recovery and remaining controller/API acceptance. Installation is staged only, outside the active local root. Offline tests and renders do not replace disposable live proof; local acceptance stays deferred under the owner's earlier decision. Supported host commands remain until parity and a separate reviewed cutover.
