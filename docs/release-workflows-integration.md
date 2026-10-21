# Disposable-kind release boundary integration

## TL;DR

- The `release-workflows-kind.yml` CI job exercises unchanged maintenance,
  adoption persistence, database-role, bucket and registry templates on an isolated
  GitHub-hosted Linux runner.
- It installs checksum-pinned kind, kubectl, Argo CLI and the official
  namespace-scoped Argo v4.1.5 asset; production selections are unchanged.
- It checks actual package mounts, Git artifact checkout, authenticated API
  reads, successful evidence, controlled refusals, create conflicts, retention
  and interrupted-coordinator recovery decisions.
- Applications and preflight input are synthetic. This does not prove release
  request delivery, Argo CD reconciliation, migration or deployment parity.
- Ledger fixture bytes are schema-valid test data, not accepted adoption records
  or promotion authority. Native create-only storage is tested separately.
- Database/bucket parity uses transient PostgreSQL/SeaweedFS and generated
  credentials; it does not deploy production charts, test retained storage,
  prove the production S3 proxy boundary or execute migrations.
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
`perfeng-verify-release-quiescence-v2`, `perfeng-adoption-records-v1` and
`perfeng-prepare-database-roles-v1` and `perfeng-prepare-bucket-v1`
WorkflowTemplates, plus the immutable
`perfeng-external-state-scripts-v1` ConfigMap, then installs the unchanged maintenance RBAC and its named
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
Application CRD validation, Argo CD drift/sync, full adoption or external-state parent execution,
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
resource manifests and does not invoke the adoption parent. A separate test-only
adapter calls the unchanged pure adoption planner on the runner with native
observations; it does not execute the planner's Argo container or request adapter.

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

### Interrupted coordinator and recovery decisions

