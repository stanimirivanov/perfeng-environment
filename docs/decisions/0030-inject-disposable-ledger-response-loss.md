# ADR-0030: Inject a disposable native-ledger create response loss

- Status: Proposed
- Date: 2026-10-23
- Milestone: M06 — Production Readiness

## TL;DR

- The owner approved a CI-only TLS API response proxy handling the existing release-adoption ServiceAccount token.
- Commit one exact receipt, drop its successful response before sending headers, then require failed native execution and fresh direct recovery observations.
- Reuse the existing boundary job, image and grants; keep production templates and complete-parent claims separate.

## Context and decision

Quota exhaustion proves a rejected pointer write, and a suspension/termination
barrier proves interruption after a confirmed write. Neither proves loss of an
API response. Add the owner-approved transport fault only to the existing owned
disposable-kind boundary fixture. No new job, scenario, Git ref, permission,
controller, production workflow or release contract is introduced.

Generate a fixture resource template from the published native create-receipt
template. Preserve its manifest meaning, ownership policy and deadline. Wire
entrypoint inputs to the same Workflow parameters, serialize the manifest as
JSON, and supply a public fixture kubeconfig. Use normal native kubectl create:
the pinned Argo executor always adds `-o json`, which kubectl refuses with `--raw`.
The proxy serves fixed `/api`, `/apis` and `/api/v1` discovery documents locally;
no discovery request or token is forwarded upstream. Fixture-only `--validate=false`
avoids client OpenAPI lookup; the actual API still validates/admit-checks the
exact receipt. Pin the normal `kubectl-create` field manager and allow only its
exact create query with `fieldValidation=Ignore`, never dry-run or other options.
Ordinary Argo reporting remains on its direct cluster connection. This transport
adapter is a fixture copy, not unchanged complete-parent acceptance.
Declare the caller's ConfigMap volume and mount together in its PodSpec patch.
Argo resolves referenced Workflow volumes before applying that patch; a mount
introduced only by the patch cannot rely on a Workflow-level volume declaration.
Do not set kubectl's request-timeout flag here: client-go adds a timeout query
parameter, which the exact-route filter refuses. Retain the native 60-second
Pod deadline, 15-second incoming socket limit and 10-second upstream timeout.

The worker's public kubeconfig references its existing projected token file and
a fixture CA. A short-lived self-signed TLS certificate/key is generated with
the already locked cryptography library, without touching system trust. Only
the proxy Pod mounts the private key. It has no ServiceAccount token mount;
it forwards the caller's token to the fixed Kubernetes endpoint with the actual
cluster CA. Both TLS hops verify certificates. Use the existing pinned Python
image and standard-library HTTP/TLS support in the Pod, without new dependencies.

## Fault and evidence

Allow only one bounded POST to the ledger ConfigMap collection with exactly the
expected receipt content and query. Reject other write methods, routes, options, identities,
credentials and payloads. The caller token must identify release-adoption; the
API remains responsible for signature, audience, expiry and RBAC validation.
The JWT claim filter does not replace Kubernetes authentication. Later attempts
are refused without forwarding; the proxy has no retry loop.

Only an upstream 201 with matching immutable receipt content and a real UID
qualifies. Shut down the client socket without forwarding a status line or body.
Emit a bounded event containing only status, UID and content digest. API denial,
TLS/transport errors, invalid response data and handler failures cannot qualify.
Never log request headers, token contents, payloads, upstream bodies or exceptions.

Require failed native execution with no output result and the correct worker
identity. Fresh direct native reads must match the committed UID, bytes and
first-Workflow provenance with the pointer absent. A fresh unchanged direct
create must return AlreadyExists without modification. The unchanged pure
planner must reuse the receipt and request only the absent pointer. Continue
the existing pointer race, exact retry and Workflow-deletion retention checks.

Preserve coordinator interruption separately using a second schema-valid
receipt identity, fresh native observation and the same pure-planner predicate.
No record is deleted to reset either case. Both receipts must survive fixture
Workflow deletion. Planner readiness/root samples remain explicitly synthetic;
these records are not accepted releases.

## Verification, rollback and limits

Run real loopback TLS fault/refusal tests, an actual isolated kubectl create with
Argo's output flag, generated-template/evidence checks,
shell syntax, offline Argo lint and make validate. Actual native execution runs
in the existing GitHub-hosted CI boundary job, never on a retained local cluster.
Temporary keys/configuration remain in private runner state and the owned
cluster; only safe outcome/Workflow/worker observations are uploaded.

Rollback removes this fixture and its invocation. Owned-cluster teardown removes
its Pod, Service, TLS Secret and ConfigMap. Production grants/templates remain
unchanged. Complete-parent ambiguous-response recovery, initial-parent races,
promotion, retained-environment recovery, production admission/export/restore,
operational cutover and measured script retirement remain separate gates.

Implementation references: pinned [Argo resource executor](https://github.com/argoproj/argo-workflows/blob/v4.1.5/workflow/executor/resource.go)
and [kubectl raw create](https://github.com/kubernetes/kubectl/blob/v0.35.0/pkg/cmd/create/create.go).
