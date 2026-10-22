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
- A separate [trusted-main workflow](../.github/workflows/release-preflight-main.yml)
  accepts the complete production preflight using a generated Git request for
  its real disposable cluster UID; see [ADR 0020](decisions/0020-publish-disposable-ci-release-requests.md).
- The same trusted-main run then exercises complete maintenance-checkpoint
  composition with that exact request, manually paused fixture Applications
  and a zero-replica Deployment. No synthetic preflight result is substituted.
- It also composes the complete external-state parent against the shared
  transient database/bucket fixtures and private registry with owner-approved
  package-read authority ([ADR 0021](decisions/0021-authorize-trusted-main-external-state-parity.md)).
- A later CI-only phase runs the selected image's embedded migrator on the
  initially empty disposable schema, with fresh maintenance evidence before
  each attempt. It does not introduce a production migration entrypoint.

## Invocation and isolation

### Complete preflight on trusted main

The separate main-only workflow selects `RELEASE_KIND_MODE=preflight-parent` in
the same harness. It reuses tool pins, private kubeconfig and cluster ownership
guards, then skips maintenance/database/bucket/registry/readiness cases. Only
this job has `contents: write`; the existing PR job keeps read permissions.
No additional operator command or production wrapper is introduced.

After startup, a CI-only helper creates a schema-valid request with the actual
`kube-system` UID and the checked-out main commit's raw lock digests. The request
is a child Git commit published through a create-only unique ref. The unchanged
`perfeng-release-preflight-v2` parent runs twice with that exact request SHA and
the distinct selection SHA. Both results must match, validate against the result
schema and bind the actual UID. Workflow UIDs and the private Git Secret must
remain unchanged, with Kubernetes writes and Pod exec denied.

Unlike the read-only PR job described below, this job's ephemeral Git artifact
credential has repository write authority. The Secret name does not reduce it.
Only reviewed main code can execute this trusted boundary. Generated requests
are disposable test inputs, not human-reviewed deployment approval.

The exit handler conditionally deletes only the exact run-owned ref at its
expected SHA, including after ambiguous publication failure, before cluster
cleanup. A moved ref fails cleanup; no forced branch update is permitted.
Results include `parent-request-ref.json`, `parent-request-ref-cleanup.json`,
request bytes/parameters and `parent-{first,repeat}.{workflow,result}.json`.
Secret snapshots and raw Git diagnostics remain private. Abrupt runner loss can
leave a temporary ref requiring identity-checked manual cleanup.

This gate first runs after merge; offline/PR success does not claim live parent
acceptance. Successful preflight alone does not prove full release composition,
application delivery, promotion, recovery or script retirement.

### Complete maintenance-parent composition

After both complete preflights pass, the same trusted-main job submits the
unchanged `perfeng-release-maintenance-checkpoint-v2` parent. It reuses the
existing request parameters, Git ref, token and granted ServiceAccount; no new
CI authorization or production permission is added. Fixture administration
creates the two reviewed Application specs with automatic sync disabled and
the existing zero-replica Deployment. There is no Argo CD controller or actual
control-plane deployment in this test.

| Case | Required evidence |
|:--|:--|
| First/repeat | Complete parent succeeds; checkpoint schema, actual cluster UID, request/selection identities and each current resource UID/version match. |
| Wrong request digest | Identity Pod exits 2 at preflight with the exact byte-mismatch diagnostic; downstream observation does not execute. |
| Unpaused root | Preflight succeeds, observation exits 2 with its controlled policy refusal and no checkpoint is published. |
| Revoked Application read | Fixture administration removes only its disposable named read binding and confirms denial. Preflight still succeeds; observation exits 2 with its operational-response refusal. |
| Fresh correction | Administration restores the reviewed pause/read grant and confirms access. A new complete parent succeeds against the original request. |

Observation failures may retain a successful nested preflight `result.value`.
They must not publish a parent or observer result, including an empty/null
materialized value. Unresolved Argo output declarations are allowed. The
checked-in refusal predicate, owning Pod phase, main exit 2 and exact constant
diagnostic distinguish controlled refusal from image, Git, controller or timeout
failures. Successful nested preflight evidence must equal the original complete
preflight result from this run.

