# Architecture decision records

## TL;DR

- Use an ADR for durable decisions affecting compatibility, security, state,
  topology, ownership or multiple components.
- Number ADRs sequentially and never reuse a number.
- Allocate a number from fresh repository state; renumber after a concurrent
  collision instead of forcing a merge.
- Accepted ADRs are historical records; supersede rather than rewrite them.
- Record alternatives, consequences, rollout and validation.

## When an ADR is required

Create an ADR when a decision materially affects one or more of:

- public contracts or compatibility;
- persistence, durability, resource ownership or migration strategy;
- security, credentials, authorization or trust boundaries;
- repository, service or deployment topology;
- environment composition or reconciliation ownership;
- cross-component operational behavior; or
- a constraint future contributors might otherwise simplify away.

Local implementation choices that are cheap to reverse do not need an ADR.

## Naming and lifecycle

Use a four-digit sequence and a short kebab-case name:

```text
0003-centralize-project-roadmap.md
```

Statuses are Proposed, Accepted, Rejected, Deprecated or Superseded. A
superseded ADR links to its replacement. Do not edit the decision or
consequences of an accepted ADR to make history appear cleaner; add a dated note
or a new ADR.

Add every ADR to the index below. Immediately before creating an ADR, refresh
the branch as the current workflow allows and inspect both this index and the
decision directory. Allocate the lowest unused four-digit number from that
state. The filename does not reserve the number outside the proposed change.

If concurrent work uses the same number, refresh the branch, rename the later
ADR to the next available number, and update its title, index entry and
references. Treat this as renumbering, not a content merge. Accepted or
published ADR numbers remain immutable.

## Template

```markdown
# ADR-NNNN: Decision title

- Status: Proposed
- Date: YYYY-MM-DD
- Milestone: MNN — Outcome
- Deciders:
- Supersedes:
- Superseded by:

## Context

What forces a decision now? Include constraints and quality attributes.

## Decision

State the decision and its scope precisely.

## Alternatives considered

### Alternative

- Benefits:
- Costs and risks:
- Reason not selected:

## Consequences

### Positive

### Negative

### Neutral or follow-up

## Compatibility and migration

## Security and operations

## Validation

How will the assumptions and consequences be verified?
```

## Index

