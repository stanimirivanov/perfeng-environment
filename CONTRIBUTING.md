# Contributing to perfeng-environment

## TL;DR

- Deliver one coherent, verified outcome per issue and pull request.
- Use MUST, SHOULD and MAY as defined below; lowercase wording is explanatory.
- Proceed on documented, reversible assumptions and stop for choices that
  materially affect behavior, safety, compatibility, scope or external state.
- Follow the repository-owner workflow: prepare requested changes without
  staging or committing, then print the issue content for the owner.
- Run the exact repository checks. Report unavailable checks as not run with
  their reason and residual risk—never as passed.
- Preserve local data, credentials and resource identities by default.
- Use exact published milestone titles in the form `MNN — Outcome`.

## Policy language and sources of truth

The uppercase terms in this repository have these meanings:

- **MUST** and **MUST NOT** identify requirements. A deviation needs an explicit
  reviewer-approved exception recorded in the issue or pull request.
- **SHOULD** and **SHOULD NOT** identify strong defaults. A deviation is allowed
  only when its reason and trade-off are recorded.
- **MAY** identifies an optional choice.

Lowercase words such as “prefer,” “avoid” and “may” provide guidance and do not
silently create another normative level.

| Concern | Canonical source |
|:--|:--|
| Contributor workflow, issue timing, escalation and completion | This document |
| Concise contributor and agent entry point | [AGENTS.md](AGENTS.md) |
| Engineering and Python/GitOps standards | [docs/development/engineering-standards.md](docs/development/engineering-standards.md) |
| Architecture decisions | [docs/decisions](docs/decisions/README.md) |
| Cross-repository milestones and planned work | [docs/roadmap/milestones.md](docs/roadmap/milestones.md) |
| Supported commands and layout | [README.md](README.md) |
| Failure diagnosis and recovery | [docs/troubleshooting.md](docs/troubleshooting.md) |

When sources conflict, the narrower source governs its stated concern. An
accepted ADR governs its decision until superseded. A contributor MUST surface
an unresolved conflict rather than quietly choosing the convenient rule.

Durable principles belong in this harness. Tool versions, environment
variables, manifest identities and command details belong beside the
implementation they operate.

## Before starting

A contributor MUST:

- inspect the branch and working tree and preserve unrelated changes;
- read the relevant operational guide, engineering standards and accepted ADRs;
- identify the smallest independently reviewable observable outcome;
- identify affected persistent resources, credentials, contracts, manifests,
  troubleshooting paths, rollout and recovery behavior; and
