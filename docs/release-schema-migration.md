# Staged pinned schema migration

## TL;DR

- `perfeng-release-schema-migration-v1` stages migration through 0018 using pinned Git inputs and a native Argo Job.
- Preflight, exact lock and reviewed forward-only/manual intent must pass before fresh quiescence and Job creation.
- It remains outside the active local root; no live execution or supported host cutover is authorized here.
- A separate trusted identity can create Jobs and therefore potentially mount namespace Secrets; multi-user admission remains open.
- Failure retains Jobs/evidence and leaves the service paused; runtime grants, recovery and activation remain explicit separate operations.

## Inputs and prerequisites

Use the existing four [preflight inputs](release-preflight-workflow.md):
`request-revision`, `request-path`, `request-sha256`, `selection-revision`.
The exact reviewed request must specify manual activation and forward-only
migration ID `0018`. The parent pins the current deployment lock's raw digest
and released image; it accepts no arbitrary image, command, credential key or
Job manifest. The immutable intent ConfigMap uses the same pinned JSON Schema
validator as preflight and rechecks request bytes at the pinned Git commit.

The database roles and fixed `perfeng-control-plane-database` migration-url,
`perfeng-control-plane-registry` pull Secret and `release-git-read` credential
must already be provisioned and verified through their existing procedures.
Both Applications must be paused, the selected Deployment stopped/absent as
permitted by the unchanged checkpoint policy, and matching Pods absent.
Do not provision or rotate credentials as implicit failure recovery.

Before any future separately authorized execution, schedule downtime, stop
submissions, drain active Runs, retain a suitable database backup and exclude
other operators/manual syncs and host preparation. Quiescence is not proof of
these prerequisites or a lease. This staged parent does not read the current
ledger or implement expectedCurrent/promotion admission. Retained-environment
upgrade admission and recovery remain separate review gates.

## Staging and flow

```text
four pinned Git inputs
  -> unchanged v3 preflight
  -> exact environment + deployment-lock digest check
  -> pinned request bytes + immutable manual/forward-only/0018 intent policy
  -> fresh unchanged quiescence verifier
  -> native create/wait: selected immutable /usr/local/bin/migrate Job
  -> checkpoint + Job name; leave paused
```

The staged Kustomization includes the new parent, native Job template, immutable
intent ConfigMap and dedicated identity/RBAC. Its Application-read bootstrap
Role/Binding is `gitops/release-workflows-bootstrap/schema-migration-application-access.yaml`;
apply it only as part of a separately authorized staged installation. The
existing staged Project already permits the required kinds/destinations;
argocd remains outside those destinations. The active root, Taskfile and
supported [M04 operator procedure](kubernetes-runtime-deployment.md) are unchanged.
There is no new supported submission Task. After live acceptance and a reviewed
rollout, the existing pinned Argo submission procedure can select this parent;
this document is not permission to submit it now.

The parent holds the existing release mutex throughout. It has a 1,200-second
deadline; intent validation has 60 seconds, existing quiescence has 180 seconds,
and the native worker has 240 seconds around a Job with 180 seconds and zero
backoff. No workflow retry or continue-on-failure is configured. Job name
`perfeng-schema-<Workflow UID>` and annotations retain request/selection identity.
A fresh submission creates a different identity and must repeat all gates.

## Security and outputs

The separate `release-schema-migration` identity can read the named root/child,
Deployment, kube-system Namespace and Git Secret, list platform Pods, publish
Argo task results and create/get/watch platform Jobs. It cannot pause workloads,
sync Applications, mutate the ledger, delete Jobs or write Secrets. Job creation
can mount namespace Secrets and RBAC does not restrict template fields, so
trusted submitters are required; see [ADR 0049](decisions/0049-stage-pinned-schema-migration.md).

The Job uses fixed migration/pull Secret references, no Pod API token, non-root
UID/GID, read-only container filesystem, dropped capabilities, RuntimeDefault
seccomp and bounded CPU/memory. It retains the host migrator's control-plane
node placement and toleration. There is no shell around the migration binary.
No TTL, Workflow owner reference or cleanup hook deletes the Job.