Each case records only projected Application/Deployment identities and versions
before/after execution; they must remain unchanged within the case. UID identity
persists across explicit fixture-admin corrections. All six Workflow identities
and the complete private Git Secret object are retained and unchanged, and
workload/Application writes, Pod deletion/exec, Job creation, Secret enumeration
and ledger patch are denied to the workflow identity. Results are named
`maintenance-parent-<case>.{workflow,result,preflight,before,after}.json` as
applicable, plus constant refusal lines. Raw Pod logs and Secret snapshots stay
private. Existing exit handling cleans the one owned ref and disposable cluster.

This is complete **maintenance-checkpoint composition** evidence after the main
job passes. It does not prove Argo CD reconciliation, real workload health,
automatic pause/resume, Run draining, backups, migration/recovery, promotion or
retirement of any supported host entrypoint. Local/PR checks cannot claim live
acceptance of this trusted-main phase.

### Complete external-state parent composition

After maintenance composition, the same main-only job reuses the one exact
request/ref for `perfeng-release-external-state-v1`. The owner approved adding
`packages: read` alongside its existing contents write. The unchanged parent
performs real Git preflight and the v2 lock-selection guard before native bucket,
database-role and registry Jobs. Shared setup functions preserve existing child
fixtures and generate only disposable credentials; no second production
bootstrap path or runtime Python module is introduced.

| Case | Required outcome |
|:--|:--|
| Wrong byte digest | Exact identity refusal, no selection/preparation execution and zero Jobs. |
| Denied bucket | Confirmed absent bucket (404) and denied client (403); one failed bucket Job, no database or registry Job, roles still absent. |
| First | Explicit fixture-admin replacement of the bad bucket client; three successful Jobs, expected role grants/logins and all three pinned registry digests. |
| Repeat | Three fresh successful Jobs; stable bucket identity, sentinel object, role OIDs/password hashes and complete Secret objects. |
| Unsafe role | Admin marks only its test runtime role superuser. Bucket succeeds, database refuses unsafe attributes and registry does not execute; credentials and unsafe role remain unchanged by the Workflow. |
| Fresh correction | Admin removes the test superuser flag; a new complete parent succeeds against the original request. |

Parent success must export the exact preflight plus all three actual new Job
names. Failures may preserve truthful partial preflight/created-Job outputs;
these are not a completed preparation. The offline-checkable evidence consumer
rejects downstream execution, unrelated Jobs, leaked future-stage outputs and
stale request/cluster identities. Expected Job refusals additionally require a
failed Job condition, the owning worker exit 1 and its exact safe diagnostic;
workflow wait timeouts, image pulls and controller/artifact failures cannot count.
The published bucket script suppresses its underlying lookup stderr. Its case
is a stopping-boundary check with bad-client 403 probes before/after, not proof
of that Job's individual HTTP cause: another provider lookup error can emit the
same constant. Per-Job bucket authorization/network diagnosis remains open and
must not receive retirement or authorization-parity credit from this case.
Registry logs are private; only exact expected digests, exit codes and constant
error classifications are uploaded. Failed registry access must still fail CI.

All six Workflows and twelve new Job UIDs remain queryable before owned-cluster
cleanup. Credential objects are compared within every attempt; only the explicit
bad-client replacement changes its UID. Final role/password and bucket/object
comparisons prove no unintended mutation across repeat/refusal/correction.
Job Pods have no API token. Workflows retain only the reviewed Job grants and
cannot delete Jobs, rotate/list Secrets, exec or patch workloads/Applications.

Artifacts use `external-parent-<case>` names for Workflow/Job JSON, created-Job
inventory, worker exits, digests and constant signals. Role reports exclude
password hashes. Secret snapshots and SQL credential files remain private.
The one request ref uses existing guarded cleanup, including after failure.

This is complete finite preparation on transient PostgreSQL and direct internal
S3/emptyDir, pending a successful post-merge main run. It does not establish
production storage/proxy health, real readiness or adoption, schema migration,
manifest-byte/layer verification, recovery/export, promotion or host retirement.

