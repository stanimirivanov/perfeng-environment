# ADR-0036: Tolerate root reconciliation bookkeeping during adoption

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-25
- Milestone: M06 — Production Readiness
- Supersedes: ADR 0017 only for cross-sample root equality; ADR 0018 versioning and source-retirement rules remain in force

## TL;DR

- Validate both root observations and compare every field except three explicitly approved bookkeeping paths.
- Ignore only `metadata.generation`, `metadata.resourceVersion` and `status.reconciledAt` in that comparison.
- Publish v3 immutable verifier/caller identities; retain installed versions and append audited v2 source retirement.
- Keep ledger contracts, native operations, authorities, deadlines and the two-module/708-line budget.

## Evidence and cause

Completed [run 40227060836](https://github.com/stanimirivanov/perfeng-environment/actions/runs/40227060836) records `initial` failing at `finish` after both creates, and `receipt-only` failing at `pointer-plan` after receipt creation. Exact inputs replay through v2 reproduce `root changed during adoption; inspect and resubmit`. The only sample differences are generation, resourceVersion and reconciledAt. Normalizing only those paths makes both counterfactual replays pass with their original ledger records and requested create decisions.

The pinned Argo CD v3.5.2 [controller](https://github.com/argoproj/argo-cd/blob/v3.5.2/controller/appcontroller.go) refreshes `reconciledAt` during comparison and persists status through Application patches. Its [Application CRD](https://github.com/argoproj/argo-cd/blob/v3.5.2/manifests/crds/application-crd.yaml) has `subresources: {}`. Kubernetes [custom-resource update strategy](https://github.com/kubernetes/apiextensions-apiserver/blob/v0.35.0/pkg/registry/customresource/strategy.go) increments generation for non-metadata changes when status is not isolated. Generation therefore cannot be treated as a spec-only counter here. Whole-object equality makes successful adoption depend on controller refresh timing.

This is an implementation/compatibility mismatch with ordinary controller activity. It does not establish a need to change deployment technology or add orchestration wrappers.

## Decision

The owner approved narrowly scoped refresh tolerance. Apply existing root identity, ownership, source/project/destination, active-operation, healthy/synced, conditions and exact selected-revision validation to **both** samples. Build copies for comparison, removing only the three approved paths. Require complete equality of everything remaining, including UID, full spec, annotations/labels, operation state, sync comparison, health details, resource inventory and unknown fields. Preserve original input objects and evidence.

The excluded paths are not treated as stable semantic identity. Their values or presence alone cannot trigger drift refusal; native API validation remains responsible for API field types. Nonempty resourceVersion remains required by the existing identity predicate on each sample. This is a sampled consistency check, not a transaction or proof that no transient change occurred and reverted between reads. Operators must still exclude concurrent syncs and merges. Do not pause reconciliation, fabricate status, automatically retry until green or broadly ignore metadata/status.

## Versioning, rollout and recovery

Publish `perfeng-release-verifiers-v3` and new v3 versions of affected verification children and parents. The external-state v1 composition keeps its interface and selects v3 children. Native record/preparation/observation resources and all request/result/ledger schemas remain v1. Retire v2 files through append-only Git revision/hash records whose chains resolve to active v3 sources. Retain installed v1/v2 objects with pruning disabled and submit fresh v3 Workflows; never rewrite published ConfigMaps or resume old successful nodes as new verification.

No grants, credentials, CI scenarios/jobs/refs, deadlines, controllers or ledger operations change. Receipt/pointer bytes, UID binding and provenance stay exact. A failed adoption after creates still leaves records for inspected recovery. Rollback selects the prior staged Git revision and retained immutable objects without pruning or undoing ledger history. Active Python stays within the existing two modules and 708 physical lines; no original host-script retirement credit is claimed.

## Verification and limitations

Focused regressions must fail on v2 and pass on v3 for first, receipt-only and exact-retry planning. Verify unchanged inputs, exact output values, and refusals for semantic drift in either sample, invalid identity/ownership, unhealthy/out-of-sync roots, changed annotations, operations, resources and unknown fields. Replay both captured failing planner inputs without normalizing them. Run immutable-source lifecycle/budget checks, `make validate` and `make workflow-lint`.

Full adoption and fault-recovery proof remain the existing main-only post-merge disposable acceptance. Offline replay does not establish complete-parent live success, promotion, production recovery or retirement qualification.
