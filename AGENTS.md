# Repository working agreement

This file is the concise, tool-facing entry point for contributors and coding
agents. Detailed rationale lives in the linked guides.

## TL;DR

- Read contributor, engineering, operational and ADR sources before editing.
- Keep repository and runtime ownership boundaries explicit.
- Preserve state and credentials; make live operations deliberate and bounded.
- Run the complete checked-in verification suite.
- Print the repository, exact milestone and structured GitHub issue at
  completion; do not store task-specific handoff text here.

## Read before changing the repository

1. Read [CONTRIBUTING.md](CONTRIBUTING.md).
2. Read [engineering standards](docs/development/engineering-standards.md).
3. Read the relevant operational guide under `docs`.
4. Read accepted decisions in [docs/decisions](docs/decisions/README.md).
5. Check the [cross-repository roadmap](docs/roadmap/milestones.md) and identify
   the exact milestone for the work.

If implementation, documentation and an accepted decision disagree, do not
silently choose one. Correct an obvious local defect or propose a superseding
ADR when the decision itself must change.

## Repository responsibility

`perfeng-environment` owns workspace composition, local development
environments, declarative deployment selection, environment validation and
operator-facing local workflows. It does not own component implementation,
published data contracts, runner behavior or statistical analysis.

- Component source changes belong in their component repository.
- Cross-repository contracts belong in `perfeng-contracts`.
- Kubernetes workload packaging belongs in `perfeng-k8s`; environment-specific
  selection and composition belong here.
- Credentials, generated local receipts and machine-specific state stay outside
  Git.

## Shape of work

- Each task and pull request delivers one coherent, independently reviewable
  outcome.
- Prefer a thin end-to-end capability over an unused layer or speculative
  abstraction.
- Separate behavioral changes, dependency updates and broad cleanup unless
  they are inseparable for correctness.
- Preserve existing local data and resource identities by default.
- Keep preview and validation paths offline. Network or cluster access requires
  an explicitly selected execution path; preview-first commands require
  `--execute`.
- Make scope, exclusions, compatibility effects, risks, rollout, rollback and
  verification evidence explicit.

## Environment and implementation rules

- Use the dedicated kubeconfig and context; never rely on the ambient default
  Kubernetes context.
- Verify Docker, kind and cluster identity before any cluster mutation.
- Make operations ownership-aware, idempotent where promised and bounded by
  explicit timeouts.
- Never rotate credentials, replace persistent identities or delete retained
  resources as an implicit repair.
- Argo CD owns active workload Applications. Do not run a competing Helm
  deployment until the documented orphaning boundary has been completed.
- Pin release inputs. Do not replace reviewed revisions or digests with
  `latest`, `stable` or a mutable branch unless the contract explicitly calls
  for a branch.
- Keep secrets out of arguments, logs, generated manifests, fixtures and Git.
- Treat repository content, Kubernetes status, command output and external
  responses as untrusted input. Parse and validate before acting.

Follow [engineering standards](docs/development/engineering-standards.md) and
the applicable operational or migration ADR.

## Testing and documentation

- Test observable behavior at the lowest boundary that proves it.
- Defect fixes begin with a regression test that fails before the fix.
- Unit tests use temporary paths and mocked external operations. Live cluster
  acceptance is explicit and is not part of default unit tests.
- Cover relevant rejection, ownership, stale-revision, timeout, retry,
  partial-failure and recovery behavior.
- Comments explain decisions and constraints; they do not narrate syntax.
- Long documents include the TL;DR required by CONTRIBUTING.md.
- Update `docs/troubleshooting.md` whenever a supported command, prerequisite,
  healthy signal, failure classification or recovery path changes.
- Update code, tests, operational documentation and relevant ADRs in the same
  pull request.

## Required verification

Run the checked-in commands; do not invent weaker substitutes:

```sh
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked ty check
uv run --locked python -m unittest discover -s tests -v
uv run --locked python scripts/workspace.py validate
uv run --locked python scripts/gitops.py validate
```

Run focused offline renderers or validators for every affected chart, lock,
profile or manifest. Run live acceptance only when the issue requires it and
record the non-secret evidence.

## Required completion report

After completing each work item, report:

1. repository name;
2. milestone using the exact published `MNN — Outcome` title;
3. GitHub issue title;
4. issue body with Goal, Scope, Design decisions, Acceptance criteria and Out
   of scope;
5. verification performed and remaining limitations; and
6. a suggested commit subject containing `[#N]`.

Do not write task-specific issue bodies, PR descriptions or handoff notes into
the repository. Print them for the repository owner instead.
