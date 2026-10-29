# ADR-0054: Prove complete-parent DDL rollback and guarded fixture cleanup

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0053](0053-exercise-schema-sql-contention.md)

## TL;DR

- Add one DDL collision after contention cleanup and before successful first installation.
- Reuse the selected component's rollback test with a fixture-only schema/table/sentinel.
- Require exact native/PostgreSQL failure and no persisted migration tables or ledger.
- Generate transactional identity/ownership-guarded DROP RESTRICT only after proof.
- Require an empty retained baseline before fresh installation; retain both failed Jobs/Workflows.
- Keep heavy checks on CI and live proof post-merge; ambiguous commits remain open.

## Decision and fault scope

The owner approved this bounded partial-DDL fault and guarded fixture deletion.
Only the existing trusted-main initial leg changes. After proved contention
release, independently require an absent schema, empty catalog and no migration
sessions/locks. Create a fixture-owned `perfeng_control` schema and `artifacts`
table using the existing admin transport with SET LOCAL ROLE perfeng_migration.
Use one non-null integer probe/sentinel and cluster-bound comments on both
objects. Capture schema/table OIDs, owners, comments, exact columns and row data.
The fixture owns these objects exclusively in its disposable cluster.

This matches the selected component's
[TestMigrationRollback](https://github.com/stanimirivanov/perfeng-control-plane/blob/2a74a20f69773fd9d43072e41668087612cdbf9e/internal/postgres/integration_test.go)
and [migration 0001](https://github.com/stanimirivanov/perfeng-control-plane/blob/2a74a20f69773fd9d43072e41668087612cdbf9e/internal/postgres/migrations/0001_initial.sql):
the artifacts collision follows earlier runs/create_bindings creation. No
migration file, binary, production template/RBAC, credential, role or release
ledger is edited. No database reset, new CI job/scenario/ref/right/budget or
local infrastructure action is introduced.

## Native failure and rollback evidence

Submit the unchanged complete parent with the same pinned first-install request.
The shared verifier requires real successful preflight/selection/intent/quiescence,
the exact native Job/Pod declarations and only migration failure with exit 1 and
BackoffLimitExceeded. A deadline, startup error or generic failed Job cannot qualify.

Read bounded private PostgreSQL logs since Workflow creation, with Kubernetes
timestamps. Require the exact artifacts relation collision for the migration
user/database/application between Job creation and terminal parent failure.
Require the statement text to include runs/create_bindings/artifacts creation,
and require the selected migrator's fixed connection/migration/failure messages.
No successful parent output or migrator success message is permitted.

Catalog and sentinel observations must show the identical fixture schema/table
OIDs, owners/comments/columns and sole row after failure. No runs, create_bindings,
schema_migrations, index, sequence or other migration relation may persist.
No migration session/advisory lock may remain. Preserve source/policy UIDs/specs,
SQL roles/passwords, credentials/config, foundation/workload/storage and cluster
identities. Preserve the earlier contention Job too. Raw SQL/log/credential
observations stay private; shared outcomes are bounded and validated.

## Guarded cleanup and fresh installation

Only all failure/rollback/retention proof generates cleanup SQL. In one bounded
transaction, take ACCESS EXCLUSIVE on the fixture table, compare its complete
catalog/ownership/OID/marker/column inventory and sentinel against the proved
snapshot, then DROP TABLE and DROP SCHEMA with RESTRICT. Never use CASCADE,
unconditional IF EXISTS, role changes, ledger repair or an EXIT cleanup path.
An unexpected catalog, extra dependency, changed identity or stale fixture aborts
the transaction and blocks further operations. This relies on the existing
exclusive trusted fixture-writer boundary; it is not multi-user admission.

Independently re-read the catalog and retained surrounding state after normal
cleanup. Require the original database OID, absent schema/relations/columns,
no migration sessions/locks and unchanged retained identities before the existing
fresh unchanged first-install parent may proceed. Failure never permits fallback,
node retry, automatic repair or workload recovery.

## Retention, verification and remaining work

The paused baseline now contains exactly both failed Jobs and the successful
installation Job. Refusals add zero; repeats add one each. Recompute rollback
and cleanup evidence and verify the original failed Job/Workflow identity/spec/
provenance/terminal failure before existing recovery. Final evidence retains five
schema-parent Jobs and seven parent Workflows, with the original pull Secret and
all existing data/identity gates intact.

Focused offline tests cover positive rollback/cleanup, wrong identities/owners/
markers, persisted ledger/tables, sentinel drift, exact log attribution, unexpected
native outcomes, post-cleanup state and actual shell fail-stop deletion gates.
Lint/type/docs/shell checks run locally; heavy full/cross-platform checks stay on
PR CI. Actual PostgreSQL fault/guarded cleanup proof remains trusted-main post-merge.
No local Docker/cluster run or new production Python is added.

Rollback removes this additional fault and restores the previous baseline counts.
No host entrypoint is retired; the production verifier remains two modules/708
lines. Ambiguous commits, retained-release upgrades/admission/recovery, production
multi-user security, operational cutover and measured retirement remain open.
