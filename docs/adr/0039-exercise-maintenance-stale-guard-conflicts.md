# ADR-0039: Exercise three native maintenance stale-guard boundaries

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-26
- Milestone: M06 — Production Readiness
- Extends: [ADR 0038](0038-exercise-maintenance-pause-and-checked-recovery.md)

## TL;DR

- Exercise root, child and Deployment stale native patches in the existing trusted-main initial job.
- Insert exactly one suspension in a CI-only copy after the selected real guard; preserve production templates.
- Advance resourceVersion using guarded, restored fixture metadata; preserve generation, spec and retained state.
- Require native rejection with no later writes/checkpoint; no automatic repair or recovery.

## Decision

After checked maintenance recovery and the request-refusal cases, before receipt faults, run three isolated submissions using the existing request and credentials. Each CI-only WorkflowTemplate differs from the published maintenance parent only in its metadata/name and one native suspension immediately between the selected guard and patch. Removing the suspension and restoring metadata must reproduce the source exactly. Check the installed spec against that generated copy before submission and after completion. Native templates, ServiceAccount, arguments, mutex, deadlines, patch payloads and output wiring remain unchanged.

Require real preflight, selected readiness, valid existing adoption records, preceding native operations and the selected guard before setup. Capture fresh API/retention evidence. Add and remove the fixture-owned `perfeng.io/ci-maintenance-guard` marker with exact UID/resourceVersion/spec tests on both writes. Use top-level annotations for Applications and a top-level label for Deployment, never its selector or Pod template. The owner separately approved the Deployment label correction: Kubernetes increments Deployment generation on annotation updates and may copy annotations to ReplicaSets. Label updates avoid that annotation-specific behavior. Generation, full spec, UID and other ownership metadata must remain unchanged; only resourceVersion and managedFields bookkeeping may differ after restoration.

Resume only after verifying both fixture responses, exact marker restoration and a resourceVersion different from the successful guard. Require the unchanged native patch to fail, retaining the actual worker identity/exit, template reference, chronology and API Invalid diagnostic. Kubernetes uses a generic Invalid/422 message for JSON Patch application failure. The specific stale-guard cause is established by the unchanged native payload and fresh API proof of equal UID/spec but different resourceVersion, rather than invented detailed error text. Pinned Argo resource errors use exit 64. Do not accept arbitrary nonzero exits, permission errors, timeouts or setup failures.

## Security and retention

This extends only the approved disposable fault scope. Existing initial job, six scenarios, request/ref ownership, credential rights and 60-minute budget remain unchanged. Root auto-sync stays disabled, the six other children stay skipped, and prior root/child writes must preserve their already-disabled full specs. No Deployment scale or Pod replacement is expected. Strict recovered workload/Pod, ledger bytes/UID/provenance, storage, configuration, Secrets, database and S3 retention remain required. Raw API/log/Secret snapshots remain private; publish validated fixed outcomes and existing safe barrier diagnostics.

Failed setup leaves the parent suspended or failed and stops the job. There is no EXIT cleanup, automatic repair, fresh authorization, write retry or recovery. A failure may leave the fixture marker in the owned disposable cluster until ordinary cluster cleanup; never resume or advance the baseline in that state. This does not change production recovery, multi-user admission or deployment selection. Local execution stays deferred; full API/controller proof remains post-merge under the existing trusted-main guard. Host-script retirement and operational cutover remain separate gates.

## Implementation evidence

- [Kubernetes Deployment update strategy](https://github.com/kubernetes/kubernetes/blob/v1.35.0/pkg/registry/apps/deployment/strategy.go#L98-L111): annotation/spec changes increment generation.
- [Kubernetes JSON Patch handler](https://github.com/kubernetes/kubernetes/blob/v1.35.0/staging/src/k8s.io/apiserver/pkg/endpoints/handlers/patch.go#L374-L386): patch application failure maps to HTTP 422.
- [Pinned Argo resource executor](https://github.com/argoproj/argo-workflows/blob/v4.1.5/workflow/executor/resource.go): unchanged JSON patch-file arguments and BadRequest error wrapping.
