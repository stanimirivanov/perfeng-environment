# ADR-0041: Exercise Deployment interruption and checked CI recovery

- Status: Proposed (owner approved scope in conversation)
- Date: 2026-10-27
- Milestone: M06 — Production Readiness
- Extends: [ADR 0040](0040-exercise-maintenance-partial-pause.md)

## TL;DR

- Add Deployment to the existing disposable maintenance interruption acceptance.
- Stop after its real scale-to-zero patch, before the interrupted parent's checkpoint.
- Require paused retention and a separate unchanged read-only checkpoint before sync.
- Permit exactly one additional same-ReplicaSet server Pod replacement in checked recovery.
- Keep original evidence and use CI for the heavy verification on the busy host.

## Decision

The owner approved Deployment interruption and explicit recovery in the existing
trusted-main initial job. Preserve the happy-path pause/recovery and root/child
interruption cases. Add one reversible CI-only parent with a suspension directly
after the unchanged Deployment patch. Preserve its real readiness, baseline
planning, native guards/writes, identity, mutex, deadlines and output wiring.
Verify the installed spec before submission and after completion. Require the
same exact Stop evidence as ADR 0040 and no checkpoint or later worker in this
interrupted parent.

After proving the Stop, use a 120-second read-only polling deadline
for the same Deployment to reach its current observed generation, desired and
reported replicas zero, and matching-Pod absence. API requests have ten-second
bounds. A foreign UID, deletion or changed desired replicas refuses immediately.
Obtain a raw PodList using the fixed production selector, retaining nonempty
resourceVersion and absence of pagination. Keep normal workload/Pod snapshots
and the raw list as separate private evidence.

Compare paused state with the current checked running baseline: only the owned
Deployment's replicas and disappearance of its server Pod may differ. Preserve
foundation Pods/storage, database/S3 data, Secrets/configuration, root/child
specs/ownership and both original ledger records. Normal paused Application
status is checked through the quiescence contract; converged-root validation is
required again after recovery.

Submit the unchanged `perfeng-release-maintenance-checkpoint-v3` independently
with the same pinned inputs. Require a distinct Workflow UID, completed original
Stop before this preflight, successful read-only workers, exact selected input
identity, schema-valid checkpoint and matching cluster/resource identities. Replay
the unchanged quiescence policy against the independent API snapshot. Native and
fixture reads occur at different times; resourceVersions need not match across
those independent samples, but each actual verifier retains its unchanged stable
sampling checks. The interrupted parent still emits no checkpoint.

Only then manually sync the selected child and verify native/HTTP health. Reuse
the existing retention/replacement checks to permit one additional server Pod
name/UID under the same ReplicaSet, with unchanged spec except the already
approved generated ServiceAccount token-volume name and corresponding mounts.
Require fresh root convergence and full retained-state checks. Preserve all prior
snapshots; advance later receipt tests only to the fixed new checked recovery
snapshot and verify unchanged adoption reuses both records. Root auto-sync stays
disabled and the six other children remain skipped.

## Security, failure and verification

This is the approved exception to ADR 0040's no-recovery rule for Deployment
only. Setup, stop, settlement, retention or checkpoint failure must prevent sync;
recovery verification failure must prevent baseline advancement and later tests.
There is no EXIT recovery, retry of successful nodes, force deletion, marker
write, ledger repair or credential change. Existing owned-cluster cleanup remains
the cleanup boundary. Removing this fixture rolls back coverage only and does not
introduce a production recovery command.

Raw Workflows, worker/API/Secret snapshots and command diagnostics stay private;
publish sanitized barrier reports, verified fixed outcomes and the validated
independent checkpoint. Existing job, six scenarios, temporary refs, credentials,
rights and 60-minute budget remain unchanged. No production template, schema,
immutable verifier, local infrastructure action or manual CI dispatch changes.

The owner requested CI-heavy validation because the local host is busy. Run
lightweight local formatting/lint/diff and focused pure checks; existing PR CI
runs full cross-platform verification and offline workflow checks. Its existing
Linux job also generates all three interruption copies and lints them with the
already checksum-verified Argo CLI, without cluster or credential access. Report pending
CI honestly at handoff and stop after PR creation. Full parent/controller proof
remains the existing post-merge trusted-main job. Lingering-Pod fault injection,
missing rights, response loss, operational recovery/cutover and measured script
retirement remain separate gates.
