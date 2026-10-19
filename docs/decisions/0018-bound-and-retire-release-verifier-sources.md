# ADR-0018: Bound and retire release verifier sources

- Status: Proposed
- Date: 2026-10-18
- Milestone: M06 — Production Readiness
- Relates to: ADR 0014, ADR 0016, ADR 0017

## TL;DR

- Consolidate five staged Python checks in one immutable, two-module package.
- Preserve native Argo orchestration and v1 request/result/ledger contracts.
- Permit source deletion only with exact historical bytes, hashes and active
  replacements; continue rejecting edits to published versions.
- Retain installed versions and recoverable Git history without granting live
  pruning authority or claiming host-script retirement.

## Context

Readiness, quiescence and adoption introduced three helpers totalling 757
physical lines, exceeding the original two-file/708-line residual budget.
Preflight and task selection also carried embedded Python. Keeping every
published source copy active would make the measured target unreachable and
duplicate policy. Published templates still need reproducibility and safe
in-flight execution, as described by ADR 0016.

## Decision

Publish `perfeng-release-verifiers-v2` with a pure policy module and a bounded
I/O module. Move preflight and selection out of embedded script sources into
explicit container modes. Native Argo templates retain sequencing, waits,
deadlines and create-only persistence. Introduce new names for changed children
and parents, preserving the stable external-state parent's v1 interface and
all existing v1 data schemas. Do not add dependencies, clients, controllers or
privileges.

Remove superseded source copies from the current tree using an append-only
retirement index. Each record binds the path to an exact Git commit and content
hash, the Kubernetes name when applicable, and active replacement sources.
The immutable-source CI gate rejects unrecorded deletions, changed published
bytes, altered retirement history, mismatched historical provenance and inactive
replacements. Full Git history is required for this offline gate.

Installed objects and in-flight Workflows remain retained with pruning disabled.
Source retirement is distinct from live deletion, which requires a separately
reviewed reference/identity/recovery audit. Git history preserves old source
without retaining duplicate implementations in the active Python budget.

## Alternatives considered

- Keep v1 copies indefinitely: simple reference retention, but duplicates active
  code and prevents the residual-budget goal.
- Mutate existing versioned files/ConfigMaps: smaller diff, but breaks published
  identity and safe recovery.
- Move predicates into inline templates or a larger framework: hides rather
  than reduces complexity, or adds local footprint for the same finite checks.

## Consequences and compatibility

The package fits exactly two files/708 physical lines, including formerly
embedded code. Shared parsing, artifact checks and identity predicates reduce
duplication. It leaves no room for new Python without further simplification.
The broader original host-script retirement target remains unsatisfied.

Public staged entrypoint names move to v2 except the stable external-state v1
parent. Explicit parameters/data contracts remain unchanged; private implicit
script stdout results are not a compatibility API. Operators must submit fresh
Workflows after an upgrade. In-flight old objects are retained, which requires
an eventual separately authorized cluster-retirement policy.

## Security, rollout and recovery

The same pinned Python image, restricted Pods, named reads, bounded probes,
credential mounts and create-only ledger operations remain. No live mutation
is part of implementation. Follow the [package guide](../release-verifier-package.md)
for staged wave ordering and prior-revision rollback without pruning. Template
rollback does not roll back state, receipts or forward-only migrations.

## Validation

Keep the original success/refusal/recovery assertions against the shared package.
Gate inventory, line counts, stdlib imports, container mounts and audited source
retirement. Run complete non-live validation and offline Argo lint. Live parity,
cutover and M06 admission/recovery are still outstanding.
