# ADR-0033: Exercise initial receipt-create conflict recovery

- Status: Proposed
- Date: 2026-10-24
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved a fifth isolated trusted-main `receipt-create-conflict` scenario.
- Suspend after real readiness/planning; let the fixture writer create the planned receipt.
- Keep native receipt creation unchanged and require its actual AlreadyExists refusal.
- Recover through a fresh production parent, preserving the winner and creating only the pointer.

## Context and decision

The existing local Workflow mutex serializes cooperating parents. Concurrent
exact retries after adoption do not prove the initial-create conflict boundary
against a writer outside that mutex. Native create must refuse an existing
receipt without replacing it; a fresh attempt must reverify and reuse it.

Extend ADR 0032's four-scenario limit with the owner-approved fifth leg. Reuse
the existing run/attempt/scenario ref and artifact ownership rules and independent
clean cluster. Keep job permissions, credentials, production templates and the
main-only execution guard unchanged.

Reuse the existing CI parent generator and identity `perfeng-adoption-response-ci`.
For this scenario its only behavioral adaptation is a native Suspend immediately
before receipt creation, bounded by the existing parent deadline and released
explicitly. The production receipt `templateRef` and transport remain unchanged;
removing the suspension and its template must recover the exact published parent.
The shared identity does not imply a response proxy: none is created in this leg.

At the barrier, require successful actual readiness, root/absent-ledger reads and
an exact recomputed plan from pinned request bytes. The existing administrative
fixture writer creates exactly the planned immutable receipt and retains the API
response privately. It preserves the paused origin's planned provenance; this is
a controlled competing-create simulation, not a claim that another production
Workflow created the record. No ledger reset or overwrite is permitted.

Resume the parent. Require native receipt-worker failure, no downstream worker
or adoption result, the pinned Argo executor's exit 64 and its AlreadyExists
error naming the exact receipt. The manifest-path context and executor exit are
observed in qualified native-ledger kind runs; generic conflict text, timeout,
authorization failure or a response-loss event alone cannot qualify. Compare the
complete winning API response with a fresh direct receipt observation, including
UID, bytes and provenance. Require the pointer still absent.

Submit a fresh unchanged production parent through direct transport. It must
reverify readiness and ledger identity, skip receipt creation and create only the
missing pointer. Preserve the full receipt snapshot and original provenance.
Use the recovered result as baseline for existing repeats, refusals, mutex,
Workflow-deletion retention and the already approved final receipt identity
faults. Those faults remain after retention; none resets this scenario.

## Security, rollout and rollback

Reuse the token-free helper subshell and sole runner cleanup owner. The fixture
writer uses the existing owned-cluster administrative kubeconfig; no additional
process receives a ServiceAccount credential and no proxy handles a token.
One additional `-receipt-create-conflict` temporary ref and artifact is permitted.
No retained environment, production grant or public release contract changes.
Rollback removes the leg and its fixture branch; owned teardown removes test state.

## Verification and limits

Run generation reversal, barrier-plan/CLI/evidence mutations, native diagnostic
shape regressions, fresh retry invariants, token/cleanup behavioral regressions,
ref isolation, shell syntax, generated/production offline Argo lint and
`make validate`. Full-parent live proof remains post-merge under the main-only
guard. Previous main run 40090656826 failed at the migrator prerequisite before
adoption; offline success does not resolve or qualify that prerequisite.

This does not prove pointer-create conflicts, exclusion of external operators,
promotion, pointer advancement, retained export/restore, operational cutover or
measured script retirement.
