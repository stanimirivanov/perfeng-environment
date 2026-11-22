# ADR-0063: Exercise retained-baseline schema admission on the paused application

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-30
- Milestone: M06 — Production Readiness
- Extends: [ADR 0062](0062-stage-retained-schema-admission.md), live acceptance scope

## TL;DR

- Use unchanged v2 for the existing two current-schema repeat runs in the trusted-main initial leg.
- Install only the already reviewed v2 definition and explicit ledger-get authority.
- Add one initial-request submission that refuses null expectedCurrent at candidate validation, before ledger reads or any Job.
- Preserve v1 bootstrap, none-intent, Job-create denial and SQL-fault acceptance.
- Retain five paused-suite Workflows and the same two repeat Jobs; block grants/recovery on every unverified boundary.
- Keep heavy checks on PR CI and live proof post-merge; no local execution or production changes.

## Decision and scope

The owner approved bounded complete-parent acceptance in the existing trusted-main
initial job's real adopted/paused window. After the unchanged v1 none-intent and
Job-create denial/restoration cases, submit the existing accepted initial request
to unchanged v2. Require successful preflight, selection and forward-only intent,
then candidate validation exit 2 for null expectedCurrent. The submitted bytes
must equal the accepted request exactly. Hash failures, startup failures, native
API denials or another failed stage cannot qualify. No later ledger-read,
quiescence or migration worker and no successful checkpoint/Job output may exist.
Candidate Failed or Error is accepted only with main exit 2: rejection leaves
the declared output file absent, so Argo output collection can itself report Error.

Replace only the existing two paused v1 successful repeat runs with unchanged v2,
using the existing pinned upgrade request. Require real ordered candidate, pointer,
receipt, byte-check, fresh quiescence, unchanged-receipt and final admission
workers before the fixed native migration Job. Verify actual candidate output
bytes (allowing Argo's single trailing-newline removal), ledger observations and
native checkpoint identities. Do not project v2 execution into synthetic v1 proof.

Independently bind the accepted raw request digest and receipt provenance to the
current pointer, root/child UIDs, selected packaging commit/image and actual
paused Deployment. Preserve full schema ledger/checksums/timestamps/OIDs, ACLs,
roles/passwords, rows/sequences/sentinel, Secrets/config/Git, SQL/S3 data and
workload/storage identities. Both native Jobs must be distinct and retained.

## Authority, retention and failure

Explicitly install the existing standalone ledger Role/Binding for the existing
migration identity only in this approved disposable leg. Verify exact rules,
subjects, roleRef and stable UIDs before/after execution. Use the existing runner
impersonation pattern with full ServiceAccount groups to check get on the pointer
and digest-derived receipt; allow 30 seconds for RBAC propagation and refuse
errors, warnings or malformed responses. No token is issued or forwarded.

Retain all five paused-suite Workflows, both repeat Jobs and earlier installation/
SQL-failed evidence. Final aggregate retention re-reads Workflows, Jobs, source
definitions and ledger authority, revalidates required native nodes/outputs, and
publishes a separate final outcome only after all checks. Historic paused state
is validated as execution evidence; later approved ledger faults are not treated
as new admission inputs. Existing complete evidence remains available if final
retention fails. No conditional skipping based on evidence-file presence is added.

Only verified refusal, successful repeats and retention permit existing adopted
grant acceptance and manual recovery. A failed boundary stops without automatic
repair, node retry, record/Job replacement or additional recovery/Pod replacement.
The v2 wait/watch wrappers match its published 1,800-second parent deadline with
bounded 1,830/1,840-second observers; v1 retains its existing bounds. The CI job's
60-minute budget is unchanged.

## Verification and remaining gates

Offline tests cover actual shell dispatch/stop paths, required v2 nodes/order/
outputs, exact candidate refusal, source/authority drift, missing retained proof
and persistent-state drift. Shared v1 install/fault/grant evidence remains tested.
Heavy full/cross-platform checks run on PR CI; full live proof first runs after
merge under the unchanged main-only guard.

No production template/RBAC definition, new SQL Job beyond the existing two,
fault, CI job/scenario/ref/request file/credential/budget, local operation or
host-command change is included. All 13 host entrypoints and the two-module/
708-line production verifier budget remain unchanged. This proves the fixed
current selection's bounded admission/reapplication, not general upgrades,
grant-parent admission, ambiguous commits, multi-user safety or operational cutover.
