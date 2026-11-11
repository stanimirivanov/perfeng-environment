# Contract distribution plan amendment

- Status: Approved scope amendment, subject to repository review
- Date: 2026-11-11
- Milestone: M06 — Production Readiness, preparatory work only

## TL;DR

- Preserve the approved SR-1 through SR-32 identifiers and Option 0 architecture.
- SR-29 completed qualification with `NOT_ADMITTED`; issue closure is not success.
- Defer SR-31 and allow SR-32 to record the failed admission now.
- Positive admission still needs passing evidence for all mandatory capabilities.
- This amendment authorizes no registry deployment or consumer migration.

## Scope and retained sequencing

This is the maintained amendment to the approved 32-PR contract-distribution
plan, not a replacement implementation schedule. SR-1 through SR-28 delivered
the Option 0 distribution path and retain their original scope and dependencies.
The [SR-27 campaign integration](https://github.com/stanimirivanov/perfeng-environment/pull/386)
and [SR-28 provider retirement](https://github.com/stanimirivanov/perfeng-contracts/pull/108)
are merged. Individual receipt scope remains authoritative for claims of live
verification. The supported path uses signed immutable distributions, independent
tool/contract pins and offline consumer resources. Historical inputs, signature
checks and rollback paths are not retired by this amendment.

[ADR 0073](../decisions/0073-defer-schema-registry-admission.md) records the
approved Option C no-go/defer decision and its revision-bound source evidence.

| Task | Disposition | Dependency and evidence |
|:--|:--|:--|
| SR-29 — Qualify registry capabilities | Completed, `NOT_ADMITTED` | Required current/historical corpus, compatibility and byte-identity checks failed; reference checks passed. |
| SR-30 — Qualify OpenBao identity and delivery | Local synthetic qualification passed | Passing local fixture is bounded evidence, not hosted identity or persistent recovery. |
| SR-31 — Qualify persistent registry and secrets recovery | Deferred | Still blocked on passing SR-29 and sufficient SR-30 qualification. No recovery receipt exists for this task. |
| SR-32 — Record Option C admission decision | Negative decision recorded by ADR 0073 | Delivered SR-28, completed failed SR-29 evidence, bounded SR-30 evidence and explicit SR-31 deferral permit a no-go record. They do not permit positive admission. |

## SR-31 — Deferred recovery qualification

- **Owning repository** `perfeng-environment`, prerequisite work from M06.
- **Depends on** passing revision-bound SR-29 and SR-30 evidence. A closed issue,
  missing observation or deferred task cannot satisfy either prerequisite.
- **Reactivation** needs a reviewed candidate or adapter that passes the required
  corpus, reference, compatibility and exact-byte checks, plus explicit disposition
  of actual workload/CI identity, Agent delivery, registry reload and TLS gaps.
- **Retained acceptance** independently retained backups restore registry
  database/auth state and OpenBao state, exact supported content and usable
  authority. Partial publication stays unselectable. Revoked credentials remain
  rejected across the qualified topology, including failed-publication and
  credential-cache recovery cases.
- **Verification gap** the original proposed `make registry-recovery-qualification`
  and recovery fixture are not implemented. They must be delivered with SR-31
  and produce a passing receipt before positive admission. Run `make verify`
  with that implementation. No check is skipped to label this task complete.
- **Rollback and retirement** qualification remains disposable and does not deploy
  production services. Preserve Option 0 throughout. No offline input or recovery
  path may be retired based on a qualification-only result.

## SR-32 — Negative decision now, positive admission separately

- **Owning repository** `perfeng-environment`.
- **Changed dependency** the original SR-28 plus passing SR-29/SR-30/SR-31 chain
  remains mandatory for a positive decision. A negative decision may instead
  depend on delivered SR-28 and completed failing SR-29 evidence, with the
  SR-30 evidence boundary and SR-31 deferral stated explicitly.
- **Acceptance now** ADR 0073 records the candidate, failed required capabilities,
  evidence limits, ownership and reopening criteria. The roadmap retains Option 0
  and does not mark Option C admitted, SR-31 complete or M06 production-ready.
- **Verification now** source/diff review, `make docs-check`, `make verify` and
  `make validate` validate this documentation-only decision. The original proposed
  `make registry-decision-check` and admission-record test do not exist. They are
  deferred with future positive-admission enforcement, not reported as passing.
- **Future positive decision** requires a separately reviewed superseding ADR,
  passing revision-bound SR-29/SR-30/SR-31 receipts and mechanical checks refusing
  failed, missing or mismatched evidence. Administrative issue closure is never
  receipt admission. Any new candidate or changed qualification scope requires
  fresh applicable evidence.
- **Rollout and rollback** merge documentation only. Reverting the amendment does
  not grant admission. Deployment and consumer migration require another approved
  plan. No infrastructure or consumer resources are retired by SR-32.

## Completion boundary

After review and merge, this amendment closes SR-32 as a negative decision. The
supported Option 0 delivery work is complete. The full list must still show
SR-31 as deferred and Option C as not admitted. Reopening that track is optional
future work, not an unrecorded condition of the supported architecture.
