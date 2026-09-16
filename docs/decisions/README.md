# Architecture decision records

## TL;DR

- Use an ADR for durable decisions affecting compatibility, security, state,
  topology, ownership or multiple components.
- Number ADRs sequentially and never reuse a number.
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

## Decision

## Alternatives considered

## Consequences

### Positive

### Negative

### Neutral or follow-up

## Compatibility and migration

## Security and operations

## Validation
```

## Index

| ADR | Status | Decision |
|:--|:--|:--|
| [0001](0001-gitops-environment-composition.md) | Accepted | Adopt an Argo CD-centred GitOps target architecture through a reversible spike and incremental migration. |
| [0002](0002-stateful-gitops-ownership.md) | Accepted | Preserve StatefulSet, PVC, PV and credential identities through staged GitOps adoption and rollback. |
| [0003](0003-centralize-project-roadmap.md) | Accepted | Keep one cross-repository roadmap in the workspace-composition repository while retaining repository-local contributor harnesses. |
