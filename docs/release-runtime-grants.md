# Staged native runtime grants

## TL;DR

- `perfeng-release-runtime-grants-v1` stages the reviewed runtime SQL privileges through a fixed native Job.
- Pinned preflight, exact lock, shared manual/forward-only intent and fresh quiescence must pass first.
- Use the migration owner URL, never the PostgreSQL administrator Secret.
- The selected schema/roles/credentials must already be independently qualified.
- This capability stays outside the active root; no local/live operation or host cutover is authorized here.

## Inputs and flow

[ADR 0055](decisions/0055-stage-native-runtime-grants.md) uses the existing
`request-revision`, `request-path`, `request-sha256` and `selection-revision` inputs.
Only the reviewed perfeng-local deployment lock and manual forward-only/0018
intent qualify. The intent step references the published schema-migration intent
template, without invoking its migration Job.

```text
four pinned Git inputs
  -> unchanged v3 preflight
  -> exact environment/deployment-lock digest
  -> shared pinned-byte/manual/forward-only intent
  -> fresh unchanged quiescence
  -> retained native owner-only grant Job
  -> checkpoint + Job name; leave paused
```

No caller-supplied image, SQL, role, connection key, Job body or privilege set is
accepted. The parent uses release-runtime-grants and the existing external-state
mutex. It neither migrates the database nor writes release receipts/pointers,
activates workloads, repairs state or grants Kubernetes runtime authority.

## Prerequisites and privilege policy

Complete the selected schema migration and its independent ledger/checksum
qualification first. The Job checks required tables and ownership but does not
replace schema qualification or expectedCurrent/admission. Externally prepared
database roles, migration-url and release-git-read must already be verified.
Schedule downtime, drain Runs, retain an appropriate backup and exclude competing
operators/manual syncs; quiescence alone does not prove these prerequisites.

The worker maps only the fixed local migration-owner URL into literal libpq
connection fields and PGPASSWORD, clearing service/options/address overrides.
The URL/password never enters process arguments or public logs. The reviewed
local sslmode=disable profile is not a non-local production TLS policy.

One versioned immutable SQL ConfigMap applies the supported transaction with
bounded SQL timeouts and the migrator's advisory transaction lock. Require the
exact owner connection, safe owner/runtime flags, owned schema/tables and required
table inventory. Runtime memberships or existing column ACLs require separate
review. Revoke PUBLIC/runtime schema/table rights, then grant only:

| Objects | Runtime rights |
|:--|:--|
| perfeng_control schema | USAGE |
| runs, create_bindings, baselines, reconciliation_leases, benchmark_target_reservations | SELECT, INSERT, UPDATE |
| artifacts, kubernetes_executions | SELECT, INSERT |
| schema_migrations and all other tables | None |

Verify effective rights and absence of grant options before COMMIT. DELETE,
TRUNCATE, REFERENCES, TRIGGER, MAINTAIN and schema CREATE remain denied. No data,
role, membership or default-privilege mutation is performed. SQL/policy changes
require a new immutable version and corresponding template selection.

## Staging and security

Resources live in `gitops/release-workflows`: parent, fixed Job template,
immutable SQL ConfigMap and `rbac/runtime-grants`. The separate
`gitops/release-workflows-bootstrap/runtime-grants-application-access.yaml`
declares the named root/child Application reads, following the schema-migration
standalone bootstrap pattern. It is not selected by the active local root.
Installing/executing these staged resources requires separate explicit authority.

The new identity has only the existing observation/Git reads and native Jobs
create/get/watch; no Job deletion/list/patch or workload mutation is granted.
However Job creation can mount arbitrary namespace Secrets, so this remains
trusted-operator authority. SQL uses only migration-url and mounts no administrator
Secret. The fixed Job has no API token, no retries/deletion/TTL, explicit limits,
non-root execution and a read-only root filesystem. Scratch output stays private.

## Failure, inspection and acceptance status

Retain the exact Workflow/Job UID, request provenance, declared SQL version and
effective ACL observations. Invalid connection input exits before psql; SQL
failure returns a constant diagnostic and withholds vendor output. Inspect the
retained Pod's private `/tmp/grants.private` only through an explicitly authorized
operator path. Never expose credential-bearing diagnostics in shared artifacts.

A failed response may follow committed grants. Leave the service paused and
inspect effective ACLs and Job state before a separately authorized fresh parent.
No automatic retry, Job replacement, credential repair, rollback or activation
is implemented. Git removal does not automatically revoke installed authority.

Offline structure/policy/shell tests, Kustomize Project/ConfigMap validation and
pinned Argo lint cover staging. Heavy full/cross-platform checks run on PR CI.
The initial trusted-main job now submits the unchanged parent for first/repeat
grants after schema installation, while the bootstrap Applications/Deployment
stay paused ([ADR 0056](decisions/0056-exercise-owner-runtime-grants.md)). Existing
runtime TCP probes verify exact privileges; both distinct owner Jobs, their
provenance and SQL/template identities remain retained through the scenario.
Schema/ledger/OIDs/data, roles/passwords, Secrets and foundation/storage identities
must remain unchanged. The other five scenarios retain administrator grant parity;
the excess-DELETE fault and administrator correction are unchanged everywhere.

Heavy checks run on PR CI; complete-parent live proof first runs after merge on
trusted main. The initial fixture now also removes only Jobs create from the
grant Role before first success ([ADR 0057](decisions/0057-refuse-runtime-grant-job-create.md)).
Require the real successful prefix, exact native Forbidden, absent Job/Pod/output
and unchanged ACLs/schema/data/credentials/storage before guarded rule restoration.
Retain that failed Workflow and recheck restored authority at final retention.
After restoration, the same job now holds the exact advisory key in an owned,
bounded session ([ADR 0058](decisions/0058-exercise-runtime-grant-sql-contention.md)).
Require actual owner backend/Pod/blocker attribution and the server's published
lock-timeout error, with unchanged ACLs/schema/data/credentials/storage. Only
proved failure permits normal holder disconnect; both sessions/locks must be
gone before fresh first/repeat grants. Retain the failed SQL Job and Workflow
alongside the successes. Unverified outcomes stop without repair or continuation.
After contention cleanup, a scoped fixture event trigger now raises only after
observing the exact schema-USAGE GRANT's effect inside the transaction
([ADR 0059](decisions/0059-exercise-runtime-grant-rollback.md)). Require the unique
error, unchanged committed ACLs and retained state before generating transactional
OID/owner/definition-guarded DROP RESTRICT of only the trigger/function. Verify
the original baseline before fresh first/repeat execution; retain both SQL-failed
Jobs and all parent evidence through final gates. No automatic cleanup or repair.
This establishes bootstrap success, authorization, contention and attempted-GRANT
rollback acceptance, not production cutover, admission or ambiguous-commit recovery. Supported host
commands and retirement counts remain unchanged.
