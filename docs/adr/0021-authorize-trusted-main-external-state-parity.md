# ADR-0021: Authorize trusted-main external-state composition

- Status: Proposed
- Date: 2026-10-22
- Milestone: M06 — Production Readiness
- Supersedes: ADR 0019 only for its restriction of package-read permission to the read-only child-parity job

## TL;DR

- The owner approved `packages: read` alongside existing `contents: write` in the trusted-main acceptance job.
- Reuse the ephemeral `GITHUB_TOKEN` and existing package-side repository Read grant; no PAT or package setting change.
- Run the unchanged complete external-state parent against transient dependencies and the existing real-UID request.
- Keep PR integration read-only and leave production deployment, recovery and script retirement gated separately.

## Context and decision

ADR 0019 enabled private-registry manifest access in read-only child parity.
ADR 0020 subsequently authorized temporary Git-request publication on trusted
main with contents write. Complete external-state composition requires both
capabilities in the same disposable cluster. The owner explicitly approved
the combined job permission, rather than changing the credential or deferring
the gate. All other ADR 0019 and 0020 boundaries remain unchanged.

The main-only job uses its one run-owned request/ref after complete preflight
and maintenance. It runs the production external-state parent without template
overrides: preflight, lock selection, bucket, database roles and registry.
Database and S3 endpoints reuse existing transient fixtures. Setup is shared
through explicit CI functions so child and parent parity retain the same
credentials, tool images and direct internal endpoint model.

## Security and operations

The job token is repository-scoped, not ref-scoped, and now has package-read
authority too. Both the Git artifact Secret and disposable registry Docker
config contain this token. Naming a Secret read-only does not attenuate it.
Only reviewed main code runs this boundary; pinned controller/executor/worker
images and fixture administration are trusted. Existing package owners must
retain the repository Actions Read grant. Do not change visibility, add stored
credentials or fall back to anonymous reads after authorization failure.

Only fixture administration creates credentials, replaces its deliberately
invalid bucket client and toggles the unsafe test role. Release Workflows reuse
the staged namespace-scoped Job grants and cannot rotate Secrets, delete Jobs,
exec into Pods or modify workloads/Applications. Job Pods have no API token.
Password hashes, raw logs and Secret snapshots remain in the private fixture;
upload only non-secret evidence and constant diagnostic signals. Existing
expected-SHA ref deletion and bounded owned-cluster cleanup cover this phase.

## Validation, rollout and limits

Offline tests cover complete/partial outputs, exact preflight and Job identities,
missing selection success, downstream execution after refusal, safe fixture
registration and unchanged child behavior. Trusted-main CI must prove first/
repeat success, preflight rejection with zero Jobs, denied-bucket refusal before
database/registry, unsafe-role refusal before registry and fresh correction.
Retain all Workflows/Jobs and compare private credential/password and public
bucket/object identities. Live acceptance remains pending that post-merge run.

This is finite preparation against direct S3/emptyDir and transient PostgreSQL,
not a production chart/storage proof. Registry digest access is not full
manifest-byte hashing or image-layer retrieval.

The v1 bucket script hides its lookup stderr: bracketing 403 probes establish
the bad-client fixture, but its generic failed-Job diagnostic proves only the
stopping boundary, not that Job's authorization-versus-provider/network cause.
Do not count it as per-Job authorization parity or retirement evidence.
Real readiness, adoption, migrations, promotion, backup/export/recovery and legacy retirement remain
separate gates. Reverting this phase and the new main-job package permission
changes no retained environment; keep the original child-parity permission.
