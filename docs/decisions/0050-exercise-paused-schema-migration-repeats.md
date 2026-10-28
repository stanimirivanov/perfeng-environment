# ADR-0050: Exercise complete schema-migration repeats on the paused baseline

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-28
- Milestone: M06 — Production Readiness
- Extends: [ADR 0049](0049-stage-pinned-schema-migration.md), [ADR 0020](0020-publish-disposable-ci-release-requests.md), [ADR 0038](0038-exercise-maintenance-pause-and-checked-recovery.md)

## TL;DR

- Run only in the existing trusted-main initial job after proved maintenance pause.
- Reuse the existing forward-only upgrade request for two unchanged-parent runs.
- Publish one additional none-intent request in the same owned commit/ref.
- Require semantic refusal, repeat success, retained Jobs and unchanged SQL/ledger/data before recovery.
- Keep heavy checks on PR CI; complete live proof remains post-merge, with no local execution.

## Decision and authorization

The owner approved bounded complete-parent repeat acceptance and the corrected
fourth payload. The existing CI parent request is already forward-only/0018;
its upgrade-request also names the accepted request digest as expectedCurrent.
Reuse that unchanged fixture for both successes. Only the initial leg publishes
one extra `ci-schema-migration-none-v1.json`, matching upgrade-request except for
migration mode none. All original three files remain byte-compatible and the
other five scenarios still publish exactly three. Ref journaling and conditional
cleanup retain the same commit/ref ownership; no additional ref is created.

Insert acceptance between the verified happy maintenance pause/retention and its
already approved recovery. Install the approved staged definitions/bootstrap
reads and reuse the existing migration identity, Git credential and pull Secret.
No production template/policy, rights, CI job/scenario, credential, budget,
Pod-replacement allowance or recovery operation changes. No local operation or
manual CI dispatch is authorized here. The same initial job's live proof remains
post-merge under its trusted-main guard.

## Preconditions and complete-parent proof

Before any parent executes, require the original paused identities/data and all
18 selected schema-ledger entries with their released checksums, owner and OIDs.
This must already be a current schema. Snapshot SQL roles/password hashes,
application table rows and sequence state, sentinel data and existing storage,
Secrets, schema/ledger and workload identities privately. All fixture SQL is
SELECT-only; catalog identifiers/literals are quoted before psql gexec reads.

Submit the unchanged parent once with none intent. Require real preflight and
selection success, then exactly the intent worker's semantic validation exit 2,
matching the [pinned validator contract](https://github.com/sourcemeta/jsonschema/blob/v17.2.0/docs/validate.markdown).
No quiescence/Job worker, native Job, Job Pod or successful parent output may
exist. Revalidate request bytes/schema/intent against the fixed published policy;
execution, transport, image-pull or artifact failures cannot qualify.

Then submit the unchanged parent twice using the existing valid upgrade request.
Require exact request/ref/selection identities, preflight, selection, successful
intent, fresh quiescence and native Job order. Both checkpoints must bind the
current request, cluster and paused resource UIDs. Job creation must occur within
the native worker after the checkpoint, with the published image, command,
credentials, resource/security fields and Workflow-UID name/provenance. Require
exactly one successful Job Pod and the expected maintenance-independent migration
identity for every Argo worker. Job specs must match all declared fields while
allowing server defaults. At Pod admission, tolerate only the declared placement
and known not-ready/unreachable and memory-pressure defaults, consistent with
[Kubernetes admission behavior](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/).
Reject extra containers, token volumes, host namespaces and arbitrary tolerations.

Retain both Jobs with distinct UIDs/specs/provenance and all three original
Workflow identities. Recheck the installed parent/leaf/preflight/selection/
quiescence definitions and immutable intent policy, including stable UIDs.
Native controller bookkeeping may refresh resourceVersion/managedFields; it may
not replace Jobs or change declared specs/provenance. No workflow is retried,
adapted, resumed or deleted for acceptance.

## Retention and recovery gate

Each case must leave the Deployment at observed zero replicas and matching Pods
absent. Compare the original accepted receipt/pointer and Secret/config/Git
observations, foundation Pods, workload/storage identities and SQL/S3 data.
Require unchanged schema/ledger OIDs, checksums and applied timestamps, role
state and private sorted JSON snapshots of complete application rows, sequence
state and migration sentinel. Original evidence is never overwritten and no
baseline advances in this phase.

Only all verified cases, retained definitions/Jobs/Workflows and final recomputed
evidence allow the existing explicit manual child recovery and its one checked
server Pod replacement. Failure leaves the service paused for existing owned
cluster/ref cleanup; no EXIT repair, new sync or automatic recovery occurs.
The release parent itself still does not implement expectedCurrent admission;
CI verifies fixture/current identity independently and makes no upgrade claim.

## Diagnostics, verification and remaining gates

Keep bounded Pod watches through the parent's deadline and reuse existing
redacted startup projection, plus allowlisted stage phases, waiting reasons and
failure signals, before asserting success. Full Workflow/Job/Pod/SQL/Secret/log/watch files remain private; only validated
outcomes and bounded diagnostic projections enter results artifacts. Missing Job,
startup failure, API error and timeout are not semantic intent refusal.

Focused request-publication, native evidence/defaults/retention and shell recovery
ordering tests run locally with lint/type/docs/syntax checks. Heavy full and
cross-platform suites stay on PR CI. This proves only repeat execution against
an already-current schema after real pause; complete-parent empty-database
installation, denied Job authority, partial/ambiguous SQL failure, retained
upgrade admission/recovery, local cutover and measured retirement remain open.
Rollback removes this acceptance phase/fourth fixture; retain the original
three publication fixtures and production templates. No script is retired.
