# ADR-0065: Exercise grants-v2 admission on the adopted paused application

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-30
- Milestone: M06 — Production Readiness
- Extends: [ADR 0064](0064-stage-retained-runtime-grant-admission.md), live acceptance scope
- Revises: [ADR 0061](0061-exercise-grants-on-adopted-paused-application.md), adopted parent version only

## TL;DR

- Replace only the existing adopted/paused grant-parent invocation with unchanged v2.
- Reuse its pinned upgrade request, native grant Job and runtime TCP probe.
- Add one existing-initial-request Workflow proving exact null-current refusal before ledger/quiescence/grant workers or a native Job.
- Preserve all v1 bootstrap/fault/correction proof and strict five-Job/six-Workflow bootstrap retention.
- Require the same six native Jobs and eight complete Workflows, fresh native proof and retained state before recovery/final publication.
- Keep heavy checks on PR CI and live proof post-merge main-only; no local execution or production changes.

## Decision and execution window

The owner approved complete grants-v2 acceptance in the existing trusted-main
initial job after schema acceptance and before its approved recovery. Explicitly
install the already reviewed standalone grants ledger-get Role/Binding and v2
WorkflowTemplate. Existing v1 bootstrap, Job-create denial, SQL contention,
attempted-GRANT rollback and owner DELETE correction remain unchanged.

Reuse the existing bounded named-read prerequisite helper with the grants
ServiceAccount and full impersonated groups. Permit only the two reviewed schema/
grants identities, retain the schema default, and never issue or forward a token.
Verify exact source specs/UIDs and ledger Role/Binding rules/subjects/roleRef/UIDs.
Keep the original seven-source bootstrap check strict; adopted checks additionally
require the two published v2 parents and exact installed read authority.

Submit the existing initial request to unchanged grants v2. Require successful
preflight, exact selection and forward-only intent, then candidate main exit 2
for null expectedCurrent. Candidate Failed or Error is valid only with that exact
exit; the missing declared output may cause Argo output collection Error. Reject
hash/startup/permission/timeout failures. Require no later ledger/quiescence/grant
worker, native Job/Pod or successful parent output, unchanged native-Job inventory
and all paused state. Only verified refusal permits the existing adopted run.

The successful adopted run now selects v2 and the same pinned upgrade request.
Require all actual v2 admission references/outputs in order before the unchanged
owner-only Job: candidate bytes, pointer, immutable receipt, accepted byte hash,
fresh quiescence, stable receipt and final current/root/child binding. Do not
project its nodes into v1 evidence. Reuse the existing runtime-only TCP probe;
require exact policy success and unchanged ACL bytes.

## Retention, failure and authority

Preserve schema ledger/checksums/timestamps/OIDs, rows/sequences/sentinel,
roles/passwords, Secrets/config/Git, SQL/S3 data and workload/storage identities.
Before existing recovery, independently re-read both parent Workflows, the native
Job, runtime probe and installed v2 sources/authority. Revalidate the required
nodes/outputs from those current Workflow reads, rather than trusting cached
success. Repeat at final aggregate retention before publishing bounded outcomes.
Keep historical paused snapshots as execution evidence; later approved ledger
faults do not become new admission inputs.

Bootstrap still requires five native Jobs/six Workflows. Complete retention
requires six Jobs/eight Workflows: the added null refusal has no Job, and the
existing adopted v1 Job is replaced by v2, not supplemented. Retain all identities
and provenance. Missing evidence must fail explicitly; never infer a phase from
file presence or fall back to v1.

The existing adopted wait is bounded to 1,830 seconds around v2's published
1,800-second deadline; v1 and the existing 60-minute CI job budget stay unchanged.
Failure blocks the successful grant/probe or recovery/final publication as
appropriate, with no automatic repair, record/Job replacement, retry, extra Pod
replacement or recovery. Raw resource/data/credential observations stay private.

## Verification and remaining work

Focused offline tests cover v2 execution/refusal and actual shell dispatch/stop
paths, wrong versions/order/outputs, missing cached/fresh proof, source/authority
drift, Job counts and state preservation. Shared schema identity checks retain
their default behavior; v1 grant/fault evidence remains covered. Heavy full and
cross-platform checks run on PR CI. Full controller/API/SQL proof first runs after
merge under the unchanged main-only guard; no local/live operation is performed.

No production definition, new SQL Job/fault/recovery, request/ref/credential,
CI job/scenario/budget or host-command change is included. All 13 host entrypoints
and the two-module/708-line production verifier budget remain unchanged. General
upgrades, ambiguous commits, non-local/multi-user safety, operational cutover and
measured retirement remain separate gates.