Success means the Job completed and returns its name plus the prior checkpoint.
It does not prove schema identity/checksums, runtime grants, operational health,
adoption or promotion. The selected binary executes all bundled migrations
through 0018, not a caller-supplied single SQL file. The existing CI image-parity
phase remains separate and has not exercised this complete parent.

## Failure, retry and rollback

Any failed preflight, lock, intent or quiescence stops before the Job. Missing
credentials, image-pull failures, native API denial, timeout or migrator failure
stop without pause/resume, grants, sync, ledger advancement or automatic repair.
A failed/ambiguous create may leave a Job or committed SQL; retain private
Workflow/node/Job identities, full Job spec and migration-ledger observations.
Do not expose database URLs or raw logs in shared artifacts.

Inspect retained state before a separately authorized fresh submission. Do not
use argo retry, reuse successful nodes, delete a conflicting Job or bypass fresh
quiescence. Kubernetes/API behavior is not an exactly-once SQL guarantee; the
migrator's ledger and reviewed recovery procedure remain necessary. Follow the
existing coordinated M04 recovery guidance for partial forward-only changes;
reverting Git cannot undo SQL. No automatic restore is provided.

Removing this staged source/selection prevents future intended use but does not
remove installed immutable objects or RBAC while pruning is disabled. Explicit
withdrawal of the new RoleBindings needs owner approval. Retain Jobs, records,
Secrets and data. See [troubleshooting](troubleshooting.md) for failure classification.

## Verification and cutover gates

Focused offline tests cover supported/refused intent, request-byte refusal,
exact-lock refusal, sequence, native Job security/retention and RBAC. Offline
Kustomize/Project checks verify the staged inventory; PR CI performs heavy full,
cross-platform and pinned Argo lint/render checks. No Docker/cluster execution
is introduced into offline validation. Complete-parent repeat/data retention
acceptance is staged in the existing initial CI leg, with live proof post-merge.
Bounded complete-parent first installation is staged in the initial CI leg;
bounded SQL contention failure and fresh execution are staged under
[ADR 0053](decisions/0053-exercise-schema-sql-contention.md). Bounded attempted-DDL
rollback is staged under [ADR 0054](decisions/0054-exercise-schema-ddl-rollback.md);
ambiguous commits remain deferred.
Bounded Job-create denial acceptance is staged under [ADR 0051](decisions/0051-exercise-schema-job-create-denial.md).
All 13 host deployment entrypoints remain supported and no script is retired.

## Complete-parent repeat acceptance on disposable CI

The existing trusted-main initial job now stages unchanged-parent repeat
acceptance while the service is already paused, before its approved recovery
([ADR 0050](decisions/0050-exercise-paused-schema-migration-repeats.md)). It reuses
the already-current schema from earlier image parity and the existing valid
upgrade request. One extra none-intent request is published in the same owned
commit/ref for semantic refusal. The original three files and other five
scenarios remain unchanged.

Require all 18 selected migrations before execution, real parent stage/worker
order, semantic none-intent exit 2 with no Job, two fresh checkpoints and distinct
successful retained Jobs/Pods. Preserve original receipt/pointer and Secret
identities, roles, schema/ledger checksums/timestamps/OIDs, complete private
application row/sequence state and SQL/S3/storage data while still paused.
Only all checks and retained source/Job/Workflow identities permit the existing
manual recovery; no new replacement or recovery path is added. Raw evidence
stays private; validated outcomes and redacted startup/stage projections are
artifacts. Heavy verification stays on PR CI and full live proof remains
post-merge. This is repeat execution, not complete-parent first installation,
expectedCurrent admission, an upgrade or interrupted SQL recovery. Host tasks
and retirement status are unchanged.

## Complete-parent Job-create denial

[ADR 0051](decisions/0051-exercise-schema-job-create-denial.md) adds one case
between none-intent refusal and successful repeats in the same paused window.
Temporarily remove only Jobs create from the fixture migration Role, preserving
get/watch and all observation rights. Verify effective access, run the unchanged
parent, and require the successful real prefix/quiescence followed only by the
native create worker's exact Forbidden. No Job/Pod, successful parent output or
SQL/retained-state change may occur. Only proved refusal and retention permit
UID/resourceVersion/rules-guarded restoration of the captured rules and verified
restored access. All four Workflows remain retained. Failure stops without Role
repair or recovery; production authority and local infrastructure are unchanged.
Heavy checks stay on CI and live proof remains post-merge. This adds no script
retirement credit or SQL failure/recovery claim.

