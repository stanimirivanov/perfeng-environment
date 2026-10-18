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
