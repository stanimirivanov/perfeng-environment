# ADR-0045: Exercise no-op root/child maintenance PATCH response loss

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-27
- Milestone: M06 — Production Readiness
- Extends: [ADR 0044](0044-exercise-maintenance-patch-response-loss.md)

## TL;DR

- Exercise lost successful PATCH responses for the two named Applications.
- Both are already auto-sync-disabled: the PATCH is a no-op and its version may stay unchanged.
- Scope each TLS proxy instance to one exact target, identity and planned payload.
- Require native failure, no downstream writes/checkpoint and unchanged running state.
- Run fresh unchanged adoption only after verified fault/retention; perform no recovery.

## Decision and authorization

The owner approved root/child Application response-loss acceptance in the existing
trusted-main initial job. This expands ADR 0044's excluded Application-fault
scope without adding jobs, scenarios, refs, rights, budgets, Pod replacements,
production changes or local operations. The already approved maintenance identity
is used; no credential is provisioned or broadened. Existing Deployment loss and
checked recovery remain intact.

Run after the read-only/permission/stale-guard/root-child interruption acceptance
and before the Deployment interruption/recovery. The running retention baseline
remains the existing checked happy-maintenance recovery. Each case uses its own
parent, proxy ConfigMap/Pod/Service/TLS Secret, certificate directory, evidence
prefix and immutable-source reversal check. Create-only setup refuses collisions;
there is no replacement, deletion or reuse repair. Existing pinned unprivileged
proxy shape, verified TLS, projected worker token and 10-second request semantics
are preserved. Runner credentials are not injected into any proxy Pod.

## Reversible copies and one-target transport

Generalize the existing CI copy generator with a closed root/child/Deployment
selection. Defaults preserve the prior Deployment copy. For each Application,
insert the same setup barrier after its real guard, redirect only the selected
native PATCH to its local transport copy, and preserve payload, arguments,
UID/resourceVersion/spec tests, authority, mutex, flags and deadlines. Reversing
the barrier/reference/transport changes must recover both published definitions.
Check installed spec before submission and after failure. PR CI generates and
lints both new copies with the existing pinned Argo CLI.

The shared proxy derives one target from an explicit whitelist of exact
apiVersion/kind/namespace/name identities. An Application instance serves only
that Application's named GET/PATCH route and fixed discovery; it cannot forward
the other Application or Deployment route. Reuse the verified maintenance-token
subject, API authentication, TLS, bounded bodies/query semantics and one-shot
mutation policy. Reject other methods, credentials, paths, payloads, duplicate
fields, unsupported timeout values and repeat forwarding. The ledger POST proxy
is unchanged. Actual kubectl GET/PATCH tests include `timeout=10s` to guard the
client integration corrected in PR #294.

## No-op response and retained-state proof

Require an already auto-sync-disabled Application at setup, real selected
readiness, existing adoption records/planning and the actual successful native
prefix/guard. The fresh target must exactly match guard UID/version/spec. Render
the unchanged native payload and require its desired spec to equal the captured
spec. These cases do not authorize or prove an enabled-to-disabled transition.

Validate HTTP 200, exact Application UID/spec/ownership metadata and no active
operation/deletion before dropping the response. A no-op may legitimately return
the same resourceVersion and generation. This does not relax Deployment's required
version change, generation increment and exact scale-to-zero spec. Upstream
Application generation must remain unchanged; only fresh, independently validated
root observations retain ADR 0036's existing refresh tolerance. Child generation
and all other ownership metadata remain checked. Require exactly one matching
commit event with the planned patch digest and zero successful responses forwarded.
The proxy's attempted flag prevents a repeated mutation even when no-op version
guards would still match.

Require the same failed parent UID, sole selected native worker exit 64 and
successful earlier workers. No later guard/PATCH, checkpoint observer or successful
maintenance result may execute. Arbitrary failure, stale guard, transport/API
refusal or wrong committed state cannot qualify. Preserve raw evidence privately.

Independently validate both captured roots, full running Deployment/Pod snapshots,
all workload specs/UIDs, original ledger bytes/provenance, Secrets/configuration,
Git credential observations and foundation storage/SQL/S3 data. Reuse the existing
strict running-state retention policy. There is no scaling, Pod replacement,
manual sync, baseline advancement, EXIT repair or recovery in these cases.

Only verified response loss and retained state permit a fresh unchanged adoption
parent. It rechecks real readiness, must reuse both records without create, and
must preserve the original records and adoption result. Keep this evidence and
the failed maintenance parent separate; never retry or resume successful nodes.
An unproven case stops for owned disposable-cluster teardown.

## Verification and limits

Run focused local format/lint/type, pure evidence/preparation and shell syntax/
diff checks. Heavy TLS/real-client/shell and full cross-platform checks run on
PR CI as requested. Full live proof remains the existing post-merge trusted-main
initial job; no manual dispatch or local execution is added. Publish only bounded
validated barrier/outcome reports and existing adoption results; raw token,
Workflow, API, proxy and Secret diagnostics remain private.

Rollback removes the two cases and their lint entries, reducing acceptance
coverage only. Enabled-to-disabled transitions, operational cutover/recovery,
production multi-user security and measured script retirement remain separate
gates; this does not claim production readiness or retirement credit.
