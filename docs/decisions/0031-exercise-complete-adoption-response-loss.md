# ADR-0031: Exercise complete adoption receipt response loss

- Status: Proposed
- Date: 2026-10-24
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved a third `response-loss` trusted-main scenario.
- Extend the existing exact-receipt TLS proxy to an adapted complete-parent fixture.
- Require a fresh unchanged production parent to recover by creating only the absent pointer.
- Keep the main-only guard, credential rights and production templates unchanged.

## Context and decision

Native-ledger response loss with synthetic planner inputs does not prove complete
adoption recovery. The receipt-only parent scenario proves pointer quota
rejection, which is a different failure boundary.

The owner approved a third isolated scenario, its scenario-qualified temporary
Git ref/artifact, and the existing proxy handling the release-adoption worker's
projected token. This supersedes ADR 0026's two-scenario limit and ADR 0030's
native-worker-only scope for this disposable test. Initial and receipt-only
scenarios retain their current meaning.

Generate a separate WorkflowTemplate from the published parent. Preserve its
mutex, identity, deadline, arguments, readiness, observations, planners and output
composition. Add only a bounded native Suspend after `plan` and replace the
receipt-create reference with a local copy of the native resource template
carrying ADR 0030's existing transport adapter. Offline reversibility checks must
recover the exact published parent and native template. Production source and
installed production templates remain unchanged.

At the barrier, read actual planner outputs and the originating Workflow UID.
Recompute the plan against exact request bytes and actual root/readiness
observations before configuring the proxy's one allowed receipt. Require an
empty ledger. Start the existing TLS proxy and explicitly resume the parent.
The existing parent deadline bounds the suspension; there is no automatic resume
timer to race proxy setup. A failed Workflow alone cannot qualify: only the
committed-response event and corresponding failed receipt worker satisfy acceptance.

Require successful real nested readiness and planning, failure at receipt
creation, no downstream worker and no adoption result. The proxy's single event
must prove upstream HTTP 201, exact content digest, committed UID and zero
successful responses forwarded. Fresh direct API reads must find the exact
receipt with original Workflow provenance and no pointer. Validate the
controller-stored fixture composition against generated source.

Then submit the unchanged production parent through normal transport. Fresh
readiness and planner phases must reuse the receipt and create only the missing
pointer. Compare the full stored receipt before and after recovery. Continue
existing retries, refusals, concurrency, authority and retention checks. Delete
the fault-origin disposable Workflow only at the existing retention boundary.
No record is deleted to reset this case. Existing identity faults still follow
completed retention proof in each owned disposable scenario.

## Security, rollout and rollback

The job remains trusted-main only. The third leg reuses job-scoped permissions,
pinned checkouts, approved repository Secret configurations and scenario-specific
compare-and-delete ref cleanup. Credential rights do not expand. No GitHub token
reaches the proxy and its Pod has no ServiceAccount token mount. Both TLS hops
verify certificates. Forwarding remains one attempt, limited to the expected
receipt and worker identity. Proxy deadlines and behavior remain unchanged.
Tokens, payloads and raw API diagnostics are not uploaded.

Setup uses the owned disposable cluster and dedicated kubeconfig. Teardown
removes the proxy, ephemeral TLS state and fixture template with that cluster.
Rollback removes the third scenario and invocation; no retained environment,
production manifest or published contract requires migration.

## Verification and limits

Run deterministic generation/evidence mutation and ref isolation checks, shared
proxy TLS/refusal tests, shell syntax, generated-template offline Argo lint and
`make validate`. These prove fixture consistency, not live parent acceptance.
Complete-parent execution remains post-merge under the existing main-only guard;
live proof is pending until the trusted-main leg succeeds.

The failed origin is an explicitly adapted complete-parent fixture. The
recovering parent is unchanged. Pointer response loss, initial-parent races,
promotion, retained recovery/export/restore, production admission, operational
cutover and measured script retirement remain separate.
