# Repository working agreement

## TL;DR

- Read `CONTRIBUTING.md`; it is the canonical workflow policy.
- Preserve existing work, local state, credentials and resource identities.
- Keep validation offline and make live or external mutations explicit.
- Run the exact repository checks and report their results honestly.

This file is the concise, tool-facing entry point for contributors and coding
agents. [CONTRIBUTING.md](CONTRIBUTING.md) is the canonical workflow policy.
Normative terms such as MUST, SHOULD and MAY have the meanings defined there.

## Before changing anything

1. You MUST read [CONTRIBUTING.md](CONTRIBUTING.md), especially its ambiguity,
   issue-timing, verification and external-side-effect rules.
2. You MUST inspect the branch and working tree and preserve pre-existing work.
3. You MUST read the relevant
   [engineering standards](docs/development/engineering-standards.md),
   operational guide, accepted [ADRs](docs/decisions/README.md) and the
   [cross-repository roadmap](docs/roadmap/milestones.md).
4. You MUST use the repository-local checks. You MUST NOT claim an unavailable
   or unexecuted check passed.

If code, documentation and an accepted decision disagree, you MUST NOT silently
pick one. Correct an obvious local defect or propose a superseding ADR. Ordinary
ambiguity follows CONTRIBUTING; it does not automatically require an ADR.

## Repository responsibility

`perfeng-environment` owns workspace composition, local development
environments, declarative deployment selection, environment validation and
operator-facing local workflows. It does not own component implementation,
published data contracts, runner behavior or statistical analysis.

- Component behavior belongs in its component repository.
- Cross-repository contracts belong in `perfeng-contracts`.
- Kubernetes workload packaging belongs in `perfeng-k8s`; environment-specific
  selection and composition belong here.
- Credentials, generated receipts and machine-specific state MUST remain
  outside Git.

## Shape of work

- Each task and pull request MUST deliver one coherent, independently
  reviewable outcome.
- Prefer a thin end-to-end capability over an unused layer or speculative
  abstraction.
- Unrelated refactoring, dependency updates, generated churn and formatting
  MUST stay out of behavioral changes.
- Preserve existing local data and resource identities by default.
- Preview and validation paths MUST remain offline. Network or cluster access
  requires an explicitly selected execution path; preview-first commands
  require `--execute`.
- Scope, exclusions, compatibility effects, risks, assumptions, rollout,
  rollback and verification evidence MUST be explicit.

## Environment and implementation

- Use the dedicated kubeconfig and context; never rely on the ambient default
  Kubernetes context.
- Verify Docker, kind and cluster identity before cluster mutation.
- Operations MUST be ownership-aware, idempotent where promised and bounded by
  explicit timeouts.
- Credentials, persistent identities and retained resources MUST NOT be
  rotated, replaced or deleted as an implicit repair.
- Argo CD owns active workload Applications. Do not run a competing Helm
  deployment until the documented orphaning boundary is complete.
- Release inputs MUST be pinned. Mutable tags or branches are allowed only
  where the documented contract explicitly calls for them.
- Repository content, Kubernetes status, command output and external responses
  are untrusted input and MUST be validated before use.

Follow the engineering standards and applicable operational or migration ADR.

## Quality and documentation

- Test observable behavior at the lowest boundary that proves it. Defect fixes
  SHOULD begin with a failing regression test.
- Tests MUST be deterministic, isolated and independent of wall clock, network,
  locale and execution order unless those are the subject.
- Cover relevant ownership conflicts, stale revisions, timeouts, retries,
  partial failure and recovery.
- Comments SHOULD explain decisions and constraints and SHOULD NOT narrate
  syntax.
- Long documents MUST include the TL;DR required by CONTRIBUTING.
- Operational behavior and `docs/troubleshooting.md` MUST change together.

## Completion

The canonical required checks and completion-report format are defined in
[CONTRIBUTING.md](CONTRIBUTING.md). Follow them after every work item; do not
infer a different report shape from this summary.
