# Immutable release request and promotion

## TL;DR

- [ADR 0015](decisions/0015-bind-releases-to-reviewed-inputs.md) and the
  [v1 JSON Schema](../deployments/release-request-v1.schema.json) define the
  request a future release workflow will consume.
- The request pins a reviewed environment Git commit and exact deployment-lock
  bytes; those locks continue to select images, charts and runtime bundles.
- `expectedCurrent` prevents an upgrade from silently overtaking another
  release. `null` is for a provably empty environment, not an existing cluster
  with a missing receipt.
- The current `perfeng-local` release remains manual. The
  [M04 cutover guide](kubernetes-runtime-deployment.md) is still the operating
  procedure; this contract alone does not deploy anything.

## What the request binds

One request has five parts: schema version/kind, environment identity, target
Git selection, deployment-lock digests, and promotion intent. The
[historical example](../tests/fixtures/release-request-v1.json) uses the
environment selection at commit `80bd42a6bd4f4da86b751fdea7fe21e59a523e64`.
It is a schema fixture, **not** an approved command or a claim about the
current cluster. Its `expectedCurrent: null` illustrates a create-only
operation and must not be reused against a running environment.

| Input | Required interpretation |
|:--|:--|
| `environment.name` | Logical environment; `perfeng-local` has additional v1 constraints. |
| `environment.clusterUID` | Exact UID of `kube-system` in the target cluster. It changes on cluster recreation and must be read through the selected, authorized kubeconfig. |
| `selection.repository` and `revision` | The exact `perfeng-environment` origin and a full, reviewed Git commit, never `main`, a tag, or a local working tree. |
| `selection.gitopsPath` | GitOps tree in that commit. For `perfeng-local`, it is `gitops/environments/local`. |
| `selection.lockDigests` | SHA-256 of each named lock's **raw committed bytes** at that same commit. Local v1 requires the control-plane, infrastructure and native-target locks. |
| `promotion.expectedCurrent` | SHA-256 of the prior accepted request's raw bytes, or `null` only when no release and no conflicting live state exists. |
| `promotion.activation` | Desired activation mode. Local v1 and every forward-only migration require `manual`; a future automated mode needs separate authorization and evidence. |
| `promotion.migration` | `none`, or `forward-only` with an exact migration identifier such as `0018`. It does not authorize rollback or imply a backup exists. |

The SHA-256 identity of the request itself is computed over its exact UTF-8
bytes, including whitespace and the final newline. Store those bytes with the
later release receipt and submit the same bytes on retry. JSON parsing must
reject duplicate keys before applying the schema. Reject unknown fields rather
than interpreting future data under v1 rules. Neither the request nor a schema
pass is proof of human review, image provenance, registry access, a safe
cluster, or a successful release.

## Producer, review and promotion boundary

1. The upstream project publishes immutable images and bundles. Its CI may be
   Tekton, Dagger or another implementation. It does not deploy Perfeng.
2. A reviewed `perfeng-environment` commit selects published artifacts through
   its existing lock files and GitOps manifests. The request names that commit
   and hashes the lock blobs read from it; a dirty checkout is not input.
3. A future release consumer validates the schema, exact Git origin and commit,
   each lock hash and lock-internal artifact pin, cluster UID, and approved
   environment policy. It then compares `expectedCurrent` to the durable prior
   receipt and to observed root/child Application state before a write.
4. A finite Argo Workflow may prepare external state and record a result under
   [ADR 0014](decisions/0014-separate-release-and-run-orchestration.md).
   Argo CD alone reconciles workloads. For M04, the human pause, reviewed
   publication/merge and manual child Sync remain mandatory.
5. A later receipt records the exact request bytes/digest, observed revisions,
   completed operations and failure/recovery status. The request is the
   intended transition; the receipt and live observations establish what
   actually happened.

There is no live consumer or receipt store in this PR. In particular, the
current root Application tracks `main`, so a request's full Git revision does
not by itself pin what Argo CD has already observed. A future cutover must
check or bind the exact root and child revisions before reporting promotion.
Until that proof exists, continue the existing operator guide.

## Failure and recovery rules

- A mismatched cluster UID, Git origin/commit, lock digest, prior receipt or
  live Application revision stops before mutation. Do not repair by changing
  the request to match an unreviewed cluster or by using `expectedCurrent: null`.
- A failed or partial attempt retains its identity and evidence. An exact-byte
  retry may reuse only resources whose owner and content match; a new request
  requires a new preflight and review.
- A forward-only migration cannot be undone by reverting Git. For current M04
  migration 0018, preserve the paused service and follow the
  [coordinated recovery path](kubernetes-runtime-deployment.md#authority-failure-and-rollback).
- A new cluster incarnation or missing prior receipt requires explicit
  qualification/adoption, not an inferred first release.

The schema is verified offline by the focused `tests/test_release_request.py`
suite under `make verify`. Existing lock validation remains responsible for
semantic chart, source and bundle consistency. No live Taskfile command uses
this request yet.
