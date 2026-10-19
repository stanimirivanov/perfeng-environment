# Disposable-kind release boundary integration

## TL;DR

- The `release-workflows-kind.yml` CI job exercises unchanged maintenance and
  native adoption persistence templates on an isolated GitHub-hosted Linux runner.
- It installs checksum-pinned kind, kubectl, Argo CLI and the official
  namespace-scoped Argo v4.1.5 asset; production selections are unchanged.
- It checks actual package mounts, Git artifact checkout, authenticated API
  reads, successful evidence, controlled refusals, create conflicts and retention.
- Applications and preflight input are synthetic. This does not prove release
  request delivery, Argo CD reconciliation, migration or deployment parity.
- Ledger fixture bytes are schema-valid test data, not accepted adoption records
  or promotion authority. Native create-only storage is tested separately.
- The test refuses local/self-hosted execution. No retained cluster or
  environment credential is used; only non-secret results are uploaded.

## Invocation and isolation

The separate `make release-workflows-integration` target is invoked by the
path-filtered PR job or an explicitly selected `workflow_dispatch` run. It is
not a dependency of `make verify` or `make validate`. Fork PRs do not run this
job. Checkout uses the PR head commit so the Git artifact driver can fetch that
exact published commit, rather than relying on a synthetic merge commit.

Do not run this target as an operator deployment command or override its runner
checks to use the retained cluster. It requires GitHub-hosted Linux authority,
an unset Docker host/context override and the default local Unix Docker socket.
It rejects pre-existing kind clusters before arming cleanup. A private temporary
kubeconfig always selects `kind-perfeng-local`; the job creates one disposable
node using the infrastructure lock's exact node image. It does not bootstrap
the Perfeng workload stack or change the active GitOps root.

The fixture's `toolchain.json` records official binary URLs and SHA-256 values.
The Argo CLI checksum is shared with offline CI. The controller asset/hash match
the `k8s-infrastructure` add-on selection. This small CI substrate installation
is a test fixture, not a second production bootstrap provider. Review new tool
pins and compatibility together; production infrastructure locks are independent.

## What runs and what is synthetic

The job renders the real staged Kustomize root, extracts only the immutable
`perfeng-release-verifiers-v2` ConfigMap and
`perfeng-verify-release-quiescence-v2` and `perfeng-adoption-records-v1`
WorkflowTemplates, then installs the unchanged maintenance RBAC and its named
Application read grant. No fixture
copy of the Python package or child template is maintained. The controller
creates real restricted Pods; their Git artifact is the exact checked-out
repository commit, fetched using a short-lived repository-read token.

A deliberately minimal test Application CRD stores the two real Application
specs with automated sync disabled. No Argo CD controller reconciles them.
A zero-replica test Deployment has the reviewed selector and ServiceAccount
identity. Kubernetes supplies actual UIDs, resource versions, rollout status,
ServiceAccount token and serving CA. The test workflow passes a visibly
synthetic preflight envelope containing the actual cluster UID and exact lock
digest. Its fake request identity is test data, not an admitted request or an
accepted release. No self-referential request commit is created or published.

Consequently this gate tests the child execution and Kubernetes API boundary,
including its real Git artifact adapter. It does not test private release-request
publication/preflight, the full maintenance parent or mutex, production
Application CRD validation, Argo CD drift/sync, adoption, external-state Jobs,
database migration, full health parity or non-local admission.

## Cases and evidence

| Case | Expected observation |
|:--|:--|
| First and repeated invocation | Schema-valid checkpoint with actual cluster UID and exact selection; stable resource UIDs across both samples. |
| Wrong cluster UID | Predicate refuses with no successful checkpoint. |
| Root unpaused | Predicate refuses before publishing success. |
| Matching standalone Pod, including Pending | Absence is refused; no Pod deletion by the verifier. |
| Application read RoleBinding removed | Actual API read is denied and the verifier refuses. |
| Fixtures/grant corrected, fresh Workflow | Verification succeeds without replaying old successful nodes. |

