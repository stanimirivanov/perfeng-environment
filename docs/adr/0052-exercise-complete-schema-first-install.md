# ADR-0052: Prove complete schema-parent first installation

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-29
- Milestone: M06 — Production Readiness
- Extends: [ADR 0051](0051-exercise-schema-job-create-denial.md), [ADR 0050](0050-exercise-paused-schema-migration-repeats.md)

## TL;DR

- Replace only the initial leg's first successful CI migrator with the unchanged complete parent.
- Use the naturally empty database and explicit paused bootstrap placeholders; never reset data.
- Create the immutable server pull Secret earlier and require exact verified reuse later.
- Prove native parent/Job/Pod execution, all 18 migrations and unchanged surrounding identities.
- Retain the install Job/Workflow/Secret through the later paused suite before existing recovery.
- Keep other scenarios, production definitions, rights, budgets and local infrastructure unchanged.

## Decision and authorization

The owner approved bounded first-install acceptance. Preserve the existing
runtime-role SQL denial and independent checkpoint before first execution.
In the trusted-main initial leg only, replace its first CI-only migration
Workflow with the published `perfeng-release-schema-migration-v1` parent using
the existing forward-only parent request. Other five scenarios retain their
current first invocation, with no new jobs, scenarios, refs or request files.

The database is naturally empty at this point. Verify absent control schema,
zero public/control application relations, denied runtime CREATE and granted
migration CREATE immediately before submission. Refuse preexisting native
migration Jobs. Existing root/child Applications are explicit auto-sync-disabled
bootstrap objects, and the zero-replica Deployment is the never-started fixture.
This proves fresh installation against those objects, not real adopted server
readiness. The later adopted pause/repeat suite remains independent.

## Credential timing and native authority

Create the fixed `perfeng-control-plane-registry` Secret earlier, cloning only
the already-approved fixture registry data. Refuse an existing target; use a
create-only immutable Secret and retain its returned UID/type/data/metadata.
No additional token is issued, forwarded or granted new rights. Before server
setup, read that Secret and compare it with the original and existing source
credentials. Generate/create only the runtime/authentication Secrets in this
leg; never recreate, rotate or replace the retained pull Secret. Other scenarios
still create all three inputs at the original boundary.

Install the same staged migration templates, intent policy and approved
migration authority earlier. Validate published specs/policy and stable UIDs
before/after execution. Use the shared pure parent-execution verifier for exact
Git bytes/digest, request/selection/cluster identities, real ordered
preflight/selection/intent/quiescence/migrate workers and migration identity.
Require the unchanged fixed Job name/provenance, image/command/Secret references,
security/resource declarations, bounded single successful Job Pod and all 18
selected schema ledger versions/checksums with the migration owner. No retries,
Job deletion, SQL repair or alternate migrator are permitted.

## Retention and recovery gate

Compare existing platform workloads/config/Secrets/Services/claims, PV bindings,
running ready foundation Pods, paused Applications, cluster identity and SQL
roles before/after. Ignore only controller status and metadata
resourceVersion/managedFields in inventory comparisons; UIDs, declared fields,
credentials and all other metadata remain checked. Schema creation is the
intended SQL mutation; the starting application database has no data to replace.
The selected Job mounts no S3 or runtime credential and receives no API token.

Preserve the independent checkpoint and existing image-parity projections;
validate their fixed server pull/PGAPPNAME variant only for the first complete
parent. Raw parent/Pod/credential observations stay private. Bounded startup
projections and validated outcome records contain no credential/log data.

At the later paused-suite baseline, require exactly the original install Job,
including its spec/UID/provenance. Refusal cases must add zero Jobs; repeats
must add exactly one each while preserving all prior Jobs. Final retention
requires three Jobs, the four later parent Workflows plus the original install
Workflow, and exact pull-Secret retention. Verify installation retention before
publishing the complete suite outcome or permitting existing checked recovery.
No new Pod replacement, baseline advancement or recovery path is authorized.

## Verification, rollout and remaining gates

Focused offline tests cover first-install shapes and refusal, Secret reuse and
drift/replacement, prior-Job preservation and shell fail-stop/observer cleanup.
Lint/type/docs/shell checks run locally; heavy full/cross-platform checks run
on PR CI. Live proof remains post-merge under the existing trusted-main guard
and 60-minute budget. No local cluster or Docker execution is performed.

Rollback restores the original initial first invocation, original credential
timing and empty paused Job baseline. Production templates/host commands remain
unchanged. Partial/ambiguous SQL failure, retained-release upgrades/admission/
recovery, production multi-user security, local cutover and measured script
retirement remain separate work. No deployment Python is retired or added.