## Complete-parent first installation

[ADR 0052](decisions/0052-exercise-complete-schema-first-install.md) replaces only
the initial scenario's first CI-only successful migrator with the unchanged
complete parent. The database is naturally empty: no reset or deletion is used.
The existing forward-only parent request passes real preflight, selection,
intent and quiescence against explicit paused bootstrap Applications and the
never-started zero-replica Deployment. Require the selected fixed native Job/Pod
and all 18 migration versions/checksums. This stage proves first installation;
the later real adopted application and repeat proofs remain separate.

The immutable server pull Secret is created earlier from the same approved
fixture credentials. Later server setup must verify its original UID/type/data
and reuse it, creating only runtime/authentication Secrets. Drift or replacement
stops execution. Other five scenarios retain their original timing/path.
Preserve roles, existing platform resources/config/Secrets, running ready
foundation Pods, PV/claim bindings and cluster identity through installation.

The later paused suite requires the retained install and both failed Jobs
at baseline; refusal cases add zero, and successful repeats add one each.
Require all five Jobs, four later Workflows plus the installation and two failed Workflows, and
the original pull Secret before publishing complete acceptance or allowing
existing recovery.
No production templates, credentials/rights, CI jobs/scenarios/refs/budgets,
local infrastructure or supported host commands change. Heavy checks run on
PR CI; live proof remains post-merge. Ambiguous SQL recovery, retained upgrades,
production multi-user security, cutover and measured retirement remain open.

## SQL contention before installation

[ADR 0053](decisions/0053-exercise-schema-sql-contention.md) adds one complete
parent failure before the existing first installation in the initial CI leg.
An owned bounded psql session holds the selected migrator's exact advisory lock.
Require the successful real parent prefix/quiescence, then the actual selected
backend blocked by that holder, bound to native Pod IP, role/application,
backend PID/start time and exact key/query. Require native exit 1 with a failed
Job rather than a Job deadline, no parent result, absent schema and retained
surrounding state. No credential, role, ledger or persisted setting is edited.

Only proved failure and retention permit normal disconnect through the holder's
private input FIFO. Independently require both sessions/locks gone and schema
still absent before the fresh unchanged first-install parent. No backend
termination, node retry, fallback or SQL repair is permitted. Retain the failed
Job/Workflow through installation, paused refusals/repeats and final recovery
gates. Heavy checks stay on CI; live proof remains post-merge. This proves
pre-DDL contention failure/retry, not partial writes or ambiguous commits.

## Attempted-DDL rollback before installation

[ADR 0054](decisions/0054-exercise-schema-ddl-rollback.md) adds the selected
component's existing artifacts-table collision after contention cleanup and
before first installation in the initial CI leg. Create only a fixture-owned
schema/table/sentinel, then require the unchanged parent's real successful
prefix and exact native/PostgreSQL relation-collision failure. Catalog evidence
must preserve fixture OIDs/owners/markers/columns/sentinel while showing no
persisted migration-created tables or ledger and unchanged surrounding state.

Only that proof generates cleanup SQL. A bounded transaction locks the fixture
table, rechecks catalog identity/ownership/inventory and sentinel, and drops
only that table/schema using RESTRICT. No CASCADE, IF EXISTS, EXIT repair or
release-ledger edit is permitted. Independently prove the original empty catalog
and retained state before fresh installation. Retain both failed Jobs/Workflows
through final recovery gates. Heavy checks stay on CI and actual database proof
remains post-merge. Ambiguous commits remain separate work.


## Runtime privileges remain a separate phase

The [staged runtime-grants parent](release-runtime-grants.md) now declares the
existing reviewed grant transaction through a separate native Job using the
migration owner. It reuses this parent's published intent template, not its
migration Job. It remains outside the active root with offline/PR-CI verification
only; complete-parent live grant acceptance and host cutover are separate.
Do not treat schema migration success as proof that runtime privileges were
established, or staged grants as proof of migration-ledger qualification.
