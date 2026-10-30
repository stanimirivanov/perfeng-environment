# ADR-0069: Exercise complete database preparation on the adopted paused fixture

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-30
- Milestone: M06 — Production Readiness
- Extends: [ADR 0068](0068-stage-database-preparation-composition.md), live acceptance only

## TL;DR

- Preserve standalone coverage and add complete-parent null-current refusal plus first/repeat acceptance in trusted-main initial only.
- Reuse existing pinned requests/authority; retain two complete parents and six additional native Jobs before recovery.
- Reuse the existing runtime TCP probe after composition; add no additional probe Job.
- Validate raw nested boundaries, ordered native gates, outer identity/mutex/deadline and required Job/checkpoint outputs.
- Fresh proof permits exact additional-Job inventory accounting; unknown Jobs still fail standalone suites.
- Heavy checks stay on PR CI; full-parent live proof remains post-merge, with no production or local changes.

## Decision and execution window

The owner approved bounded acceptance of the unchanged staged database parent
in the existing initial job, after standalone schema/qualification and adopted
grant execution, while the real adopted application remains paused. Install only
the published parent. Submit the existing initial request first: real migration
preflight/selection/intent must succeed, then candidate main exit 2 must refuse
null expectedCurrent before ledger/SQL workers, later phases or success outputs.
Allow unexecuted skipped/omitted placeholders, not later native workers.

Run two fresh complete parents with the existing pinned upgrade request. Each
must complete migrate → qualify → grant, producing three distinct retained native
Jobs bound to its outer Workflow UID. Reuse the already approved caller authority,
credentials and publication ref/request files. Preserve all standalone cases and
their strict evidence checks. The existing runtime TCP probe runs after the two
composed parents, in its original adopted-grants sequence; no new probe Job is
needed. Capture state again after the probe and before existing recovery.

This adds six native Jobs and three parent Workflows, within the existing initial
job's 60-minute budget. No new GitHub jobs/scenarios, inputs/refs, credentials,
rights, deadlines, Pod replacements or recovery operations. Other five matrix
scenarios remain unchanged. A budget failure requires diagnosis, not skipping
acceptance or silently raising limits.

## Nested execution and unchanged semantic checks

Validate the actual outer WorkflowTemplate reference, four pinned arguments,
stored published Steps/caller/mutex/deadline and absence of conflicting overrides.
Require exact native worker identities/exits and complete phase Pod coverage.
Select phase roots by reference and outer boundary, then follow boundaryID owner
chains, rejecting cycles, orphan workers, wrong phase ownership and execution
outside phase time bounds. Require phase ordering and the three exact Job outputs;
the final checkpoint must equal the grants phase's pre-Job observation.

Use the pinned [Argo 4.1.5 Steps controller](https://github.com/argoproj/argo-workflows/blob/v4.1.5/workflow/controller/steps.go)
boundary semantics. Its outbound nodes connect to later step groups, so a naive
transitive children traversal can cross phases. Boundary ownership isolates the
actual template invocation instead. In-memory views adapt only the root lookup,
display name and reference for existing standalone predicates. All node/worker/
Job UIDs, arguments, observations and outputs remain observed values. Never save
these views as standalone Workflows or use synthetic records as trust sources.
Retain raw complete Workflows unchanged, including stored specification.

Reuse existing schema execution/admission, grants/qualification execution,
paused-state/persistent-state and fixed runtime probe predicates. The schema
execution check permits release-runtime-grants only with retained v2 admission;
its standalone release-schema-migration default stays unchanged. This tests the
already approved caller use without new production policy or authority.

## Source, state and inventory retention

Capture the eleven referenced published templates, intent/SQL/inventory/verifier
ConfigMaps and exact caller ServiceAccount/Role/Binding/ClusterRole declarations.
Require unchanged UIDs, source/spec/data bytes and exact rules/subjects/roleRefs.
Preserve schema ledger/checksums/timestamps/OIDs, ACLs, rows/sequences/sentinel,
roles/passwords, release receipt/pointer, Secrets/config, SQL/S3 data and workload/
storage identities. Existing paused-state and checked-recovery gates remain.

Before recovery and final success, reread all three raw parents, native workers,
six Jobs/Pods, source/authority identities, full native Job inventory and state.
Recheck the existing runtime probe's identity, native exit and policy result.
Final retention allows only the already checked workload recovery; persistent
data and execution records remain protected. Publish bounded UID/digest/decision
summaries only after complete proof.

Standalone inventories must account for the additional cohort explicitly. First
revalidate complete preparation retention, then require the exact two successful
cohort Jobs of the relevant kind, with original UID/spec/provenance/status. Save
the full raw inventory as a private `.all.json` observation before partitioning
only those two identities for the unchanged standalone count checks. Missing,
replaced or modified cohort Jobs fail; all other Jobs remain for strict legacy
validation. No label/name wildcard or optional file-presence exclusion is allowed.

## Failure and remaining work

Any failed wait, phase, null refusal, source/state/probe or retention gate blocks
later operations/recovery and final acceptance. No retry, node resume, ledger
edit, credential rotation, deletion or automatic/EXIT repair. Earlier SQL may
have committed; failure does not prove rollback. Existing owned-cluster cleanup
is unchanged and raw observations stay private.

Focused offline tests cover real-shaped nested data, cross-boundary/cyclic/out-of-
window nodes, wrong callers/outputs, null refusal, lost/replaced Jobs, source/
authority/state/probe tampering, strict partitioning and actual Bash stop paths.
They are not SQL/controller proof. Full checks stay on PR CI; live acceptance
runs post-merge under the existing trusted-main guard. Deliberate failure/partial-
state injection, ambiguous commits, concurrency exclusion, general upgrades,
backup, non-local/multi-user safety, production selection, local execution and
host cutover/measured retirement remain separate. No production Python is added.