Maintenance refusal cases must terminate the main verifier container with exit code 2.
An artifact-fetch failure, pending Pod or controller error cannot satisfy that
assertion. All waits, downloads and API operations are bounded. Separate
authorization checks require explicit `no` for workload/Application/ledger
patching, Pod deletion, Job creation and Secret enumeration; transport errors
do not count as denied authority.

### Native ledger persistence

After maintenance verification, the same isolated cluster installs the production
ledger namespace/grants and adoption ServiceAccount/RBAC. A step-only fixture
Workflow references the existing native record operations; it contains no copied
resource manifests and does not invoke the adoption parent or planner.

The test generates schema-valid request/receipt bytes with current lock digests,
the actual fixture cluster/Application UIDs and the exact checkout revision. It
checks stored bytes, provenance and receipt UID binding. These bytes are not
published or reviewed as a Git release request, and no readiness attestation is
produced for them. Their persisted `adopted-baseline` payload is fixture data,
not evidence that the full adoption protocol has succeeded.

| Case | Required API result |
|:--|:--|
| Missing receipt and pointer | Native reads return JSON `null`. |
| Receipt created, pointer absent | Exact immutable bytes/provenance are readable; no pointer is silently created. |
| Two in-flight pointer creates | Exactly one succeeds; the other reports the API's AlreadyExists conflict. |
| Exact duplicate and changed-content creates | Explicit AlreadyExists failures; the entire stored winner, UID and resource version remain unchanged. |
| Fixture administrator attempts receipt-data patch | Kubernetes rejects immutable-data mutation independently of the ServiceAccount's denied patch permission. |
| Foreground deletion of ledger-fixture Workflows | Both records survive unchanged with no Workflow owner reference, finalizer or Argo CD tracking; a fresh native read returns them. |

The adoption identity must have actual get/create authority in the ledger only,
with patch/delete and cross-namespace create denied. Workload/Application writes
and Secret enumeration are also denied. API transport failures cannot satisfy
these checks. The fixture deliberately has no adoption-parent mutex so competing
creates exercise the storage boundary itself. It proves neither caller approval,
planner conflict decisions, full-parent retry behavior nor pointer advancement.

The job saves case Workflow JSON, successful checkpoint JSON and a Pod summary
plus non-secret ledger snapshots and filtered, bounded create-conflict/immutability
diagnostics under `release-workflows-results`, retained for three days. It uploads no Secret,
kubeconfig, node export, controller log or temporary credential file. GitHub's
read-only token reaches the Git artifact driver through the named temporary
Secret; it is never passed as a command argument. Temporary source files are
removed immediately after Secret creation and again on exit.

An exit/signal handler preserves bounded non-secret evidence and deletes only
the job-owned kind cluster. Cleanup failure fails an otherwise successful test.
If a runner is forcibly terminated, GitHub discards the ephemeral host; this is
not a cleanup/recovery policy for a retained or remote environment.

## Verification and remaining gates

Offline tests check CI authority/artifact boundaries, fixture contracts, tool
pins, Bash syntax and refusal on unsupported runners before side effects.
Run `make validate` and `make workflow-lint` for complete non-live checks.
The latter covers production WorkflowTemplates; this fixture additionally runs
through the real controller in its separate CI tier.

The helper `ledger.sh` can only be sourced by the isolated harness with its
ownership marker, private kubeconfig and command functions. It has no separate
bootstrap or operator entrypoint. Both shell files receive offline syntax checks;
the fixture's embedded Python is syntax checked, and generated/stored data are
schema/byte checked during CI execution.

Inspect the CI job and its case artifacts during explicit review. A successful
job establishes only the integration boundary above. Do not infer live success
from offline checks or claim the job passed before it runs. Existing disposable
kind release acceptance, prior-release/promotion guards, maintenance writes,
backup/recovery, operational cutover and the original script-retirement gate
remain open. Production Python stays at two files and 708 physical lines.
