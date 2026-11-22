# ADR-0015: Bind release promotion to reviewed immutable inputs

- Status: Proposed
- Date: 2026-10-17
- Milestone: M06 — Production Readiness
- Deciders: Project owner and performance platform maintainers
- Supersedes: None
- Superseded by: None

## TL;DR

- A release request identifies one environment and cluster incarnation, one
  exact `perfeng-environment` Git commit, and SHA-256 digests of its deployment
  lock files. The locks remain the artifact source of truth.
- Promotion compares the expected current release identity before acting.
  `null` means create only when there is no current release.
- The request's exact UTF-8 bytes are its retry identity; changing any byte
  creates a new request. Git review and authorization remain external gates.
- A forward-only migration always requires manual activation. The current
  `perfeng-local` selection requires manual activation even without migration.
- This decision defines input and validation only. It does not submit a
  Workflow, alter Argo CD sync policy, or approve a live release.

## Context

[ADR 0014](0014-separate-release-and-run-orchestration.md) assigns finite
release steps to Argo Workflows, continuous workload reconciliation to Argo CD,
and performance Runs to the Perfeng control plane. The environment already has
separate immutable [control-plane](../../deployments/local-control-plane.json),
[infrastructure](../../deployments/local-infrastructure.json) and
[native-target](../../deployments/local-native-target.json) locks. Each has
its own validation and may reference upstream images, charts and bundles.

Passing ad hoc script flags or copying all lock fields into a second manifest
would create inconsistent release selections. A future workflow also needs an
unambiguous target, a stale-promotion guard, and a retry identity before it
can safely replace the current manual sequence. The current root Application
tracks `main`, while the M04 control-plane child remains manual; the request
must not pretend that a pinned Git commit has already been activated.

## Decision

Define the environment-owned
[release request v1 schema](../../deployments/release-request-v1.schema.json).
Its fields are deliberately small:

| Field | Meaning |
|:--|:--|
| `environment.name`, `clusterUID` | Exact target environment and observed `kube-system` namespace UID. A recreated cluster is a different target. |
| `selection.repository`, `revision`, `gitopsPath` | Reviewed environment repository, full Git commit and GitOps tree. Branches and tags are not release revisions. |
| `selection.lockDigests` | Repository-relative deployment lock paths and SHA-256 of their exact committed bytes at `selection.revision`. The locks carry upstream artifact identities. |
| `promotion.expectedCurrent` | SHA-256 identity of the previously accepted request, or `null` for an empty environment. It is a compare-and-swap precondition, not an instruction to overwrite. |
| `promotion.activation`, `migration` | Manual or automated activation intent and whether a named forward-only migration is involved. Schema admission alone never grants execution authority. |

The request is immutable after review. Its identity is `sha256:` plus the
lowercase SHA-256 of the **exact UTF-8 request bytes**. Consumers must retain
those bytes rather than reserialize JSON before hashing. An exact-byte retry
may inspect and resume only an owned, compatible partial attempt. A changed
request, even with equivalent JSON meaning, receives a new identity and must
pass a new promotion preflight. Duplicate JSON object keys are invalid and
must be rejected before schema validation.

For a first deployment, `expectedCurrent: null` requires proof that no prior
accepted release or conflicting live Application state exists. For an upgrade,
the expected digest must match the durable prior release receipt **and** the
observed Argo CD/workload selection before any mutation. A missing receipt on
an already running environment is an explicit adoption/recovery decision, not
a reason to use `null`. The receipt format and live compare-and-swap execution
belong to a later implementation PR.

The schema constrains `perfeng-local` to its current GitOps path, three
existing lock files and manual activation. Any forward-only migration is also
manual for every environment. Automated activation without a migration is
syntactically possible for a future environment, but still requires a
separately reviewed environment policy and operational evidence. A request
contains no token, kubeconfig, approval claim or secret value. Human or policy
authorization is verified outside the request.

## Alternatives considered

| Alternative | Benefit | Reason not selected |
|:--|:--|:--|
| Reuse each existing lock as an independent workflow input | No new envelope. | The workflow cannot bind environment, cluster, GitOps tree and expected current state as one operation. |
| Copy every image/chart/bundle field into a consolidated release manifest | One readable document. | Creates a second artifact source of truth and extra drift rules. |
| Use a branch/tag or current working tree as the selection | Easy local authoring. | The bytes can change between approval and execution. |
| Put approval, credentials or a rollback command in the request | Single-file operator input. | Self-asserted approval is not authorization; secrets and recovery need separate trust boundaries. |
| Infer initial state when no prior receipt exists | Convenient adoption. | It can silently treat an existing M04 deployment as empty and bypass migration/recovery checks. |

## Consequences

- A future release consumer must verify Git origin, commit type, committed lock
  bytes, lock-internal pins, cluster UID, authorization, prior receipt and live
  Argo CD state. JSON Schema only validates document shape and safe syntax.
- The exact-byte identity makes retries inspectable but treats formatting
  changes as new requests. This is intentional and avoids a custom canonical
  JSON implementation.
- The environment Git commit is reviewed intent. The current Argo CD root's
  mutable `main` source is a known gap: later cutover must bind or verify its
  observed revision and the manual child revision before claiming promotion.
- A future receipt store and initial-adoption protocol are required. This PR
  does not invent either or replace current deployment locks.

## Compatibility and migration

The v1 schema and [historical example](../../tests/fixtures/release-request-v1.json)
are additive. Existing Taskfile commands, locks and Applications continue to
operate as documented. The example illustrates a create-only request and is
not a current-cluster promotion instruction. Later PRs introduce a consumer,
receipt and disposable-kind proof before any task is redirected. A breaking
field or meaning change uses a new schema version and migration plan; old
requests and receipts remain readable for recovery.

## Security and operations

The request is untrusted input. Enforce a bounded size, duplicate-key rejection,
exact Git origin and revision, safe paths, SHA-256 checks and cluster identity
before contacting a cluster or registry. Workflow service accounts and secret
providers remain separate from this document. The M04 pause, policy
publication, manual child sync and coordinated recovery remain governed by
[the cutover guide](../kubernetes-runtime-deployment.md); migration 0018 is
not reversed by a Git revert.

## Validation

Offline tests check the Draft 2020-12 schema, historical example and rejection
of mutable revisions, foreign origins, traversal, bad digests, duplicate or
unknown fields, and automated activation with forward-only migration. Run
`make verify` and `make validate`. Live admission, request receipts,
serialization, compare-and-swap and failure recovery require future
disposable-kind acceptance and are not claimed by this decision.
