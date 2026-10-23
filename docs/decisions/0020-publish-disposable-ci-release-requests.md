# ADR-0020: Publish run-owned requests for full preflight acceptance

- Status: Proposed
- Date: 2026-10-21
- Milestone: M06 — Production Readiness
- Supersedes: None
- Amended by: [ADR 0028](0028-publish-adoption-request-boundary-fixtures.md), expanding publication to three CI request files in the same owned commit/ref

## TL;DR

- The owner approved a separate trusted-main CI job with `contents: write`.
- Publish a request after kind assigns its actual cluster UID; select the reviewed main commit separately.
- Run the unchanged complete preflight twice, then conditionally delete only the run-owned Git ref.
- PR integration remains read-only. This is preflight acceptance, not release deployment or promotion.

## Context and decision

The committed all-zero UID fixture proves child behavior and complete-parent
refusal. A fresh kind UID cannot be known before cluster startup. The owner
selected automatic main-only acceptance over a separately reviewed request for
a longer-lived cluster. The CI-generated request is not itself human-reviewed:
the reviewed workflow deterministically constructs and publishes its test input.
Production request review policy is unchanged.

`release-preflight-main.yml` runs on relevant main pushes or explicit main
dispatch. It checks out the event's immutable SHA with credential persistence
disabled, reuses the isolated pinned substrate, and enables only the preflight
parent phase. Request bytes bind the actual cluster UID, exact selected commit
and raw lock hashes. Git Data API creates a child commit changing one request
file; create-only publication uses `refs/heads/ci/preflight/<run>-<attempt>`.
No checkout branch/index is changed. No production manifest/schema is changed.

## Security and operations

Only the dedicated job requests repository contents write; no package write,
PAT, new credential setting or PR write permission is introduced. The token is
repository-scoped, **not ref-scoped**. It authenticates both publication and the
unchanged Git artifact driver's private checkout. Consequently the disposable
`release-git-read` Secret contains a write-capable token in this job. Its name
does not attenuate GitHub authority. Only trusted main code runs here; the
controller, executor and pinned images are inside this temporary trust boundary.
Kubernetes preflight authority remains read-only and explicitly tested.

Persist the exact ref/commit intent privately before attempting publication,
including ambiguous responses. Cleanup uses a receive-pack expected-SHA lease
on a **deletion only**. The lease flag implements the explicitly approved
temporary-ref cleanup; it does not permit forced branch updates, implicit repair,
history rewriting or deleting moved refs. REST GET followed by DELETE was
rejected because another writer can move the ref between those operations.
A conflicting ref or unexpected identity fails closed. Only authenticated
absence resolves a failed deletion safely.

The exit handler attempts ref cleanup before bounded cluster deletion and
fails on cleanup error. Upload only request bytes, commit/ref identifiers,
Workflow evidence and cleanup status; never token-bearing Secret objects or
raw subprocess errors. Hard termination or runner loss can leave the ref;
inspect the recorded exact commit before explicit manual cleanup. Unreferenced
Git objects can remain until GitHub garbage collection. This is not an audit
retention system. Token pushes do not trigger another push workflow.

## Validation and consequences

Offline tests cover trust guards, exact bytes and selection, intent before
publication, lost responses, credential handling and atomic cleanup refusal.
Main CI requires two successful complete production parents with identical
schema-valid evidence and actual UID, retained Workflow/Secret identities and
denied Kubernetes writes. Live acceptance remains pending until that workflow
passes after merge; it is intentionally unavailable on unreviewed PR code.

Full release composition, current-pointer advancement, real application health,
retained-state recovery/export and retirement of the thirteen legacy entrypoints
remain separate gates. Disabling this new workflow rolls back acceptance
automation without changing production inputs or the retained environment.