### Selected migration-image parity

After external-state preparation, the main-only job runs the exact control-plane
image selected by `deployments/local-control-plane.json`. Its CI resource wrapper
executes `/usr/local/bin/migrate` directly; no rebuilt binary, mutable image tag
or shell substitute is used. `migration-image.json` records the selected image,
source commit and raw SHA-256 of the eighteen SQL files embedded by that source.
These are test expectations, not copied SQL or a new shared release contract.
A new image/source selection requires reviewing this fixture too. No extra Git
read credential or CI permission is introduced. The selected control-plane
package rejects anonymous pulls; it must separately grant this repository
Actions Read access, just as the execution packages do. Success against those
other packages does not prove access to this one. No package setting is changed
by this PR.

Before any attempt, require the owned disposable PostgreSQL Pod, no application
relations in `public`, absent `perfeng_control`, migration CREATE permission and
no runtime CREATE permission. Each attempt then submits a **fresh complete**
maintenance parent against the original Git request. Its successful checkpoint
must bind that request/selection/cluster and finish before migration Workflow
and Job creation. This sequential CI proof is not an atomic production admission
or a backup/recovery attestation.

| Case | Required evidence |
|:--|:--|
| Runtime role | Migrator connects, reaches migration processing, exits 1 and leaves the schema absent. PostgreSQL itself must report database CREATE denial for `perfeng_runtime`, database `perfeng` and this attempt's application name. |
| First migration role | Exact selected image exits 0; the schema belongs to `perfeng_migration`, with all eighteen expected filename/checksum rows through `0018_benchmark_cleanup.sql`. |
| Repeat | Fresh checkpoint and Job; preserve schema/relation OIDs, ledger hashes/timestamps and a separately seeded non-secret sentinel's OID/bytes. |

The fixture-only PostgreSQL log prefix includes user, database and application.
The pinned driver's [standard `PGAPPNAME` support](https://github.com/jackc/pgx/blob/v5.11.0/pgconn/config.go)
binds the expected denial to the runtime-role attempt. A generic migrator failure,
connection failure, wrong role/application or image-pull error cannot count.
Raw PostgreSQL/migrator logs stay private; upload only the constant denial,
worker exit/waiting reasons, safe image/Job metadata, checkpoints and schema
observations. The separate disposable Docker-config Secret reuses the approved
CI token; its full data and the database credential/password snapshots are never
uploaded. Job Pods have no Kubernetes API token, run non-root and use a read-only
root filesystem.

Artifacts use `migration-image-<case>.{workflow,job,worker,checkpoint,schema}.json`
as applicable, including checkpoint Workflow JSON. Three distinct migration Jobs
and Workflows plus three fresh checkpoint Workflows remain queryable before
normal owned-cluster cleanup. Database Pod, role/password and Secret identities
remain unchanged. No failed Job is replaced or retried.

Live OCI/schema acceptance first runs after merge. This phase establishes
selected migrator/schema parity only: it does not upgrade a retained database,
test a populated upgrade or restore, grant runtime table privileges, run the
control-plane server, establish readiness/adoption, authorize production
migration/promotion, or retire the supported host commands. The existing M04
manual pause/backup/migration/recovery process remains authoritative.

### Read-only PR boundary integration

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
Workflow YAML does not establish this grant. Failed workers publish exit codes
and constant error categories (authorization, manifest-missing, TLS, network,
command or unclassified), with case-insensitive matching and no raw log lines.
An unclassified error requires further investigation; it is not an auth denial
and cannot satisfy the deliberately invalid credential case.
No PAT, host login or package
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
references, validated digest lines, retained Job names, worker exit codes and
constant error categories. Check package grants, immutable selection
and registry connectivity on failure; never substitute credentials or broaden
permissions automatically. Existing operation bounds and owned-cluster cleanup
remain in force.

