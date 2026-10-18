# Staged release verifier package

## TL;DR

- Five Python verification modes share two stdlib modules and one immutable
  `perfeng-release-verifiers-v2` ConfigMap in `argo-workflows`.
- Argo retains sequencing, native resource waits, deadlines and persistence;
  the package owns input predicates and bounded reads/probes.
- Use v2 preflight, readiness, maintenance-checkpoint and adoption entrypoints.
  The external-state parent and all request/result data schemas remain v1.
- Retired source is identified by Git revision and SHA-256; installed v1
  objects remain retained. Source retirement does not authorize cluster pruning.
- This corrects the staged helper budget. Host cutover, original script
  retirement, live parity and production readiness remain outstanding.

## Package boundary and accounting

`gitops/release-workflows/verifiers-v2/release_policy_v2.py` contains pure
identity, readiness, quiescence and adoption predicates. Its companion
`release_runtime_v2.py` handles checked Git artifacts, environment inputs,
bounded authenticated Kubernetes reads, fixed HTTP probes and result output.
Each container invokes one mode: `preflight`, `selection`, `readiness`,
`quiescence` or `adoption`. There is no general command runner, subprocess
or custom waiting loop. Both modules mount read-only from one immutable
ConfigMap. The existing pinned images, resources, security contexts and
ServiceAccount boundaries remain in force.

The previous staged code totalled 852 lines: 757 in three standalone helpers,
87 in embedded preflight and eight in embedded selection. The package has
438 policy lines and 270 runtime lines: **708 physical lines
across two files**, including blanks and comments. This includes code previously
embedded in the preflight and task-selection templates as well as the three
standalone helpers. No embedded Python remains in staged WorkflowTemplates.
The inventory test counts every staged `.py`, checks the module/line limits,
stdlib imports and package mounts, and refuses embedded Python containers.

These two files consume the entire residual Python budget. Any additional
deployment Python requires further simplification or an explicitly reviewed
budget decision; moving it into another template or helper is not retirement.
The [original baseline](deployment-script-retirement.md) remains 13 host files
and 3,545 lines. None is deleted or counted as retired by this package change.

## Version and compatibility boundary

Preflight, task selection, verification children, readiness, maintenance and
adoption parents receive new `-v2` Kubernetes names and source paths. The stable
`perfeng-release-external-state-v1` parent points to v2 preflight/selection while
retaining its four inputs and outputs. Its native preparation children, native
readiness observer and adoption record operations keep their existing v1 names.
Request, preflight, readiness, checkpoint and adoption data schemas stay v1;
the exact request/lock bytes and ledger meaning are unchanged. Container modes
use explicit output parameters; callers must not depend on the former private
script templates' implicit stdout result.

[ADR 0018](decisions/0018-bound-and-retire-release-verifier-sources.md) defines
source retirement. `deployments/release-workflow-retirements.json` records each
removed path, exact historical Git commit, SHA-256, old Kubernetes name where
applicable and active replacement paths. CI compares published source to the
PR merge base: in-place edits are refused; deletions need matching provenance
and an active replacement. Existing retirement records are append-only. Later
retirement chains must resolve to active sources; cycles and template version
regression are refused.
Historical blobs remain in Git and can be read with
`git show <sourceRevision>:<path>`; verify their recorded SHA-256 before reuse.

## Staged upgrade and rollback

1. Keep the active local root and host operator path in place. Select a
   disposable fixture and review the staged Application diff at an exact commit.
2. Confirm pruning remains disabled. Sync the package ConfigMap at wave `-2`,
   children at `-1`, then parents. Both module keys must exist in the same
   namespace as their Pods. No additional RBAC is required.
3. Submit a **fresh** Workflow using the guide's v2 entrypoint and four pinned
   Git inputs. Retain old objects and in-flight v1 Workflows; do not resume or
   memoize successful v1 nodes as a v2 verification attempt.
4. If the upgrade fails, preserve failed Workflow evidence and stop new
   submissions. Review/sync the previous staged Git revision with pruning off;
   it restores source selection and parent references. Existing immutable v1
   ConfigMaps/templates remain usable. Do not overwrite a v2 ConfigMap or delete
   installed versions as an implicit repair.

Removal from desired source does not remove retained cluster resources when
pruning is off. A future live resource-retirement task must inspect in-flight
references, exact identities and recovery needs and obtain deletion approval.
No Workflow or ledger receipt is removed by this source change. Rolling back
templates does not undo external-state operations, adoption records or database
migrations; their owning recovery procedures continue to apply.

## Verification and remaining acceptance

Run `make validate` and `make workflow-lint`. The PR-only immutable-source
check uses a full local Git history and `GITHUB_BASE_REF`; it needs no GitHub or
cluster calls. Existing identity, refusal, probe-bound, output-publication and
adoption recovery tests exercise this same package.

Before cutover, exercise all five modes on a disposable kind fixture, including
first/repeat success, bad inputs, missing permissions/package keys and failed
probe recovery. Compare v1 schema evidence and confirm that resource identities,
RBAC and ledger behavior are preserved. No live fixture is selected here.
Guarded pointer advancement, maintenance writes, broader health parity,
external ledger recovery and original-script retirement remain separate tasks.
