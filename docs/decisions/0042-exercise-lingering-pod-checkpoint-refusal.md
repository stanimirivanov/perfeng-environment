# ADR-0042: Exercise lingering-Pod checkpoint refusal in disposable CI

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-27
- Milestone: M06 — Production Readiness
- Extends: [ADR 0041](0041-exercise-deployment-pause-interruption.md)

## TL;DR

- Insert one scheduling-gated matching Pod after the checked Deployment interruption.
- Require real unchanged checkpoint policy refusal and bracketed retained-state evidence.
- Delete only the captured fixture Pod with UID/resourceVersion preconditions.
- Require a fresh unchanged successful checkpoint before the existing checked recovery.
- Keep heavy validation on CI and live proof in the existing post-merge initial job.

## Decision and authorization

The owner approved bounded lingering-Pod acceptance in the existing trusted-main
initial job. This extends ADR 0041's explicitly excluded fault scope. Existing
happy maintenance, root/child interruptions, checked Deployment stop, fresh
checkpoint and recovery remain intact. No new job, scenario, temporary ref,
credential, permission, timeout budget or production manifest is introduced.

After the interrupted Deployment has settled at zero replicas, create exactly one
named Pod in perf-platform using the checkpoint's two workload labels, a
fixture-owned annotation bound to the stopped Workflow UID, and a distinct
pod-template-hash label. That hash prevents existing Deployment ReplicaSets from
adopting the otherwise matching orphan. A scheduling gate remains present for the
Pod's entire lifetime: no node, token mount, volume, init container or running
container is permitted. Use the selected Deployment's image identity; no image
pull or container execution is needed. Do not remove the gate.

Capture private API, ledger, Secret, configuration and data snapshots before and
after submitting the unchanged read-only checkpoint with the original pinned
parameters. Validate the stopped-parent identity, successful real preflight,
original preflight result, unchanged verifier reference and chronology. All worker
UIDs/accounts must match; only the actual quiescence worker may exit nonzero, with
its native policy-refusal exit 2. No observer or parent result may exist. Timeouts,
preflight failures, operational errors and unrelated failures are not acceptance.

The production verifier intentionally hides policy details in its log. Require
its existing generic policy-refusal line, then replay the unchanged immutable
policy over both independent snapshots. Each must refuse exactly `Pods remain or
absence is unproven`; each must pass when only the sole fixture Pod is removed
from that snapshot. Validate its UID/spec/gate and non-execution against creation
evidence in both samples. Preserve original evidence rather than editing API
snapshots. This combines real native failure with precise independent policy
attribution; it does not expose or change production diagnostics, nor record the
worker's internal HTTP samples. Bracketed evidence cannot establish an atomic
lease; every subsequent operation must still independently check current state.

## Cleanup and recovery boundary

Only validated refusal and retention may generate DeleteOptions containing the
last observed fixture UID and resourceVersion. DELETE uses the exact fixed Pod
API path and those preconditions, with no force, retry, EXIT repair or fallback.
A stale/replaced object or failed setup stops acceptance before cleanup/recovery;
the owned disposable cluster remains the eventual cleanup boundary. Confirm
absence and settled zero replicas before submitting a new unchanged checkpoint.
Bind its distinct UID and preflight start to the prior failed execution and its
validated refusal report. Existing full checkpoint verification must succeed
before manual child sync, same-ReplicaSet replacement verification and checked
baseline advancement. No deployed resource or ledger record is deleted here.

## Verification and limits

Focused local formatting, lint/type, pure policy and shell syntax checks are
appropriate on the busy host. Existing PR CI runs the heavy cross-platform
suite, including failure-order rehearsals; full live proof remains post-merge
trusted-main. Raw Workflows, logs, worker and API/Secret snapshots stay private;
only the validated fixed refusal report and existing checkpoint/recovery reports
are published. No manual dispatch or local infrastructure execution is added.

Rollback removes only this acceptance fixture and its call, restoring ADR 0041
coverage. Missing maintenance write rights, ambiguous maintenance responses,
operational cutover/recovery and measured script retirement remain separate gates.
