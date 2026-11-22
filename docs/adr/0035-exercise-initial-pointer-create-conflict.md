# ADR-0035: Exercise initial pointer-create conflict recovery

- Status: Proposed
- Date: 2026-10-25
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved a sixth isolated trusted-main `pointer-create-conflict` scenario.
- Preserve real receipt creation/readback; suspend after pointer planning.
- Require native pointer AlreadyExists refusal, then unchanged-parent reuse of both records.

## Context and decision

Receipt-create conflict acceptance does not prove the later pointer-create
boundary. Extend ADRs 0033/0034 with one scenario-qualified temporary Git ref,
artifact and existing 60-minute-budget job instance. Keep credential rights,
production templates, main-only guard and cleanup ownership unchanged.

Reuse the existing CI parent generator and token-free helper subshell. The only
parent adaptation is a bounded native Suspend before the original pointer
templateRef. Removing the suspension must recover the exact production parent.
No response proxy or alternate transport is created for this scenario.

After successful actual readiness, initial absent-ledger planning, real receipt
creation/readback and pointer planning, the administrative fixture writer creates
exactly the planned pointer. This simulates a competing writer outside the
Workflow mutex; it does not claim that another production parent wrote it.
Retain the winning API response privately and compare it to fresh API state.

Resume the unchanged native create and require the pinned executor's exit 64
and exact AlreadyExists error naming the pointer. Require no downstream worker
or adoption result. Preserve the full winning pointer and original receipt,
including UID, bytes and receipt provenance. Wrong-object errors, authorization,
timeout, response-loss events or changed records cannot qualify.

A fresh unchanged production parent must reverify readiness, skip both creates
and reuse both records. Canonical full snapshots must remain identical. Existing
refusal, concurrency, Workflow deletion retention and approved final receipt
identity fault checks continue after recovery; no ledger reset is allowed.

## Security, cost and rollback

The existing administrative kubeconfig performs the single planned pointer
create in the owned disposable cluster. No additional process receives a
ServiceAccount credential and no proxy handles one. The owner explicitly approved
one more job instance/ref/artifact; the per-job ceiling remains 60 minutes.
Rollback removes the isolated leg and fixture branch. Existing owned teardown
cleans up its cluster and exact ref without touching retained environments.

## Verification and limits

Verify exact generator reversal, barrier/plan/receipt binding, native diagnostic
mutations, fresh API capture for both create-conflict cases, no-create retry,
token/cleanup behavior, CLI round trips, ref isolation, shell syntax, generated
and production offline Argo lint, and the full repository gate. Full-parent live
proof remains post-merge under the main-only guard. PR #249's main run was still
in progress at scoping; offline checks do not qualify its prerequisites.

This does not establish external-operator exclusion, promotion, pointer
advancement, retained export/restore, production cutover or script retirement.
