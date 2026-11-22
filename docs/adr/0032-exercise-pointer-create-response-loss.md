# ADR-0032: Exercise adoption pointer-create response loss

- Status: Proposed
- Date: 2026-10-24
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved a fourth isolated trusted-main `pointer-response-loss` scenario.
- Reuse the verified TLS proxy for exactly the planned initial pointer create.
- A fresh unchanged parent must skip both creates and preserve both committed records.
- Keep production templates, credential rights and the main-only guard unchanged.

## Context and decision

Receipt response loss leaves a missing pointer to create on retry. Losing the
pointer's successful response leaves both records committed without a successful
adoption result. The retry must establish fresh readiness and reuse both records
without overwriting their identities or original provenance.

The owner approved a fourth matrix leg with a scenario-qualified temporary Git
ref/artifact and the existing proxy handling the adoption ServiceAccount token.
This extends ADR 0031's three-scenario limit and receipt-only fault scope. Each
leg retains its own clean cluster, existing deadlines, permissions and cleanup.
Initial, receipt-only and receipt-response-loss scenarios keep their meaning.

Reuse the CI parent generator. For this leg, preserve normal receipt creation
and readback, root re-observation and pointer planning. Add a bounded native
suspension immediately before pointer creation and adapt only that native
resource template's transport. Reversing those declared changes must recover
the exact published parent and native pointer operation. The failed origin is
an adapted fixture; the subsequent recovering parent is unchanged.

At the barrier, recompute both planner phases from exact request bytes and
actual readiness/root/receipt observations. Bind the expected pointer to the
committed receipt UID and original Workflow provenance. Only the receipt may
exist before resumption. Forward the one exact planned pointer create through
the existing verified TLS hops, then withhold its HTTP 201 response before
headers. The proxy must compare all pointer data, namespace/name, labels,
annotations and UID, and reject binary data, immutable pointers and owner
references. It remains restricted to the same collection and exact query.

Require failure specifically at the pointer worker with exit 1, successful prior
readiness/plans/receipt creation, no downstream worker and no adoption result.
The single typed event must match the directly observed pointer UID and exact
content digest, with zero successful responses forwarded. The receipt readback
must still match the stored receipt. API denial, timeouts, mismatched data or
transport failure alone cannot qualify.

Submit a fresh unchanged parent through direct transport. It must skip both
creates, publish a freshly verified result and preserve full receipt/pointer
snapshots, UIDs and original receipt provenance. Continue existing repeats,
refusals, concurrency and Workflow-deletion retention checks. The already
approved receipt identity faults still occur only after completed retention;
no ledger record is removed to reset the pointer fault.

## Security, rollout and rollback

The existing token-free subshell and sole parent cleanup owner apply to both
response-loss scenarios. The proxy Pod has no ServiceAccount token mount and
receives only the approved worker's projected credential for the exact write.
No GitHub token, new rights, controller, package setting or production contract
is introduced. Raw payloads, credentials and diagnostics remain private.

The fourth temporary ref uses the existing run/attempt ownership guard plus
`-pointer-response-loss`; cleanup cannot cross scenarios. Owned-cluster teardown
removes fixture/TLS state. Rollback removes this leg and invocation; retained
environments and published manifests are untouched.

## Verification and limits

Run pointer/receipt TLS and refusal regressions, generated-template reversal,
CLI/evidence mutations, both retry semantics, token/cleanup and ref isolation,
shell syntax, offline Argo lint for both generated copies and `make validate`.
Existing native receipt acceptance remains distinct from the complete pointer
scenario. Full-parent live execution remains post-merge behind the unchanged
trusted-main guard; no live pass is inferred from offline tests.

This does not implement pointer advancement, initial-parent races, promotion,
retained export/restore, production admission, operational cutover or script
retirement.