- determine the applicable workflow under [Issue timing](#issue-timing).

A contributor MUST NOT discard, overwrite, reformat or incorporate unrelated
work merely to obtain a clean diff. Destructive operations and externally
visible writes outside the stated task require explicit authorization.

An ADR MUST be proposed when a change establishes or revises a durable decision
about compatibility, persistent state, security, repository responsibility,
deployment topology or cross-component behavior. Ordinary implementation
ambiguity does not require an ADR.

## Ambiguity and escalation

Before escalating, a contributor SHOULD inspect relevant code, tests, fixtures,
documentation, ADRs and issue history and perform safe read-only investigation.

A contributor MAY proceed with a documented assumption only when all of these
are true:

- the choice stays within the stated outcome and exclusions;
- it is local, reversible and inexpensive to change;
- it does not alter a public contract, persisted meaning, security boundary,
  external state or destructive behavior;
- it does not weaken acceptance criteria or required verification; and
- a reasonable alternative would not materially change what reviewers believe
  they are approving.

The assumption and consequence MUST be recorded in the pull request and
completion report. Prefer the smallest change that preserves future choices.

A contributor MUST stop and request a decision when missing information could
materially change:

- the observable outcome, acceptance criteria, milestone or scope;
- compatibility, data meaning or loss, security, authorization or privacy;
- an irreversible or destructive operation;
- production, third-party or other externally visible state;
- meaningful cost, operational ownership or deployment topology; or
- a choice between credible designs with materially different trade-offs.

The request MUST state the exact decision, evidence already checked, viable
options and consequences, and work that can safely continue. Agents MUST NOT
bury a material choice in a diff or halt on a trivial reversible choice.

## Issue timing

The repository-owner workflow deliberately separates implementation from
GitHub administration:

- When an issue number is supplied, the contributor MUST follow its scope and
  milestone. A material mismatch follows the escalation rules.
- When no issue number is supplied, the contributor prepares the smallest
  coherent requested change and prints a complete proposed issue in the
  completion report. The owner creates the issue and then creates or renames
  the branch so it contains the issue number.
- Contributors acting on behalf of the owner MUST NOT stage, commit, push,
  create or modify GitHub issues, create pull requests, or change milestones
  unless the owner explicitly requests that action.
- Read-only investigation and scoping MAY occur before an issue exists.

Creating an issue, milestone, pull request, release or other external record is
an externally visible write. It requires explicit authority even when the
proposed content is already present in the completion report.

## Milestones and roadmap

Milestone titles use the published project convention:

```text
MNN — Short outcome
```

Published milestones MUST NOT be reused, renumbered or casually renamed. Every
issue MUST name one exact milestone. GitHub is authoritative for live issue
assignment, labels, assignees and milestone state.

This repository owns the single checked-in cross-repository roadmap because it
owns workspace composition. Component repositories link to it instead of
copying it. Before allocating a new milestone number, refresh the GitHub
milestone list. Resolve a collision with the next available number and update
references; never force conflicting metadata through review.

## Required issue structure

Use an imperative, outcome-oriented title. Every implementation task uses:

```markdown
**Repository:** perfeng-environment
**Milestone:** MNN — Outcome

## Goal

Describe the problem and observable result.

## Scope

- Included behavior and boundaries.

## Design decisions

- Important choices, assumptions, constraints, compatibility effects and ADR
  links.

## Security and operations

- Trust boundaries, credentials, persistent resources, external side effects,
  rollout, recovery and rollback.

## Acceptance criteria

- [ ] Observable behavior and verification evidence.
- [ ] Relevant failure or negative behavior.
- [ ] Documentation and operational effects.

## Verification

- Required and focused checks, including expected live acceptance.

## Out of scope

- Explicit exclusions and deferred work.
```

Acceptance criteria MUST describe observable behavior or verifiable invariants,
not activities such as “write tests.” Known limitations MUST be visible.

## Pull-request-sized work

A pull request MUST:

- solve one problem or deliver one coherent vertical capability;
- leave the repository valid and the supported environment recoverable;
- include implementation, tests, documentation and migration required for that
  capability;
- preserve existing state unless replacement is the reviewed purpose;
- contain evidence for expected and important failure behavior; and
- be reviewable without an unmerged speculative follow-up.

Split independent behavior, broad cleanup, dependency upgrades, schema redesign
and unrelated formatting. A thin vertical change may legitimately touch
scripts, manifests, tests and operational documentation together. Placeholder
abstractions and future configuration MUST NOT be added merely to make a
roadmap appear implemented.

## Development workflow

1. Select the workflow defined in [Issue timing](#issue-timing).
2. Prepare one focused PR-sized change without staging, committing or pushing
   on behalf of the owner.
3. Add or update tests with the behavior.
4. Implement the smallest coherent solution and update documentation.
5. Format and run the exact repository checks.
6. Review the complete diff for secrets, generated churn, accidental contract
   changes, assumptions and unrelated edits.
7. Print the canonical [completion report](#completion-report).
8. The owner creates or confirms the issue and branch, may adjust the change,
   commits with `[#N]`, pushes and opens the pull request.

Do not store task-specific issue bodies, PR descriptions, branch instructions
or handoff notes in repository documentation.

## External operations and destructive actions

Read-only inspection is allowed when relevant to the task. Filesystem, cluster,
registry, GitHub and other external mutations MUST remain within the requested
scope and the authority granted by the owner.

- Preview and validation commands MUST be offline and side-effect-free.
- A live operation MUST use its explicit execution path, the dedicated
  kubeconfig and context, bounded timeouts and ownership checks.
- Credentials MUST NOT appear in arguments, logs, manifests, fixtures or Git.
- Credential rotation, persistent-resource replacement, pruning, deletion and
  repository history changes require explicit authorization and verified exact
  targets.
- A partial operation MUST retain enough non-secret evidence for safe diagnosis
  and retry.
- Live acceptance is run only when the issue requires it or the owner asks for
  it; successful unit tests do not imply live-cluster acceptance.

## Verification and constrained environments

A contributor MUST run every applicable required check supported by the current
environment. Narrow checks MAY provide interim feedback but MUST NOT be
presented as equivalent to the required suite.

```sh
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked ty check
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/workspace.py validate
uv run --locked python scripts/gitops.py validate
uv run --locked python scripts/validate_cluster.py
```

Run focused offline renderers or validators for every affected chart, lock,
profile or manifest.

When a required check cannot execute because of sandbox restrictions, network,
credentials, missing services or fixtures, platform limits or another
environmental constraint, the contributor MUST:

1. report the exact command or check as **not run**;
2. state the blocking condition and safe attempts made;
3. list evidence from checks that did run;
4. state residual risk and where the missing check should run; and
5. avoid weakening the check merely to make the current environment pass.

An unexecuted check MUST NOT be reported as passed. A failure MUST be reported
as failed even when it appears unrelated; identify a verified pre-existing
failure separately with its evidence.

## Documentation

Every long document MUST contain a `## TL;DR` section immediately after its
title and status metadata. A document is long when any of these applies:

- it contains 800 or more words;
- it contains more than five second-level sections; or
- it is an architecture, security, end-to-end workflow, migration or
  operational guide readers will consult selectively.

Issue and pull request templates are forms rather than narrative documents and
are excluded from this rule.

The TL;DR SHOULD state purpose, intended reader, important outcome and next
action in three to seven short bullets where practical. It does not replace
safety constraints.

Operational changes MUST update `docs/troubleshooting.md` when they change a
supported command, prerequisite, healthy signal, failure classification or
recovery path. Accepted ADRs are historical records; supersede rather than
rewriting their decision or consequences.

## Pull request description

A pull request MUST state:

- linked issue and milestone;
- problem, outcome, boundaries and deliberate exclusions;
- design decisions, assumptions and unresolved questions;
- verification commands and evidence, including every check not run;
- compatibility, security, persistent-resource, rollout, rollback and recovery
  effects; and
- known limitations and follow-up work.

## Review checklist

- [ ] MUST: The issue uses the required structure and exact milestone title.
- [ ] MUST: The change is one coherent, independently mergeable outcome.
- [ ] MUST: Material assumptions and unresolved questions are visible.
- [ ] MUST: Repository and resource ownership boundaries remain explicit.
- [ ] MUST: Preview and validation paths are offline and side-effect-free.
- [ ] MUST: Mutations verify the dedicated environment and are bounded.
- [ ] MUST: Persistent identities, credentials and retained data are preserved
      or their migration is explicitly accepted.
- [ ] MUST: Tests cover observable success and relevant failure behavior.
- [ ] MUST: Long documentation has a useful TL;DR.
- [ ] MUST: Operational and troubleshooting documentation is current.
- [ ] MUST: Applicable checks passed and unavailable checks are reported under
      the constrained-environment protocol.
- [ ] MUST: No secrets, local receipts, machine-specific paths or unrelated
      changes are included.
- [ ] MUST: An ADR exists when the decision meets the ADR threshold.

## Completion report

After every completed work item, the contributor MUST print:

1. the repository name;
2. the exact milestone in the form `MNN — Outcome`;
3. the proposed GitHub issue title;
4. the complete issue body from [Required issue structure](#required-issue-structure),
   matching the work actually performed;
5. assumptions, unresolved questions and limitations;
6. verification commands and outcomes, distinguishing **passed**, **failed**
   and **not run**; and
7. a suggested commit subject containing `[#N]`.

When an issue already exists, repeat or amend its title and body so the delivered
scope remains auditable. Printing the report does not publish external records.