| ADR | Status | Decision |
|:--|:--|:--|
| [0001](0001-gitops-environment-composition.md) | Accepted | Adopt an Argo CD-centred GitOps target architecture through a reversible spike and incremental migration. |
| [0002](0002-stateful-gitops-ownership.md) | Accepted | Preserve StatefulSet, PVC, PV and credential identities through staged GitOps adoption and rollback. |
| [0003](0003-centralize-project-roadmap.md) | Accepted | Keep one cross-repository roadmap in the workspace-composition repository while retaining repository-local contributor harnesses. |
| [0004](0004-separate-kubernetes-infrastructure.md) | Accepted | Separate reusable Kubernetes substrates and GitOps bootstrap from Perfeng-specific environment composition. |
| [0005](0005-adopt-k8s-infrastructure-name.md) | Accepted | Adopt the created `k8s-infrastructure` name and separate workspace membership from GitOps source authorization. |
| [0006](0006-loopback-native-worker-connectivity.md) | Proposed | Use an opt-in loopback HTTPS gateway and controlled target for native Windows integration. |
| [0007](0007-package-controlled-browser-candidate.md) | Proposed | Give the controlled fixture an honest immutable candidate image identity without prematurely promoting or deploying it. |
| [0008](0008-separate-four-core-4k-browser-cohort.md) | Proposed | Define a separate explicitly selected four-core 4K laptop cohort without relaxing OS or calibration gates. |
| [0009](0009-select-image-backed-native-target.md) | Proposed | Select the image-backed target declaratively, reuse the owned pull identity and proxy only to its verified Pod without promoting native execution. |
| [0010](0010-manage-native-connectivity-workflow.md) | Proposed | Acquire a reviewed serving binary, diagnose prerequisites and supervise one bounded foreground native session without implicit trust or state repair. |
| [0011](0011-qualify-local-kubernetes-benchmarks.md) | Proposed | Qualify an explicitly integration-only local Kubernetes cohort through guarded reads and immutable receipts without changing infrastructure. |
| [0012](0012-select-verified-resource-metrics.md) | Proposed | Select verified-TLS resource metrics in a separate restricted GitOps project, retaining explicit provider certificate operations and independent benchmark qualification. |
| [0013](0013-select-local-benchmark-runtime.md) | Proposed | Select the mixed-contract benchmark runtime with dedicated-cluster authority, independent pull credentials and an explicit quiescent migration/manual activation boundary. |
| [0014](0014-separate-release-and-run-orchestration.md) | Proposed | Assign finite release operations to Argo Workflows while retaining Argo CD workload reconciliation and control-plane performance Run ownership. |
| [0015](0015-bind-releases-to-reviewed-inputs.md) | Proposed | Bind each release request to an exact environment Git commit, deployment-lock digests, cluster incarnation and compare-and-swap promotion precondition. |
| [0016](0016-compose-versioned-release-templates.md) | Proposed | Compose external-state preparation from versioned namespaced WorkflowTemplates and an immutable script ConfigMap while preserving the v1 parent contract. |
| [0017](0017-record-explicit-local-release-adoption.md) | Proposed | Isolate immutable local adoption receipts and a create-only initial current pointer, keeping adoption distinct from release promotion and cluster recovery. |
| [0018](0018-bound-and-retire-release-verifier-sources.md) | Proposed | Consolidate staged Python in a bounded immutable package and permit audited source retirement while retaining installed versions. |
| [0019](0019-use-ci-identity-for-registry-parity.md) | Proposed | Use the ephemeral repository CI identity with package-read permission for disposable registry manifest-access parity. |
| [0020](0020-publish-disposable-ci-release-requests.md) | Proposed | Publish a run-owned temporary Git request in trusted main CI to accept the unchanged complete preflight against an actual disposable cluster UID. |
| [0021](0021-authorize-trusted-main-external-state-parity.md) | Proposed | Combine existing trusted-main Git publication with owner-approved package-read authority for complete external-state parent parity. |
| [0022](0022-use-pinned-source-for-server-parity.md) | Proposed | Use the existing contents-read credential only for a pinned private chart/package checkout in trusted-main server parity, keeping it out of the cluster. |
| [0023](0023-authorize-disposable-argocd-source-access.md) | Proposed | Extend source-token use to a pinned infrastructure checkout and an exact-URL repository Secret for actual Argo CD reconciliation in disposable trusted-main CI. |
| [0024](0024-authorize-environment-source-for-foundation-parity.md) | Proposed | Extend disposable Argo CD read-token configuration to the exact environment repository URL and accept PostgreSQL Application adoption with retained database/storage state. |
| [0025](0025-bound-disposable-root-reconciliation.md) | Proposed | Reconcile selected root-owned objects in disposable CI with automatic sync disabled and six non-control-plane children skipped, preserving workloads and data. |
| [0026](0026-isolate-disposable-adoption-recovery-scenarios.md) | Proposed | Isolate initial and receipt-only trusted-main acceptance with distinct request refs/artifacts and native quota failure followed by guarded, history-preserving recovery. |
| [0027](0027-inject-disposable-adoption-receipt-identity-faults.md) | Proposed | After retention proof, inject owner-approved receipt deletion/recreation only in disposable CI and require complete-parent refusal without pointer repair. |
| [0028](0028-publish-adoption-request-boundary-fixtures.md) | Proposed | Publish two approved additional CI requests in the same owned commit/ref and prove complete adoption refuses changed retry identity and upgrade intent. |
| [0029](0029-require-patched-native-gateway-toolchain.md) | Proposed | Require owner-approved Go 1.27.3 throughout the gateway boundary and reject old-toolchain setup/start without relabeling the existing selected asset. |
| [0030](0030-inject-disposable-ledger-response-loss.md) | Proposed | Drop one committed native receipt response in disposable CI through an approved TLS proxy; require direct read/retry/retention proof without changing production templates. |
| [0031](0031-exercise-complete-adoption-response-loss.md) | Proposed | Extend the approved proxy to a third trusted-main scenario; bind complete-parent receipt response loss to unchanged-parent pointer-only recovery. |
| [0032](0032-exercise-pointer-create-response-loss.md) | Proposed | Add an isolated pointer-response-loss scenario; require a fresh unchanged parent to reuse both ledger records without rewriting either. |
| [0033](0033-exercise-initial-receipt-create-conflict.md) | Proposed | Add isolated initial receipt-create conflict acceptance; retain the winner and recover with a fresh parent creating only the pointer. |
| [0034](0034-bound-disposable-acceptance-fixture-lifetimes.md) | Proposed | Use the owner-approved 60-minute disposable acceptance budget with aligned observer lifetime, unchanged readiness deadlines and sanitized startup diagnostics. |
| [0035](0035-exercise-initial-pointer-create-conflict.md) | Proposed | Add isolated initial pointer-create conflict acceptance; preserve the real receipt and winning pointer, then require unchanged-parent reuse without either create. |
| [0036](0036-tolerate-root-reconciliation-bookkeeping.md) | Proposed | Validate both root samples and tolerate only generation/resourceVersion/reconciledAt bookkeeping, with immutable v3 sources and audited v2 retirement. |
| [0037](0037-stage-guarded-maintenance-pause.md) | Proposed | Stage three guarded native maintenance writes for an already adopted baseline, preserving read-only checkpoint authority and requiring separate live cutover acceptance. |
| [0038](0038-exercise-maintenance-pause-and-checked-recovery.md) | Proposed | Exercise the complete pause in the existing initial leg, then verify manual recovery and exactly one Pod replacement before advancing the CI retention baseline. |
| [0039](0039-exercise-maintenance-stale-guard-conflicts.md) | Proposed | Prove stale root/child/Deployment native patches refuse after guarded fixture metadata changes, preserving the checked recovered baseline. |
| [0040](0040-exercise-maintenance-partial-pause.md) | Proposed | Stop CI-only parents after the native root/child patches and prove no later write/checkpoint or retained-state change. |
| [0041](0041-exercise-deployment-pause-interruption.md) | Proposed | Stop after the Deployment pause, prove independent quiescence, and verify one additional server Pod replacement before advancing the checked CI baseline. |
| [0042](0042-exercise-lingering-pod-checkpoint-refusal.md) | Proposed | Prove gated matching-Pod refusal, conditional fixture cleanup and a fresh independent checkpoint before existing checked recovery. |
| [0043](0043-exercise-maintenance-write-denial.md) | Proposed | Narrow each existing named PATCH grant, prove complete-parent Forbidden refusal and retained state, then conditionally restore exact original Role rules. |
| [0044](0044-exercise-maintenance-patch-response-loss.md) | Proposed | Prove one committed guarded Deployment PATCH loses its response, then require direct paused-state evidence, independent checkpoint and one checked recovery replacement. |
