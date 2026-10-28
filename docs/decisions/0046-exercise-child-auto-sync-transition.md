# ADR-0046: Exercise the native child auto-sync pause transition

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-28
- Milestone: M06 — Production Readiness
- Extends: [ADR 0045](0045-exercise-application-patch-response-loss.md)

## TL;DR

- Prove a real child enabled-to-disabled transition in the existing initial CI leg.
- Enable only the healthy pinned control-plane child, preserving prune/selfHeal false.
- Invoke the unchanged published child guard and PATCH using the maintenance identity.
- Require exact restoration and retained running state before fresh unchanged adoption.
- Keep root disabled; this is leaf acceptance, not complete-parent transition proof.

## Decision and authorization

The owner approved bounded child transition acceptance on 2026-10-28. Run only
in the existing trusted-main `initial` disposable cluster, after Application
response-loss acceptance and before Deployment interruption. No jobs, scenarios,
refs, credential rights, budget increases, production templates, local operations
or additional Pod replacements are authorized.

A runner fixture PATCH tests UID, resourceVersion and full spec before enabling
only the healthy child already reconciled to its pinned selected sources.
Prune and selfHeal remain false. Validate both API response and independent named
read: exact identity/ownership/spec except the enabled bit, one generation
increment, changed resourceVersion, healthy/synced state and no active operation
or conditions. Do not retry writes or repair ambiguous responses.

Use a create-only CI WorkflowTemplate with exactly two steps referencing the
published `guard-child` and `patch-child` leaves in
`perfeng-maintenance-resources-v1`. Preserve the published parent's maintenance
ServiceAccount and shared mutex. Native resource payloads, guards, flags,
timeouts and outputs remain unchanged, without transport adapters or retries.
The composition has a 180-second deadline and bounded runner wait. Check its
installed spec before submission and again before accepting evidence.

Root automatic sync stays disabled and six other children remain skipped.
Enabling the child differs from Git and can make root OutOfSync. Do not weaken
complete-parent root convergence checks to accommodate this: the composition
proves only the child leaf transition. Root enablement and complete-parent
enabled-baseline acceptance remain separate. The mutex covers the CI workflow,
not fixture setup or other operators; this remains an isolated trusted fixture,
not multi-user coordination.

## Evidence and retained state

Require the exact submitted enabled snapshot, guard output identity/full spec
and disabled sync policy, correct native references and step order, and exactly
two successful maintenance workers. The restored child retains its UID and full
original spec, has a second generation increment and a resourceVersion different
from the enabled observation. Only these generation/version changes and
managedFields may differ in child metadata. Both captured roots independently
pass existing convergence validation and ADR 0036 bookkeeping tolerance.

Preserve workload/Deployment/Pod identities and specs, full ledger records,
Secrets/configuration/Git credential observations and cluster UID. Existing
retention independently preserves foundation/storage/SQL/S3 identities and data.
No Pod replacement, sync, recovery or baseline advancement occurs. Only verified
transition, restoration, root convergence and retained state permit fresh unchanged
adoption to recheck readiness and reuse both original records/results.

Failed setup, native operation, convergence, evidence or retention stops before
adoption, without EXIT repair. The child may remain enabled after failure until
owned disposable-cluster teardown. Publish only the bounded validated outcome
and existing sanitized adoption results; keep raw Workflow/API/worker/Secret
evidence private.

## Verification and limits

Run focused pure evidence/preparation, formatting/lint/type and shell syntax
checks locally. Heavy shell/full cross-platform checks and generated workflow
lint run on PR CI. Actual controller/API acceptance remains post-merge under
the existing main-only guard; local execution stays deferred. Rollback removes
this acceptance phase and lint entry only. Root transitions, complete-parent
enabled transitions, operational cutover, production multi-user admission and
measured script retirement remain open.
