# Disposable-kind maintenance verifier integration

## TL;DR

- The `release-workflows-kind.yml` CI job exercises the unchanged staged
  quiescence child on an isolated GitHub-hosted Linux runner.
- It installs checksum-pinned kind, kubectl, Argo CLI and the official
  namespace-scoped Argo v4.1.5 asset; production selections are unchanged.
- It checks actual package mounts, Git artifact checkout, authenticated API
  reads, successful evidence, controlled refusals and recovery.
- Applications and preflight input are synthetic. This does not prove release
  request delivery, Argo CD reconciliation, migration or deployment parity.
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
`perfeng-verify-release-quiescence-v2` WorkflowTemplate, then installs the
unchanged maintenance RBAC and its named Application read grant. No fixture
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

Negative cases must terminate the main verifier container with exit code 2.
An artifact-fetch failure, pending Pod or controller error cannot satisfy that
assertion. All waits, downloads and API operations are bounded. Separate
authorization checks require explicit `no` for workload/Application/ledger
patching, Pod deletion, Job creation and Secret enumeration; transport errors
do not count as denied authority.

The job saves case Workflow JSON, successful checkpoint JSON and a Pod summary
under `release-workflows-results`, retained for three days. It uploads no Secret,
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

Inspect the CI job and its case artifacts during explicit review. A successful
job establishes only the integration boundary above. Do not infer live success
from offline checks or claim the job passed before it runs. Existing disposable
kind release acceptance, prior-release/promotion guards, maintenance writes,
backup/recovery, operational cutover and the original script-retirement gate
remain open. Production Python stays at two files and 708 physical lines.
