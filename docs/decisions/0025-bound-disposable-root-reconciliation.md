# ADR-0025: Bound root reconciliation in disposable acceptance

- Status: Proposed
- Date: 2026-10-23
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved actual root reconciliation in disposable trusted-main CI.
- Keep root automatic sync disabled and all six non-control-plane children skipped.
- Prove root-owned object convergence and retention; full-tree health and release adoption remain separate.

## Context and decision

ADR 0024 excluded root activation while foundation Applications were qualified.
PostgreSQL and SeaweedFS now have independent controller acceptance. The next
prerequisite is real root convergence, which the published adoption contract
requires at the request's selected revision. The owner explicitly approved this
bounded extension of ADR 0024's lifecycle scope. Existing source credentials,
repository URLs, controller versions and job permissions remain unchanged.

After foundation acceptance, render the unchanged root Kustomize tree and refuse
inventory expansion beyond four Namespaces, three AppProjects and seven child
Applications. Before removing root's skip annotation, annotate existing children
and create missing children with `argocd.argoproj.io/skip-reconcile: "true"`:
PostgreSQL, SeaweedFS, sample SUT, native search, resource metrics and benchmark
authority. Keep control-plane active with its committed manual policy.

Root retains its canonical `main` source and disabled automatic sync. Each manual
operation names the selected full commit, uses apply strategy and forbids prune,
force, replace or manifest overrides. Require fresh first/repeat/corrected
operations, exact source/revision, complete resource inventory, `Synced`/`Healthy`
and no conditions. If `main` moves, fail selection comparison rather than changing
source or syncing a newer revision. Deliberate `perf-sut` environment-label drift
must remain `OutOfSync` until explicit correction.

## Ownership and retention

Root restores committed child specs, including foundation `main` sources and
their committed automation settings. The separately managed skip annotations
must survive normal client-side apply; do not save them as root-owned desired
configuration, filter the source or rewrite production manifests. The selected
controller honors these annotations. Validate all six skips, unchanged child
operation records and preserved child UIDs after every root operation. Do not
unskip these children later in the same fixture.

Compare workload specs and Pod UIDs, database schema/roles/ACLs and canary/sentinel
OIDs/bytes, PVC/PV binding, object metadata/bytes, proxy configuration and
credentials before/after each operation. No workload rollout or storage repair
is authorized by this slice. Keep raw snapshots private; upload only selected
root/child identity and controller evidence plus constant drift outcomes.

## Verification, rollback and limits

Offline tests reject inventory expansion, stale/wrong revisions, automatic
sync, source overrides, missing skips, child operations and incomplete evidence.
Run repository validation and Workflow lint. Actual controller reconciliation
and retention run only in the existing post-merge trusted-main owned kind job.
No local or retained environment is deployed.

Rollback removes the final CI phase; owned cluster teardown removes its state.
This does not prove skipped child health, full-tree activation, complete release
adoption/promotion, production authorization, recovery or host-script retirement.
No ledger writes, new credentials, controllers or published verifier/template
changes are introduced. The original retirement baseline receives zero credit.
