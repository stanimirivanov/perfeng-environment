# Contributing to perfeng-environment

## TL;DR

- Deliver one coherent, verified outcome per issue and pull request.
- Use exact published milestone titles in the form `MNN — Outcome`.
- Preserve local data, credentials and resource identities by default.
- Keep preview and validation commands offline; require an explicitly selected
  execution path for live operations.
- Follow the engineering, ADR and operational sources of truth below.
- Include tests, documentation and non-secret verification evidence.
- At task completion, print the repository, milestone and proposed GitHub issue;
  do not store task-specific handoff text in the repository.

## Sources of truth

| Concern | Source |
|:--|:--|
| Tool-facing repository rules | [AGENTS.md](AGENTS.md) |
| Engineering and Python/GitOps standards | [docs/development/engineering-standards.md](docs/development/engineering-standards.md) |
| Architecture decisions | [docs/decisions](docs/decisions/README.md) |
| Cross-repository milestones and planned work | [docs/roadmap/milestones.md](docs/roadmap/milestones.md) |
| Supported commands and layout | [README.md](README.md) |
| Failure diagnosis and recovery | [docs/troubleshooting.md](docs/troubleshooting.md) |

Durable principles belong in this harness. Tool versions, environment
variables, manifest identities and command details belong beside the
implementation they operate.

## Before starting

- Confirm the target repository, milestone and smallest observable outcome.
- Read the relevant operational guide and accepted ADRs.
- Check the branch and working tree. Preserve unrelated changes.
- Identify affected persistent resources, credentials, contracts, manifests,
  troubleshooting paths and rollback behavior.
- Stop and propose an ADR when a change affects compatibility, durability,
  security, repository responsibility, deployment topology or multiple
  components.

## Milestones and roadmap

Milestone titles use the existing project convention:

```text
MNN — Short outcome
```

Do not reuse, renumber or casually rename a published milestone. The text after
the number states the capability available when the milestone completes.

This repository owns the single checked-in cross-repository roadmap because it
owns workspace composition. Component repositories link to it rather than
copying it. GitHub remains authoritative for live issue assignment, labels,
assignees and milestone state.

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

- Important choices, constraints, compatibility effects and ADR links.

## Acceptance criteria

- [ ] Observable behavior and verification evidence.
- [ ] Relevant failure or negative behavior.
- [ ] Documentation and operational effects.

## Out of scope

- Explicit exclusions and deferred work.
```

Acceptance criteria describe observable behavior or verifiable invariants, not
activities such as “write tests.” Record limitations rather than hiding them.

## Pull-request-sized work

A pull request should:

- solve one problem or deliver one coherent vertical capability;
- leave the repository valid and the supported environment recoverable;
- include implementation, tests, documentation and migration needed for that
  capability;
- preserve existing state unless replacement is the reviewed purpose;
- contain expected and important failure evidence; and
- be understandable without an unmerged speculative follow-up.

Split independent behavior, broad cleanup, dependency upgrades, schema redesign
and unrelated formatting. A vertical change may legitimately touch scripts,
manifests, tests and operational documentation together.

## Development workflow

1. Start from clean, current `main`.
2. Prepare one PR-sized change without staging, committing or pushing on behalf
   of the repository owner.
3. Print the proposed repository, milestone and GitHub issue at completion.
4. The owner creates the issue and a branch containing its number.
5. The owner may adjust or format the prepared change.
6. Run the complete checked-in verification suite.
7. Commit with a subject containing `[#N]`, push and open a pull request.

Do not store task-specific issue bodies, PR descriptions, branch instructions
or handoff notes in repository documentation.

## Required checks

Run the same checks used by CI:

```sh
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked ty check
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/workspace.py validate
uv run --locked python scripts/gitops.py validate
```

Run affected offline renderers and validators as well. Tests use temporary paths
and mocked external operations unless the pull request explicitly introduces a
live integration test. Live evidence never includes Secret data.

## Documentation

Every long document contains a `## TL;DR` immediately after its title and any
status metadata. A document is long when any of these conditions applies:

- it contains 800 or more words;
- it contains more than five second-level sections; or
- it is an architecture, security, end-to-end workflow, migration or
  operational guide readers will consult selectively.

The TL;DR states purpose, intended reader, important outcome and next action in
three to seven short bullets where practical. It does not replace safety
constraints.

Operational changes update `docs/troubleshooting.md` when they add or change a
supported command, prerequisite, healthy signal, failure classification or
recovery path. Diagnostic commands default to read-only behavior, use the
dedicated kubeconfig and label mutations or destructive actions explicitly.

Accepted ADRs are historical records. Supersede rather than rewriting their
decision or consequences.

## Pull request description

A pull request states:

- linked issue and milestone;
- problem, outcome, boundaries and deliberate exclusions;
- design and compatibility decisions;
- verification commands and non-secret evidence;
- persistent-resource, security, rollout, rollback and recovery effects; and
- known limitations and follow-up work.

## Review checklist

- [ ] The issue uses the required structure and exact milestone title.
- [ ] The change is one coherent, independently mergeable outcome.
- [ ] Repository and resource ownership boundaries remain explicit.
- [ ] Preview and validation paths are offline and side-effect-free.
- [ ] Mutating operations verify the dedicated environment and have bounds.
- [ ] Persistent identities, credentials and retained data are preserved or
      their migration is explicitly accepted.
- [ ] Tests cover observable success and relevant failures.
- [ ] Long documentation has a useful TL;DR.
- [ ] Operational and troubleshooting documentation is current.
- [ ] Formatting, linting, type checking, tests and validators pass.
- [ ] No secrets, local receipts, machine-specific paths or unrelated changes
      are included.
- [ ] An ADR exists when the decision meets the ADR threshold.

## Completion report

After completing a work item, print the repository, exact milestone, proposed
GitHub issue title and complete issue body. Also report verification,
limitations and a suggested `[#N]` commit subject.