This proves manifest/digest access at that moment. `crane digest` does not prove
layer downloads or guaranteed full manifest-byte hashing. The host verifier's
stronger byte check, full reviewed-request parent, deployment, migration,
promotion and non-local acceptance remain separate. Original scripts are retained.

## Readiness verifier runtime and HTTP parity

The final sourced `readiness.sh` phase invokes the unchanged
`perfeng-verify-release-readiness-v2` child through a step-only wrapper using
`release-readiness`. Argo fetches the selected repository at the exact CI commit
using the existing Git-read Secret. The non-root verifier mounts the published
two-module package and checks the selected lock before making HTTP requests.

Preflight and workload observations are explicitly synthetic policy fixtures.
They are not collected from running PostgreSQL, SeaweedFS or control-plane
workloads. A separate fixture Pod uses the same pinned Python image as the
verifier and serves controlled responses through the real internal
`control-plane.perf-platform.svc.cluster.local:8080` Service. Its selector is a
fixture label. This proves the installed verifier, Git artifact and HTTP path;
it does not prove the resource observer, reviewed-request parent, Argo CD
reconciliation or actual application health.

| Case | Required behavior |
|:--|:--|
| First/repeated healthy execution | All three probes succeed and schema-valid output binds the synthetic request, actual cluster UID and selected Git commit. Results are byte-identical. |
| Wrong selected image | Exact policy refusal, verifier exit 2, no result and zero endpoint requests. |
| HTTP 503 on readyz | Exact readyz refusal, no metrics request and no result. |
| Redirected metrics | Exact metrics refusal; redirect destination is never requested. |
| Commented readiness metric | HTTP 200 still refuses the invalid metrics body with no result. |
| Explicit correction | Fixture administrator resets the same responder to healthy; a new Workflow emits the original result. |

Only fixture administration can change responder mode through loopback HTTP
inside its Pod. The release identity cannot exec into that Pod, modify workloads
or Applications, create Jobs or Secrets, or list Secrets. The seven distinct
Workflows and original responder Pod/Service UIDs remain. TCP readiness probes
do not affect the per-case HTTP request counts.

Artifacts include Workflow/result JSON, constant refusal lines and endpoint
counts. Raw worker logs, Git credential context and HTTP response bodies are
excluded. Negative cases require exit 2 from the verifier's main container and
the exact owning refusal; Git checkout, image pull, controller and timeout
failures cannot count as an expected rejection. Argo may retain the declared
output's `valueFrom` path on failure; the absence check rejects a materialized
`result.value` at any node, including empty/null, rather than the declaration.
Correct prerequisites through
their owner and submit a fresh Workflow; the verifier repairs nothing.

The production package remains two files/708 lines. Native observation is
covered separately below. Full readiness-parent acceptance, real service health,
M04 recovery, promotion and script retirement remain open.

## Native readiness resource observation

After HTTP parity, `observer.sh` invokes the unchanged
`perfeng-observe-release-readiness-v1` resource templates using
`release-readiness`. Real Deployment and StatefulSet controllers manage pinned
synthetic sleeper Pods. Their names match the observer's fixed read contract;
the two StatefulSets have distinct fixture selectors and no PVCs. The existing
fixture Service remains separate from those sleepers. Application status is
explicitly administered on the minimal test CRD, which has no Argo CD controller
or status subresource.

The step-only wrapper collects both StatefulSets, Application, Deployment and
Service through native GET operations and their production success/failure
conditions. First/repeat observations must be identical. Their UIDs, names,
generations, rollout/revision fields, image and source/configuration references
are checked against fresh native API snapshots. This tests the observer's
installed resource polling and jq projections; synthetic workload readiness and
Application status do not establish actual service health or reconciliation.

| Case | Required behavior |
|:--|:--|
| First/repeat observation | Five successful projections match native identities/controller state, with stable output. |
| Degraded Application | Owning Application resource step fails with its evaluated failure condition; no finalized observation set. |
| Revoked Application read | Fixture administrator removes only the disposable RoleBinding; owning step fails with API Forbidden evidence for the readiness identity. |
| Explicit correction | Administrator restores healthy status/read binding; a new Workflow observes the original resource UIDs successfully. |
| Pending Deployment | With zero replicas, require an executor log recording an evaluated false native condition and a still-running Workflow. Only then scale up explicitly; the same Workflow completes with fresh generation and unchanged Deployment UID. |

