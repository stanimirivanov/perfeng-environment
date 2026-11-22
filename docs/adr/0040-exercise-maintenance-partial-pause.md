# ADR-0040: Exercise interruption after native Application pause writes

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-27
- Milestone: M06 — Production Readiness
- Extends: [ADR 0039](0039-exercise-maintenance-stale-guard-conflicts.md)

## TL;DR

- Stop two CI-only parents immediately after their real root or child pause patch.
- Use the existing trusted-main initial job and checked recovered baseline.
- Require exact stop evidence, no later writes/checkpoint and strict retained state.
- Never repair, resume or advance the baseline after these cases.

## Decision

The owner approved two disposable partial-pause cases after the root and child
writes. Run them after the existing stale-guard cases and before receipt faults.
Each generated parent changes only its metadata and adds one suspension directly
after the selected unchanged native patch. Removing that suspension and restoring
metadata must reproduce the published parent exactly. Validate the installed spec
before submission and again after completion. Preserve arguments, ServiceAccount,
mutex, deadlines, native payloads and output wiring.

Before stopping, require actual selected preflight/readiness, the original
adoption records and successful baseline planning, the completed native prefix,
and the one running suspension. All observed Pod workers must have the maintenance
identity and successful main-container exits. Stop only that validated Workflow
using `argo stop`, then require the same Workflow UID/spec with `shutdown: Stop`,
the pinned controller's Failed phase and stop message on both Workflow and
suspension. A timeout, permission failure, unrelated worker failure, termination
strategy or resumed barrier cannot establish acceptance. Later guard/write or
checkpoint workers and successful checkpoint output must remain absent.

## Security and retention

Both Applications are already auto-sync-disabled in this fixture. This proves
interruption ordering after completed native writes, not an enabled-to-disabled
transition or recovery from a stopped Deployment. Preserve root/child specs and
ownership, the same running server Pod and Deployment, foundation Pods, storage,
ledger bytes/UIDs/provenance, configuration, Secrets and database/S3 data through
independent before/after observations and the existing retention checks.
Both fresh root samples must pass the existing root validation; retain ADR 0036's
generation bookkeeping tolerance without relaxing root spec or ownership checks.

No recovery, resume, marker write, workload repair or baseline advancement is
allowed. A setup or validation failure stops the job without cleanup beyond its
existing owned-cluster cleanup. Raw Workflow/worker/API/Secret evidence remains
private; publish only sanitized barrier reports and fixed validated outcomes.
The existing initial job, six scenarios, credentials, rights, temporary refs and
60-minute budget remain unchanged. Production templates and local infrastructure
remain unchanged. Removing the CI cases rolls back acceptance coverage only.

## Verification and remaining gates

Offline tests verify reversible copies, real baseline policy, strict stop and
retention evidence, and actual shell failure ordering. Run repository validation
and pinned offline Argo lint including both generated copies. Controller/API
proof remains post-merge trusted-main; this change does not dispatch CI or act on
the local cluster. Deployment interruption, lingering Pods, missing write rights,
ambiguous maintenance responses, operational recovery/cutover and measured script
retirement remain separate gates.

The pinned [Argo stop implementation](https://github.com/argoproj/argo-workflows/blob/v4.1.5/workflow/util/util.go#L1566-L1573)
sets `shutdown: Stop`. The [controller shutdown path](https://github.com/argoproj/argo-workflows/blob/v4.1.5/workflow/controller/operator.go#L1299-L1312)
fails an active suspension with the strategy-specific message. Unlike termination,
Stop permits exit handlers; this generated copy preserves the current published
parent, which has none, and acceptance forbids later workers/results.
