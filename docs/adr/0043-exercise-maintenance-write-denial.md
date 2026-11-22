# ADR-0043: Exercise missing maintenance PATCH grants in disposable CI

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-27
- Milestone: M06 — Production Readiness
- Extends: [ADR 0042](0042-exercise-lingering-pod-checkpoint-refusal.md)

## TL;DR

- Test root, child and Deployment PATCH denial in the existing initial job.
- Narrow only the existing fixture Role; keep GET and all other approved writes.
- Submit the unchanged complete parent and require the exact native Forbidden.
- Preserve workloads, Pods, ledger, Secrets and data without recovery.
- Restore captured Role rules conditionally only after verified refusal/retention.

## Decision and owner authorization

The owner approved three-boundary missing-write-permission acceptance. This
extends ADR 0042's excluded fault scope, reusing the existing trusted-main initial
job, six scenarios, temporary refs, credentials and 60-minute budget. It changes
no production manifest, verifier, workflow, credential right or local deployment.
Run the cases after happy maintenance recovery and request refusals, before the
existing stale-guard/partial-pause cases and destructive receipt faults.

For each named target, capture the existing maintenance Role and require its
rules to equal the staged source. Preserve its identity, labels and annotations.
Temporarily remove only PATCH for the selected name. For the shared Application
Role, retain GET for both names and PATCH for the other name; for the platform
Role, retain all reads and remove only the named Deployment PATCH. No Role,
RoleBinding or ServiceAccount is created, replaced or deleted.

Both narrowing and restoration are JSON patches testing Role UID,
resourceVersion and exact current rules before replacing rules. Require original
Role identity/metadata and the exact intended rule set in API responses. A
stale patch or unexpected policy stops without repair or mutation retries.

## Effective authority and native refusal

Use the already approved runner-admin impersonation helper, with the full
maintenance ServiceAccount groups, to query GET/PATCH authorization for all three
targets. Require all grants before narrowing, exactly one denied PATCH during
the fault (before submission and after failure), and all grants after restoration.
Allow bounded read-only polling for RBAC cache convergence, never arbitrary
command errors or malformed responses. Operational errors stop immediately.
Each API call retains its existing 10-second timeout; the polling loop admits
new rounds only within 30 seconds and bounds a round to six calls.

Submit the published complete maintenance parent with the original pinned
parameters and no copy, suspension or retry. Require real preflight/readiness,
existing receipt/pointer planning with both creates false, and the successful
native prefix through the selected guard. Bind each guard to the captured
resource UID/spec. Require the selected unchanged native PATCH to be the sole
failing worker (exit 64), with actual Forbidden naming the exact subject, PATCH
verb, API group, plural, namespace and object. A preliminary GET denial, invalid
JSON patch, timeout, unexpected success or unrelated worker failure cannot pass.
No later guard/write, checkpoint observer or maintenance result may execute.

## Retention and restoration

Both Applications are already auto-sync-disabled in this fixture. Earlier native
Application patches therefore must preserve their full spec. These cases permit
no scaling or Pod replacement: require the same Deployment spec/UID, complete
Pod snapshots, workloads, ledger bytes/UIDs/provenance, configuration, Secrets
and Git credential observations. Reuse the existing PostgreSQL/SeaweedFS
storage/data retention checks. Validate both captured roots with the existing
convergence checker before applying ADR 0036's limited root-generation tolerance.

Only verified native refusal and retained state may emit a restoration patch.
Fresh Role observation must still match the original identity/metadata and exact
narrowed rules. Restoration writes the original rules under UID/resourceVersion/
rules tests. Require exact restored API identity/rules and effective grants
before the next case. No EXIT restoration, force operation, live permission
broadening beyond the captured original grants, workload recovery or baseline
advancement is allowed. A failed setup leaves only the owned disposable cluster
for eventual teardown; it never authorizes an analogous local-host operation.

## Verification and limits

Run lightweight formatting, lint, targeted types, pure regression tests, shell
syntax and diff checks locally. Heavy cross-platform suites and shell failure
rehearsals run on PR CI at the owner's request; full live proof remains the
existing post-merge trusted-main workflow. No manual dispatch is added. Raw
Role/API/Workflow/worker/log/Secret snapshots stay private; publish only fixed
validated refusal and restored-identity outcome reports.

Rollback removes the acceptance fixture call and coverage only. This proves the
existing named write-denial boundaries, not multi-user isolation or field-level
RBAC. Ambiguous maintenance responses, operational cutover/recovery and measured
script retirement remain separate gates.
