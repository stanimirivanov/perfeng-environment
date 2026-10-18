# ADR-0017: Record explicit local release adoption in an isolated ledger

- Status: Proposed
- Date: 2026-10-18
- Milestone: M06 — Production Readiness
- Supersedes: None; implements the adoption boundary in ADR 0015

## TL;DR

- The owner selected immutable Kubernetes ConfigMap receipts and a guarded current-release pointer for the staged local pilot.
- Keep runtime ledger records in a dedicated namespace, outside Argo CD desired state and Workflow garbage collection.
- Introduce explicit existing-release adoption first: native create-only operations can initialize a pointer or verify an exact retry, never advance an existing pointer.
- An adopted baseline is not a completed deployment, migration approval, full health attestation or disaster-recovery solution.

## Context and decision

ADR 0015 requires an explicit adoption decision when an existing environment lacks a prior receipt. The owner selected a ConfigMap ledger before implementation. The current preflight, readiness and maintenance outputs are transient observations; none is an accepted-release record. The root also tracks mutable `main`, so a selected Git commit cannot be assumed to be the root's observed revision.

Use the separately bootstrapped `perfeng-release-ledger` namespace for runtime state. A receipt is named `receipt.<first 32 SHA-256 hex characters>.<last 32 characters>`. Its immutable `binaryData` stores exact UTF-8 request bytes and a versioned adoption payload. The payload binds the request digest, environment/cluster, selection revision, root UID, control-plane Application UID, deployment revision and image. First Workflow UID and request-delivery revision are provenance annotations. Preserve the original record on retry, including its original provenance.

The explicit `perfeng-release-adopt-existing-v1` entrypoint performs fresh readiness/preflight, checks an owned healthy root at the exact selected resolved revision and compares existing ledger objects. It creates an absent matching receipt, rereads it, rechecks the root, then creates the absent `perfeng-local-current` pointer. The pointer binds receipt name **and UID**, request digest and cluster UID. Final reads verify receipt/pointer compatibility and an unchanged root observation before publishing success. Plan nodes emit no success result.

Adoption uses the request envelope to identify a baseline, with `expectedCurrent: null` and manual activation intent. This distinct entrypoint does not execute the request as an empty-environment deployment. Missing records on a running environment never authorize the normal release path. Only a trusted operator may deliberately submit this adoption after reviewing existing state and recovery history. Self-asserted input fields are not authorization.

Creation is compare-and-create: a concurrent create conflicts rather than overwriting state. A matching immutable receipt without a pointer is a recoverable partial attempt. A pointer without its matching receipt, a replaced receipt UID, foreign ownership or changed content is a conflict, not a repair opportunity. An existing matching pointer is an exact retry; a different pointer stops before any write. This version grants no ledger patch or delete permission. Pointer advancement is deliberately not exposed until a separate promotion implementation checks the incoming `expectedCurrent`, live state, authorization and completed release evidence, with UID/resourceVersion/digest conditions.

## Security, compatibility and recovery

ConfigMap create cannot be constrained by resource name through ordinary RBAC. Isolate get/create permission in the ledger namespace; do not grant it over workflow configuration. Argo CD's staged Project adds only the ledger destination, retaining its existing kinds and excluding `argocd`. Namespace and ledger access are explicit bootstrap resources. The workflow receives the existing named readiness/preflight reads and a separate bootstrap root-read grant. It receives no workload, Job, Secret or Application writes.

Argo native resource templates own persistence. A versioned pure planner checks bytes, ownership and compatibility; it has no Kubernetes client, subprocess orchestration or wait loop. Its deployment-specific Python counts toward retirement, including the additional file. Existing published v1 scripts are not changed or silently consolidated. The retirement gate remains unmet and must be resolved before operational cutover.

The adoption profile caps request bytes at 64 KiB, below both ConfigMap and workflow-output limits after encoding. The original v1 request schema is unchanged; larger valid requests are refused by this local consumer. Receipts have no Workflow owner reference, TTL or GitOps tracking and are never managed by a Helm release or reconciled as desired state. Removing the staged Application must not remove runtime history. Immutable ConfigMaps prevent data edits, not deletion or privileged metadata changes. This is trusted local-operator state, not a tamper-evident audit service or M06 multi-user admission.

Root/readiness samples and pointer creation are not one Kubernetes transaction. Operators must exclude concurrent syncs and merges during adoption. Fresh root comparison detects sampled changes, not all possible activity. A failure after pointer creation leaves records for explicit inspection; a green saved receipt cannot waive fresh promotion or migration checks. Neither a narrow readiness result nor this adoption result claims complete platform health. Cluster deletion loses the ledger unless separately exported/backed up; no export, restore or non-local ledger portability is implemented here.

## Alternatives and validation

- Workflow status alone has controller retention/cluster lifecycle limitations and provides no guarded current identity.
- Git remains reviewed intent; automatically committing operational history would introduce another promotion owner.
- An external database or object-store ledger could survive cluster loss but requires additional provider, authorization and transaction/recovery decisions. Defer it until non-local requirements justify it.

Run `make workflow-lint` and `make validate`. Tests cover exact bytes, stale/root conflicts, immutable content, dangling/replaced references, partial retry, create-only authority and success publication only after final verification. Disposable-kind concurrency, ambiguous create outcome, retention and denied-permission exercises remain mandatory before using the pilot operationally. Follow the [adoption guide](../release-adoption.md).

References: Kubernetes [immutable ConfigMaps](https://kubernetes.io/docs/concepts/configuration/configmap/#immutable-configmaps) and [conditional API updates](https://kubernetes.io/docs/reference/using-api/api-concepts/#updates-to-existing-resources).
