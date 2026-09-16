# ADR-0003: Centralize the Perfeng project roadmap

- Status: Accepted
- Date: 2026-09-16
- Milestone: M02 — Declarative Local Operations
- Deciders: Project owner
- Supersedes:
- Superseded by:

## TL;DR

- Keep one cross-repository roadmap in `perfeng-environment` for now.
- Keep contributor rules, commands, templates and ADRs local to every
  repository.
- Let GitHub own live issue state and the checked-in roadmap own intended
  outcomes and sequencing.
- Move roadmap ownership only through a superseding ADR if a dedicated
  governance or infrastructure repository is later justified.

## Context

Perfeng is a polyrepo platform. Each repository needs enough local guidance for
contributors and coding tools to work safely, but copying the complete project
roadmap into every repository would create conflicting status and priorities.

`perfeng-environment` already owns `workspace.json`, the multi-repository editor
workspace and environment composition. It is therefore the current natural
navigation point for work spanning repositories.

## Decision

Keep one authoritative checked-in cross-repository roadmap at
`perfeng-environment/docs/roadmap/milestones.md`.

Each component repository retains its own `AGENTS.md`, contributor workflow,
engineering rules, verification commands, ADRs and GitHub templates. Component
documentation links to the central roadmap; it does not copy milestone status
or cross-repository sequencing.

GitHub remains authoritative for live issue state, assignment, labels and
milestone membership. The checked-in roadmap owns intended outcomes,
dependencies and planned sequencing.

## Alternatives considered

### Copy the roadmap into every repository

- Benefits: every checkout contains all planning information.
- Costs and risks: duplicated status drifts immediately and makes priority
  changes ambiguous.
- Reason not selected: local discoverability does not justify multiple sources
  of planning truth.

### Put all contributor guidance in one central repository

- Benefits: one place to edit shared standards.
- Costs and risks: a standalone component checkout lacks executable local
  instructions; tools may not discover external rules; exact commands differ by
  language and repository.
- Reason not selected: shared principles do not replace repository-local
  operating contracts.

### Create a dedicated governance repository now

- Benefits: roadmap and shared policy are independent from deployment tooling.
- Costs and risks: adds another repository and migration before its ownership,
  automation and maintenance model are established.
- Reason not selected: defer until the planned environment/infrastructure split
  is evaluated.

## Consequences

### Positive

- There is one durable cross-repository plan.
- Every repository remains independently understandable and verifiable.
- Local rules can be tailored to Go, Python, TypeScript, contracts or
  Kubernetes without weakening shared principles.

### Negative

- Shared standards are initially maintained in several repositories.
- Contributors working in a component repository must follow a link for the
  complete platform roadmap.

### Neutral or follow-up

- A later governance or infrastructure repository may become the roadmap owner.
  That move requires a superseding ADR and link updates, not duplicated files.
- Automated consistency checks for common harness sections may be introduced
  after all repositories have adopted the structure.

## Compatibility and migration

Existing GitHub milestones and issue assignments are not renamed or recreated.
Repository harness changes are documentation and workflow changes; they do not
alter runtime or deployment contracts.

## Security and operations

The roadmap contains no credentials, private operational evidence or
machine-specific paths. Repository-local security and recovery instructions
remain next to the code and manifests they govern.

## Validation

- Only one repository contains the cross-repository milestone plan.
- Every component harness links to that plan after its migration.
- Issue and pull-request templates require an exact milestone.
- GitHub and the checked-in roadmap are reconciled when priorities or
  sequencing change.
