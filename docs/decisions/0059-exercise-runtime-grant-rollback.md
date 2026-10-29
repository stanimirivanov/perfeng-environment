# ADR-0059: Prove attempted runtime GRANT rollback before fixture cleanup

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0058](0058-exercise-runtime-grant-sql-contention.md)

## TL;DR

- Add one post-GRANT rollback fault after contention cleanup and before first successful grants.
- Create only a fixture-owned event trigger/function, targeting the exact owner/application/schema-USAGE statement.
- Require visible in-transaction USAGE before the unique error, then unchanged committed ACLs and surrounding state.
- Only verified rollback permits OID/owner/definition-guarded transactional DROP RESTRICT and fresh execution.
- Retain both failed SQL Jobs through final gates; heavy checks stay on CI and live proof remains post-merge.

## Decision and fixture authority

The owner approved this bounded fault in the existing trusted-main initial job.
Require an ungranted schema-USAGE baseline and no existing event triggers or
same-named public function overloads. Create one public function and one event
trigger named perfeng_ci_grant_rollback as the fixture administrator, with
cluster-bound ownership comments. No replacement or takeover is allowed.

The function is SECURITY INVOKER with search_path=pg_catalog. It only reads and
raises errors; no data or privilege writes. Its guard requires ddl_command_end,
GRANT, migration current/session user, the fixed grant application name and the
exact published schema-USAGE statement. It raises the unique fixture exception
only after has_schema_privilege observes the completed GRANT's effect. PostgreSQL
documents that [ddl_command_end observes changed catalogs before commit, and an
exception rolls the transaction back](https://www.postgresql.org/docs/17/event-trigger-definition.html).
The unchanged production parent/SQL therefore execute the actual attempted write.

## Refusal and retained state

Require the real successful preflight/selection/intent/quiescence prefix and
fixed failed native Job, exit 1/Error and BackoffLimitExceeded, without successful
parent output. Timestamped PostgreSQL evidence must attribute the unique error
and exact GRANT statement to the owner/application during the grant container's
lifetime. Generic errors, deadlines, stale logs or unexpected success cannot qualify.

Replay full native and state evidence before cleanup. Require original ACL bytes
and absent committed schema USAGE, unchanged schema ledger/checksums/timestamps/
OIDs, rows/sentinel, roles/passwords, Secrets and paused foundation/storage identities.
Job inventory may grow only by this one failed Job. Capture the complete fixture
catalog records, checking OIDs, owners, comments, body, invoker/configuration and
trigger event/filter/function binding; require unchanged installed/failed/current
definitions and no grant sessions or key locks. All raw SQL/catalog/log evidence
stays private.

## Guarded cleanup and fresh execution

Only proven rollback generates cleanup SQL. In one bounded transaction, acquire
the shared advisory key, compare the full current catalog snapshot with captured
identity/ownership/definitions, then DROP EVENT TRIGGER and DROP FUNCTION RESTRICT.
No CASCADE, IF EXISTS, replacement, EXIT cleanup or repair. An unexpected dependency
aborts the transaction. The private CI fixture has one DDL writer; the advisory key
serializes cooperating grant Jobs, not arbitrary database administrators.

Require the original catalog/privilege baseline after cleanup before the existing
fresh first/repeat parents. Retain four native Jobs (two SQL-failed, two successful)
and five parent Workflows. Final retention replays rollback and cleanup evidence,
checks fresh fixture absence/quiescence and expected successful schema USAGE,
and preserves all execution/source/authority identities before bounded outcomes.

## Verification and exclusions

Deterministic tests cover catalog ownership/definition drift, unique error and
statement attribution, ACL/data retention, guarded SQL generation and actual shell
failure/cleanup/continuation boundaries. Focused checks run locally; heavy full
checks stay on PR CI. Actual PostgreSQL trigger/parent proof remains post-merge
under the existing main-only guard.

No application DDL/data changes, production templates/settings, new CI jobs/
scenarios/refs/credentials/rights/budget or local execution. Other scenarios and
the existing DELETE fault/correction remain unchanged. Ambiguous commits,
admission, cutover and retirement remain open; the verifier budget stays two modules/708 lines.