The receipt fixture invokes the unchanged native create step, then reaches a
[suspension barrier](https://argo-workflows.readthedocs.io/en/release-4.0/walk-through/suspending/).
The harness waits at most 120 seconds for that barrier and
[terminates](https://argo-workflows.readthedocs.io/en/latest/cli/argo_terminate/)
the fixture Workflow. The case requires a successful create node, a failed
terminated Workflow and no adoption-result output. A fresh native observation
must still read the original receipt and its first-attempt provenance.

The runner calls the production `plan_adoption` predicate with those real
Kubernetes records and explicitly synthetic readiness fields. The fake root
Application receives synthetic healthy status because no Argo CD controller is
installed. These samples exercise policy/storage compatibility, not readiness
attestation or full-parent recovery.

| Recovery case | Required policy decision |
|:--|:--|
| Interrupted after receipt creation, pointer absent | Reuse the receipt; only the absent pointer may be created. |
| Complete record pair, including after fixture Workflow deletion | Exact retry needs neither create; bytes, first provenance and UID binding are preserved. |
| Root changes between observations | Refuse the changed root rather than accepting the earlier healthy observation. |
| Fixture administrator deletes the receipt | Refuse the dangling pointer. |
| Administrator recreates identical receipt bytes under the same name | Refuse the new receipt UID; leave the pointer unchanged. |

Destructive test setup occurs only in the owned disposable cluster, after the
separate retention assertions. The adoption identity gains no repair permission.
Recovery reports contain test decisions, not accepted-release results. Expected
refusals must come from the production predicate with the specific conflict;
unrelated exceptions fail the case. The dangling/recreated test states are left
for cluster teardown, never silently repaired.

The barrier establishes coordinator interruption after a confirmed API write.
It does not drop a Kubernetes response, prove recovery from an unknown transport
outcome, or run a complete adoption parent against a reviewed Git request.

The adoption identity must have actual get/create authority in the ledger only,
with patch/delete and cross-namespace create denied. Workload/Application writes
and Secret enumeration are also denied. API transport failures cannot satisfy
these checks. The fixture deliberately has no adoption-parent mutex so competing
creates exercise the storage boundary itself. It proves neither caller approval,
full-parent retry behavior nor pointer advancement.

The job saves case Workflow JSON, successful checkpoint JSON and a Pod summary
plus non-secret ledger snapshots, recovery decision reports, suspension/termination
evidence and filtered, bounded create-conflict/immutability
diagnostics under `release-workflows-results`, retained for three days. It uploads no Secret,
kubeconfig, node export, controller log or temporary credential file. GitHub's
read-only token reaches the Git artifact driver through the named temporary
Secret; it is never passed as a command argument. Temporary source files are
removed immediately after Secret creation and again on exit.

An exit/signal handler preserves bounded non-secret evidence and deletes only
the job-owned kind cluster. Cleanup failure fails an otherwise successful test.
If a runner is forcibly terminated, GitHub discards the ephemeral host; this is
not a cleanup/recovery policy for a retained or remote environment.

## Database-role preparation parity

After the maintenance and ledger phases, the same owned cluster installs only
the production external-state ServiceAccount, Workflow-result/Git-read Role and
platform Job Role. A step-only fixture calls `perfeng-prepare-database-roles-v1`
and exposes its actual `job-name` output. The published script ConfigMap and
child template are unchanged. No reviewed-request preflight is bypassed in a
production parent: the fixture invokes only this child capability.

A separate restricted PostgreSQL Pod uses the same pinned image as the child
client and an emptyDir, with a fixture Service at the expected database address.
This tests SQL operations, not the production chart, PVC retention, backup or
Argo CD reconciliation. Private fixture files generate the existing admin and
control-plane Secret shapes without pre-creating either application role.
Credentials use the production URL/password profile; Secrets are immutable.

| Case | Required behavior |
|:--|:--|
| Empty database, first child invocation | Native Job succeeds; both login roles exist with safe attributes, migration CONNECT/CREATE and runtime CONNECT without CREATE. The script verifies both TCP logins. |
| Same child invoked again | A different retained Job succeeds. Role OIDs, password hashes and complete Secret objects remain unchanged. |
| Fixture administrator marks runtime role SUPERUSER | Owning Job exits 1 with the exact unsafe-role diagnostic. No role repair or credential rotation occurs. Explicit admin correction followed by a fresh Workflow succeeds. |
| Administrator changes the existing runtime password | Owning Job exits 1 with runtime password authentication failure. The Job does not reset the password or change a Secret. After explicit admin restoration a fresh Workflow succeeds. |
| Final evidence inventory | All six successful/failed Jobs retain their original UIDs. Existing role OIDs/privileges and immutable Secrets are preserved. |

An Argo failure alone is insufficient. Refusals require an actual failed Job,
a terminated `psql` container with exit 1 and the bounded expected diagnostic;
image, mount, scheduling, executor and transport errors fail the fixture.
Waits, SQL execs and API operations have explicit bounds. The Workflow identity
must have actual create/get/watch Job permissions, with Job deletion, Secret
enumeration/create/patch, workload patching and PVC deletion denied.

Password hashes are compared only in private files, never uploaded. Public
artifacts include Workflow/Job JSON containing Secret references, non-secret
role OIDs/privileges, retained Job names and filtered refusal diagnostics.
Generated passwords and SQL reset commands never enter process arguments or
uploaded evidence. PostgreSQL generates a new salted hash on explicit password
restoration, so the final comparison checks all non-password role state; the
first repeat and each refusal compare the hashes exactly. The private runner
directory is not an artifact path and is discarded with the ephemeral host,
including on failure. This fixture is not an operator bootstrap path.

## Artifact-bucket preparation parity

The next phase installs the unchanged `perfeng-prepare-bucket-v1` child from
the rendered staged root, using the same production script ConfigMap and
external-state Job permissions. A step-only Workflow exposes its actual
`job-name`. It does not invoke the release parent or its reviewed-input preflight.

A transient SeaweedFS Pod uses the chart's pinned image and local-bootstrap
authentication profile. Its emptyDir and direct, internal S3 listener deliberately
differ from the production PVC/HAProxy topology. Other server listeners remain
on loopback. This proves child-level S3 behavior, not production ingress,
network isolation, chart reconciliation, persistence or backup.

Keep the fixture's `-volume.max=16` aligned with the production chart. Pinned
[SeaweedFS 4.45 grows single-copy volumes in batches of seven](https://github.com/seaweedfs/seaweedfs/blob/4.45/weed/topology/volume_growth.go#L47-L54).
Metadata and artifact collections share the server capacity. A two-volume cap
can allow bucket creation yet starve the first object allocation; increasing
AWS retries does not provide capacity. The regression gate reserves room for
two growth batches and checks agreement with the production setting.

A separate AWS CLI observer uses the same pinned image as the native Job,
private approved/denied credential profiles and no Kubernetes token. The fixture
administrator alone can exec into it; the release ServiceAccount cannot. Its
approved profile matches the server identity, while its denied profile matches
the initially incorrect immutable `perfeng-s3-auth` fixture. Secret/config/profile
bytes stay in private files or mounted Secrets, never CLI arguments or artifacts.

| Case | Required behavior |
|:--|:--|
| Initial API probes | Approved profile proves absence with 404; denied profile returns 403. Transport errors cannot satisfy either assertion. |
| Native child with denied credentials | Owning Job exits 1 with the non-absence refusal diagnostic. Approved profile still proves no bucket exists; both Secret objects are unchanged. |
| Explicit fixture-admin correction | Administrator deletes/recreates only the deliberately wrong disposable client Secret. The UID changes explicitly; the child never repairs credentials. |
| Fresh first preparation | A new Workflow/Job creates `perfeng-artifacts`; approved bucket metadata is recorded. |
| Repeat after a sentinel upload | A different successful Job leaves creation metadata, complete good Secret objects and the sentinel's metadata/bytes unchanged. |
| Retained evidence | All three successful/failed Jobs retain their original UIDs. |

The native script keeps its head-bucket error inside the failed container. The
fixture therefore pairs the observer's explicit 403 proof for the same denied
credential pair with the native Job's generic non-absence diagnostic; it does
not claim to extract that container's raw HTTP error. Image/mount/executor errors,
timeouts and arbitrary Workflow failures cannot replace a failed Job with an
`aws-cli` exit 1 and that diagnostic. All API, client, readiness and Workflow
waits remain bounded.

The release identity must have actual create/get/watch Job rights while Job
deletion, Secret create/patch/delete/enumeration, workload patch and Pod exec are
denied. Test-administrator credential replacement is restricted to this owned
fixture and is not an operator recovery procedure. Public evidence includes
Workflow/Job JSON with Secret references, fixed probe summaries, bucket/object
metadata, non-secret sentinel bytes and retained Job names. No Secret snapshots,
credential profiles or raw auth-error files are uploaded. A failed sentinel PUT
also saves `bucket-allocation.txt`: only allowlisted allocation phrases/counts
from a bounded server-log tail, never raw lines or identifiers. The original
PUT exit status still fails the fixture. The existing teardown removes the
job-owned cluster; private files disappear with the ephemeral host.

## Execution-registry manifest access

[ADR 0019](decisions/0019-use-ci-identity-for-registry-parity.md) records the
owner-selected identity. Only this isolated job adds `packages: read` beside
`contents: read`. A package owner must grant the environment repository **Read**
under each private package's **Manage Actions access**. Follow
[GitHub's access guidance](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility#ensuring-workflow-access-to-your-package).
Workflow YAML does not establish this grant. No PAT, host login or package
settings mutation is performed. Missing access fails with actionable guidance.

Before the original token file is removed and `GITHUB_TOKEN` unset, the sourced
`registry.sh` converts it and `GITHUB_ACTOR` into private encoded Secret documents.
It also creates an invalid credential and derives expected digests from the
published child. Both encoded files are removed by the exit handler and excluded
from artifacts. The CI actor is not retained-environment operator admission.

The final phase creates the disposable `perf-generators` namespace and installs
the unchanged generator Job grant and `perfeng-prepare-execution-registry-v1`
child. A step-only wrapper exposes its single `job-name`; the native Job retains
three pinned `crane` workers.

| Case | Required behavior |
|:--|:--|
| First/repeated authenticated execution | All three workers exit 0 and emit the selected digest; distinct Jobs remain and complete Secret objects are unchanged. |
| Invalid credential | Owning Job fails, kube-burner exits 1 and emits an authorization signal. Unrelated transport/controller/image errors cannot count. |
| Explicit fixture-admin correction | Administrator replaces only the test Secret and submits a fresh Workflow; all digests match the first observation. |
| Retained evidence | All four Job UIDs remain. The release identity cannot delete Jobs, mutate/enumerate Secrets, patch workloads or exec into Pods. |

Raw worker logs remain private. Upload only Workflow/Job JSON with Secret
references, validated digest lines, retained Job names and fixed authorization
signals such as `UNAUTHORIZED`/`DENIED`. Check package grants, immutable selection
and registry connectivity on failure; never substitute credentials or broaden
permissions automatically. Existing operation bounds and owned-cluster cleanup
remain in force.

This proves manifest/digest access at that moment. `crane digest` does not prove
layer downloads or guaranteed full manifest-byte hashing. The host verifier's
stronger byte check, full reviewed-request parent, deployment, migration,
promotion and non-local acceptance remain separate. Original scripts are retained.

## Verification and remaining gates

Offline tests check CI authority/artifact boundaries, fixture contracts, tool
pins, Bash syntax and refusal on unsupported runners before side effects.
Run `make validate` and `make workflow-lint` for complete non-live checks.
The latter covers production WorkflowTemplates; this fixture additionally runs
through the real controller in its separate CI tier.

The helpers `ledger.sh`, `recovery.sh`, `database.sh`, `bucket.sh` and `registry.sh` can only be sourced by the isolated
harness with its ownership marker, private kubeconfig and command functions.
They have no separate bootstrap or operator entrypoint. All shell files receive offline syntax checks;
the fixture's embedded Python is syntax checked, and generated/stored data are
schema/byte checked during CI execution. Offline tests execute the recovery
adapter against policy fixtures, including unexpected-refusal rejection.

Inspect the CI job and its case artifacts during explicit review. A successful
job establishes only the integration boundary above. Do not infer live success
from offline checks or claim the job passed before it runs. Existing disposable
kind release acceptance, prior-release/promotion guards, maintenance writes,
backup/recovery, operational cutover and the original script-retirement gate
remain open. Production Python stays at two files and 708 physical lines.
