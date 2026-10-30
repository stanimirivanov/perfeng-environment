# Staged read-only schema-ledger qualification

## TL;DR

- `perfeng-release-schema-qualification-v1` observes the fixed selected release's migration ledger and ownership.
- Pinned preflight, retained-baseline admission and fresh quiescence precede a bounded native read-only Job.
- Expected names/hashes come from selected upstream Git bytes, not CI fixture state.
- Reuse grants authority and migration-url; no new RBAC, SQL writes, grants, resume or promotion.
- Keep it outside the active root; bounded trusted-main first/repeat acceptance runs post-merge, with preparation composition and host cutover separate.

## Inputs and source identity

[ADR 0066](decisions/0066-stage-read-only-schema-qualification.md) reuses the four
release parameters: request-revision, request-path, request-sha256 and
selection-revision. The parent uses the same exact local deployment lock and
manual/forward-only/0018 intent and shared admission leaves as grants v2. An
initial adopted baseline/current pointer must already exist; this is not a new
bootstrap admission path. It accepts no arbitrary SQL, image, role, connection,
checksum set or Job manifest.

`perfeng-schema-qualification-sql-v1` is an immutable perf-platform ConfigMap.
Its inventory records the selected upstream repository/commit/image and eighteen
names with bare SHA-256 hashes of the exact embedded SQL bytes. Its SQL contains
the same reviewed expected set. Tests use raw Git objects at the selected commit
and the existing pinned Linux CI checkout, avoiding working-tree line-ending
normalization and the CI fixture as a trust source. A changed selection or policy
needs a newly reviewed immutable version.

## Native flow and prerequisites

```text
four pinned inputs -> preflight -> exact lock -> reviewed intent
  -> shared candidate/current/receipt/accepted-byte admission
  -> fresh quiescence -> unchanged receipt -> final current/root/child binding
  -> native read-only qualification Job -> checkpoint + Job name; leave paused
```

Reuse release-runtime-grants and its existing Git/observation/Job rights and
explicit Application/ledger-read bootstrap. Installation/execution requires
separate authority. Retain downtime, drain, backup, verified credentials and
operator exclusion; neither admission nor a snapshot is a cross-operation lease.
Broad Job-create rights remain trusted-operator authority. This adds no role,
credential or multi-user enforcement.

The public PostgreSQL client image is the existing digest-pinned grant client.
The fixed Job mounts only migration-url and the SQL key, plus private scratch.
It maps the exact reviewed local owner connection, clears inherited overrides,
forces a read-only session default and starts a read-only repeatable transaction.
It has no API token, retries, TTL or owner-triggered deletion, and retains the
existing non-root/security/resource/placement profile.

Parent/worker/Job deadlines are 1,800/240/180 seconds. Connect/API requests are
bounded to ten seconds; SQL lock/statement/idle-in-transaction bounds are
five/fifteen/fifteen seconds. No advisory lock or mutating SQL is used.

## Checks and proof limits

Require the migration owner session, expected role flags, owned perfeng_control
schema/objects and the eight ordinary tables required by the grant policy.
Require exactly eighteen distinct selected ledger rows with exact hashes,
non-null finite application timestamps, and no missing/unknown/duplicate version.
Use exact C-collation name/hash comparisons and refuse row security on the ledger.
Failed checks raise a SQL exception and cannot complete the Job successfully.

This checks recorded migration identity and ownership. It does not reconstruct
or compare every table/index/constraint definition, attest a backup or prove
application correctness. Successful migration/grants alone do not substitute for
this observation. Successful qualification is historical evidence, not permission
to mutate or resume after state may have changed.

## Failure, rollout and verification

Invalid connection input exits 64 before psql. SQL failure returns the constant
`[SCHEMA-QUALIFICATION]` diagnostic and exits 1; vendor output stays in the retained
Pod's private `/tmp/qualification.private`. Inspect it only through an explicitly
authorized path and never upload credentials/raw diagnostics. Success prints a
constant ledger/ownership qualification message and returns the Job name and
prior checkpoint. No result authorizes grants, migration, repair or activation.

Stage resources only under gitops/release-workflows. The separately approved
trusted-main initial fixture installs and runs them as described below; no
active-root or local execution selection changes.
Removing Git selection does not prune installed versions or existing authority;
withdrawal needs explicit approval. Do not delete/replace evidence or rotate
credentials as implicit repair.

Offline source-byte/static/wiring/shell tests, focused staged Kustomize/AppProject
validation and pinned Argo lint cover composition. They do not execute SQL against
PostgreSQL or prove live refusal/retention. Heavy checks stay on PR CI; an approved
complete-parent live acceptance must pass before operator cutover or
migration/grant composition. Host commands and retirement counts remain unchanged.

## Complete-parent first/repeat acceptance

[ADR 0067](decisions/0067-exercise-read-only-schema-qualification.md) authorizes
only the existing trusted-main initial job to install the three unchanged staged
resources and submit the complete parent twice with the existing pinned upgrade
request. Execution follows schema acceptance and grants ledger-read setup, while
the real adopted application is paused, before adopted grants and recovery.
Two distinct retained read-only Jobs and Workflows must prove ordered admission,
fresh quiescence, exact installed SQL/inventory/source/authority identities and
native success. SQL ledger/ownership, ACLs, rows, credentials, release records,
Secrets, SQL/S3 data and workload/storage identities must remain unchanged.

The fixture rereads both execution records, native workers, Jobs/Pods and sources
before recovery and final success. A fresh paused snapshot is required before
recovery; final SQL/data/source retention runs after the separately checked
recovery without requiring the recovered Deployment to remain paused. No
qualification failure triggers repair or recovery. Raw evidence stays private;
bounded summaries are `schema-qualification-retained.outcome.json` and
`schema-qualification-final.outcome.json`.

This adds no GitHub jobs/scenarios, requests/refs, credentials, authority or budget.
Other five scenarios remain unchanged. PR checks are offline evidence/coordinator
checks; real PostgreSQL/controller acceptance remains post-merge main-only.
SQL refusal faults and preparation-flow composition are deferred. Do not infer
full DDL equivalence, general upgrade safety or retirement from passing fixtures.
