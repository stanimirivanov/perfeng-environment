# ADR-0055: Stage runtime privileges through an owner-only native Job

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0049](0049-stage-pinned-schema-migration.md)

## TL;DR

- Stage a separate runtime-grants parent after pinned input/intent and fresh quiescence gates.
- Apply the existing reviewed grant transaction using migration-url as object owner.
- Mount no administrator Secret and add no production Python or arbitrary SQL input.
- Use a separate trusted ServiceAccount with reviewed observations and native Job create/get/watch.
- Keep the active root and supported host commands unchanged; live acceptance and retirement remain separate.

## Decision and scope

The owner approved this SQL privilege-mutation and Kubernetes authority boundary.
The supported host migration script still grants runtime rights, and CI currently
applies the same SQL as fixture administrator. Stage
`perfeng-release-runtime-grants-v1` with the same four pinned Git inputs, v3
preflight, exact local control-plane lock check and shared published
manual/forward-only/0018 intent template. Require fresh unchanged v3 quiescence
before the fixed native grant Job. Reuse the existing external-state workflow
mutex. There is no migrate, role creation, pause/resume, promotion or repair step.

The selected schema must already have been migrated and independently qualified.
Required-table/owner checks in this Job do not attest every migration checksum
or implement expectedCurrent/upgrade admission. Existing backup, drain and
exclusive-operator prerequisites remain explicit.

## SQL authority and policy

The [PostgreSQL owner privilege model](https://www.postgresql.org/docs/17/sql-grant.html)
allows an object owner to grant schema/table privileges without an administrator
connection. Use only the existing migration-url, requiring the exact reviewed
local role/host/port/database/password format. Remove inherited libpq service/
options/address overrides, use a literal PGDATABASE and keep PGPASSWORD out of
command arguments. No PostgreSQL administrator Secret is mounted or read.

A versioned immutable `perfeng-runtime-grants-sql-v1` ConfigMap holds one bounded
transaction. Take the existing migrator's advisory transaction lock. Require the
named migration connection, non-privileged owner/runtime role flags, schema and
table ownership, and the seven reviewed target tables plus schema_migrations.
Refuse runtime memberships and column ACLs rather than silently handling a new
privilege model. Do not create/alter roles, change default grants or modify data.

Preserve the supported REVOKE/GRANT matrix exactly: schema USAGE; SELECT/INSERT/
UPDATE on runs, create_bindings, baselines, reconciliation_leases and
benchmark_target_reservations; SELECT/INSERT on artifacts and kubernetes_executions.
Revoke schema/table rights from PUBLIC/runtime before granting the reviewed set.
Verify effective schema/table privileges and lack of grant options before COMMIT,
including denied DELETE/DDL/ledger and other non-selected table privileges. Any
server-side check failure aborts the transaction. Unknown authority requires
inspection, never implicit role or credential repair.

## Native Job and Kubernetes boundary

Use the already-reviewed digest-pinned PostgreSQL 17.11 client in a retained
Workflow-UID-named Job with fixed command, Secret key and SQL volume. Limit the
Job to 180 seconds, native worker to 240 and parent to 1200; use backoff zero,
no TTL/Workflow ownership, restart Never, non-root UID/GID 999, read-only root,
dropped capabilities and no API token. A bounded scratch emptyDir contains
private psql output; public results are constant and contain no vendor output.
The small shell only validates/maps the credential and redacts diagnostics;
orchestration remains native Argo resources and static SQL.

Add release-runtime-grants with the existing pinned-Git, namespace-identity,
named Application/Deployment and Pod observation reads plus create/get/watch
Jobs in perf-platform. Namespace Job creation can mount other namespace Secrets;
RBAC cannot enforce this manifest or Secret selection. This is trusted-operator
authority and is not a multi-user security boundary. No CI authorization or
new credential is introduced.

## Staging, failure and verification

Include the new resources only in the staged release-workflows Kustomize root;
the active local root stays unchanged. Follow the existing standalone bootstrap
Application-read Role pattern; that extra authority is explicitly installed only
before separately authorized execution. No local/live execution is part of this PR.

Failures retain the Job and leave workloads paused. A failed API/COMMIT response
does not prove grants were absent; inspect the exact Job and effective ACLs
before any separately authorized fresh submission. Never retry nodes, delete/
replace Jobs, rotate credentials or sync workloads as repair. Removing Git
selection does not implicitly withdraw installed RBAC with pruning disabled.

Focused offline tests cover ordering/pins/intent reuse, exact legacy grant parity,
owner guards, immutable SQL/Project validation, separate authority and the actual
shell's URL refusal/redaction/exit behavior. Offline Kustomize and pinned Argo lint
validate the staged composition. Heavy full/cross-platform checks stay on PR CI.
Complete-parent live owner-connection first/repeat/refusal/retention acceptance
requires a later scoped PR; no live grant proof is claimed here.

No host command is retired. The 13-entrypoint baseline and two-module/708-line
production verifier budget remain unchanged. Ambiguity/recovery, upgrade admission,
non-local TLS/credential profiles, production multi-user security and operational
cutover remain separate gates.
