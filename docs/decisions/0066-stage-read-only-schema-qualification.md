# ADR-0066: Stage read-only selected schema-ledger qualification

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-30
- Milestone: M06 — Production Readiness
- Extends: [ADR 0049](0049-stage-pinned-schema-migration.md), independent qualification boundary
- Reuses: [ADR 0064](0064-stage-retained-runtime-grant-admission.md)

## TL;DR

- Stage an independent ledger/ownership qualification parent after existing retained-baseline admission and fresh quiescence.
- Derive eighteen expected migration names/hashes from exact bytes at the selected upstream Git commit, never from CI fixture assumptions.
- Use a fixed native PostgreSQL client Job and immutable reviewed SQL in a read-only repeatable-read transaction.
- Reuse existing grants authority and owner credential; add no RBAC or production Python.
- Keep it outside the active root and CI execution paths; live proof and preparation-flow composition remain separate.

## Decision and source contract

The owner approved staging `perfeng-release-schema-qualification-v1` to make
independent schema-ledger/ownership checks available before later migration/grant
composition. The selected migrator applies pending SQL and therefore cannot be
used as a read-only qualification operation. Existing successful migration or
grant Jobs do not replace independent qualification.

Use the existing four pinned Git inputs, release-runtime-grants identity, shared
mutex and complete published retained-baseline admission chain, including fresh
quiescence and final root/child binding. Replace only the terminal operation with
the fixed qualification Job. No migration/grant/resume/promotion is executed.

The immutable `perfeng-schema-qualification-sql-v1` ConfigMap holds reviewed SQL
and `migration-inventory.json`. Derive the inventory from raw Git object bytes
under `internal/postgres/migrations` at selected source commit
`2a74a20f69773fd9d43072e41668087612cdbf9e`. The selected Go migrator hashes these
embedded files directly and records bare lowercase SHA-256 strings. Record source
repository/revision and selected image in the inventory and annotate the ConfigMap.
The existing pinned upstream CI checkout independently verifies every name/hash
and the SQL's exact inventory. No CI fixture is a production trust source, and no
new checkout/credential or producer contract is needed. Selection/policy changes
require a newly reviewed immutable version, not editing published v1.

## Read-only qualification

The fixed Job uses the existing pinned PostgreSQL 17.11 client, local migration
owner URL and no administrator credential. Clear inherited connection overrides,
map only the reviewed local URL/password format, and force a read-only session
default. SQL explicitly starts a REPEATABLE READ READ ONLY transaction and checks
those settings before observing state. PostgreSQL's documented [read-only
transaction semantics](https://www.postgresql.org/docs/17/sql-set-transaction.html)
guard application/schema/privilege writes; this does not mean PostgreSQL performs
no internal disk writes. A [transient DO block](https://www.postgresql.org/docs/17/sql-do.html)
contains only catalog/ledger queries, assertions and local variables; transaction
control stays outside the block.

Require the exact migration owner connection with the existing non-privileged
flags, owned schema and schema objects, and the eight required ordinary tables
already required by runtime grants. Require exactly eighteen distinct ledger
versions matching all expected names/hashes, rejecting missing/unknown/duplicate
or mismatched rows. Applied timestamps must be non-null and finite. Use a full
outer comparison so extra rows cannot be ignored. Compare names/hashes with C
collation and refuse ledger row security so filtering cannot hide inputs.
No INSERT/UPDATE/DELETE, DDL,
GRANT/REVOKE, dynamic SQL, temporary object or advisory lock is present.

This qualifies the fixed release's recorded migration ledger and ownership. It
does not prove complete DDL equivalence, application semantics, backup integrity,
concurrent-operator exclusion or general upgrades. A repeatable snapshot is not
a lease across later operations. Existing downtime/drain/backup/operator exclusion
requirements and separate live acceptance still apply.

## Security, failure and rollout

Reuse the existing trusted grants identity's observation/ledger/Job rights and
explicit bootstrap prerequisites; add no RBAC. Broad Job creation can mount
namespace Secrets, so fixed templates remain trusted-operator policy rather than
multi-user admission enforcement. The Job mounts only migration-url and the fixed
SQL key, plus bounded scratch; it has no API token, retries, TTL, owner deletion,
arbitrary execution parameters or cleanup hooks. Retain non-root/read-only-root/
dropped-capabilities resources and the existing control-plane placement.

The parent deadline is 1,800 seconds, native worker 240 and Job 180. Bound connect
and API requests to ten seconds, SQL lock wait to five, and statement/idle-in-
transaction time to fifteen. Keep vendor SQL output private and return a constant
success/refusal diagnostic. Failure leaves maintenance and retained evidence in
place without repair, credential rotation, SQL mutation, activation or retry.
Success returns checkpoint and Job name, not a reusable mutation authorization.

Select only staged parent/Job/ConfigMap resources in release-workflows Kustomize.
No active-root, existing CI selection, job/scenario/budget or local/live operation
changes. Git removal does not prune installed versions/authority; withdrawal
requires explicit approval. Supported host paths remain unchanged.

## Verification and remaining gates

Focused offline tests verify exact source bytes, selected inventory/SQL binding,
read-only and refusal guard structure, admission ordering, fixed retained Job,
required immutable ConfigMap and actual credential/redaction shell paths. These
are not PostgreSQL engine or controller/API proof. Windows CI may skip source
parity when the upstream checkout is absent; Linux PR CI requires its existing
pinned checkout. Full suites and rendering/lint stay on existing PR CI; focused
staged rendering and pinned Argo lint may also run offline locally.

Complete-parent live qualification, actual SQL refusal/state retention and
preparation-flow composition require separately approved slices. No new production
Python or host command is added/retired: all 13 entrypoints and the two-module/
708-line verifier budget remain unchanged. Operational cutover, general upgrades,
ambiguous commits and non-local/multi-user security remain open.