The existing named-read identity cannot list/watch observed resources, patch
workloads or Applications, or create Jobs/Secrets. Fixture administration alone
changes status, scale or its test RoleBinding. All six Workflow UIDs and the
observed resource UIDs are retained until owned-cluster teardown. Raw worker
logs and private native snapshots stay outside artifacts; artifacts contain
Workflow/projection JSON and constant refusal/pending markers. An unrelated
controller, image, transport or timeout error cannot satisfy an expected
refusal. The pending evidence loop is bounded CI observation of the native
executor, not a production readiness loop.

Full reviewed-request/preflight and readiness-parent execution, actual workload
health, Argo CD reconciliation, promotion and host-script retirement remain
separate. Production verifier code remains two files/708 physical lines.

## Git, schema and preflight identity

The final `preflight.sh` phase installs the unchanged
`perfeng-release-preflight-v2` and existing narrow `release-preflight` grants.
Argo's Git artifact driver fetches the actual committed CI request and a
separately pinned reviewed selection from the original private repository,
using the existing Git-read Secret. The pinned schema CLI and mounted identity
verifier process those files; no request ConfigMap, rewritten checkout, alternate
Git server or dynamic publication is used.

The committed [CI request](../deployments/release-requests/ci-preflight-fixture-v1.json)
has an all-zero fixture UID, a historical reviewed selection and the hashes of
that selection's committed lock bytes. It is not an operational request. The
step-only wrapper invokes the unchanged schema, native namespace-read and
identity children. **Only this child fixture supplies the all-zero observed UID
to the identity child.** It separately checks the native namespace output
against the real cluster UID. Successful child output therefore proves Git,
schema and identity runtime parity using synthetic cluster input, not successful
production-parent admission or permission to deploy.

| Case | Required behavior |
|:--|:--|
| First/repeat child fixture | Schema CLI and identity child succeed; schema-valid evidence matches exact request bytes, both Git revisions, selected lock hashes and synthetic UID. Results are identical. |
| Wrong byte digest | Identity child exits 2 with the exact request-byte mismatch diagnostic and no materialized result. |
| Unsafe request path | Schema child exits 2 at the request-name guard before identity verification; exact owning diagnostic is required. |
| Wrong-cluster production parent | Submit the same request to the complete, unchanged production preflight. Its native UID input must cause identity refusal, not successful admission. |
| Fresh correction | Submit the original parameters again to the child fixture; evidence matches the first result. |

Expected failures require the named owning Pod step, main exit 2 and exact
constant refusal, with no `result.value` at any node. Unresolved Argo output
declarations are allowed. Git/artifact, image, controller and timeout failures
cannot count as expected refusal. All six Workflow UIDs, the real namespace UID
and the complete Git Secret object remain unchanged/retained. The identity cannot
patch namespaces, workloads or Applications, create Jobs/Secrets, list Secrets
or exec into Pods. Raw logs and Secret snapshots remain private; artifacts
contain Workflow/result JSON and constant refusal lines only.

The real cluster UID must differ from the fixture's all-zero UID. Do not replace
the fixture UID with a live value or reuse its initial-promotion intent against
an existing environment. A successful complete parent with an actual reviewed
cluster request, parent composition, promotion/recovery and script retirement
remain open. Production verifier code remains two files/708 physical lines.

## Verification and remaining gates

Offline tests check CI authority/artifact boundaries, fixture contracts, tool
pins, Bash syntax and refusal on unsupported runners before side effects.
Run `make validate` and `make workflow-lint` for complete non-live checks.
The latter covers production WorkflowTemplates; this fixture additionally runs
through the real controller in its separate CI tier.

The helpers `ledger.sh`, `recovery.sh`, `database.sh`, `bucket.sh`, `registry.sh`, `readiness.sh`, `observer.sh` and `preflight.sh` can only be sourced by the isolated
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
