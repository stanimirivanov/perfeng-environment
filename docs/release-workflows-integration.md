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
- The test refuses local/self-hosted execution. No retained cluster is used;
  only non-secret results are uploaded. Trusted-main private source access has
  the explicit credential exception described below.
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
- A later trusted-main phase installs actual Argo CD from the selected
  infrastructure bootstrap and manually syncs private control-plane sources,
  including real Service drift/correction. This separately approved phase uses
  an ephemeral exact-URL source-token Secret; see [ADR 0023](decisions/0023-authorize-disposable-argocd-source-access.md).
- After foundation Application acceptance, a bounded phase manually reconciles
  the real root at the selected commit with auto-sync disabled and six children
  skipped. This proves root-owned object convergence, not full-tree health;
  see [ADR 0025](decisions/0025-bound-disposable-root-reconciliation.md).
- Complete adoption then runs the unchanged production parent against that root,
  checking first/repeat persistence, a root-read refusal, fresh correction and
  ledger retention after deletion of the original disposable Workflow.
- Trusted-main has four isolated scenarios: `initial` preserves first adoption;
  `receipt-only` exercises quota-blocked pointer recovery; `response-loss` exercises
  a committed receipt with its response dropped, followed by unchanged-parent
  recovery; `pointer-response-loss` requires reuse of both committed records.
  See [ADR 0026](decisions/0026-isolate-disposable-adoption-recovery-scenarios.md),
  [ADR 0031](decisions/0031-exercise-complete-adoption-response-loss.md) and
  [ADR 0032](decisions/0032-exercise-pointer-create-response-loss.md).

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
`perfeng-release-preflight-v3` parent runs twice with that exact request SHA and
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
unchanged `perfeng-release-maintenance-checkpoint-v3` parent. It reuses the
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
performs real Git preflight and the v3 lock-selection guard before native bucket,
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

### Early selected-image prerequisites

Trusted-main checks required runner tools and validates the selected image/source
against the migration contract before creating kind. The `registry-prerequisite`
stage uses a temporary Docker config containing the exact generated pull Secret's
authentication JSON, runs a native pull of the immutable control-plane image and
verifies `linux/amd64`. Pull and inspection are bounded to 120 and 20 seconds
with five-second kill grace. It never uses a stored login or loads the pulled
image into kind to bypass node authentication.

`selected-image-prerequisite.json` records only the immutable image, result,
exit code and constant signals. Authorization, missing manifest, TLS/network,
timeout and platform failures print explicit resolution guidance. Read or Admin
package Actions access is sufficient; increasing Admin is not a remedy. A grant
that already exists requires checking job-token/config delivery. Raw registry
errors and the ephemeral config are removed, never uploaded. If this stage
fails, no cluster is created and cleanup does not target an unstarted cluster.

Immediately after kind and namespaces exist, `kubelet-image-prerequisite` uses
an isolated pull Secret with identical data and one Pod with `imagePullPolicy:
Always`. It waits up to 95 seconds for the kubelet's nonempty image ID, with a
100-second command/request bound and five-second kill grace. The Pod's own
150-second deadline leaves time to inspect failed-pull status before that
status is replaced by deadline termination. This precedes controllers,
PostgreSQL and every release parent. A passed runner pull followed by a failed
node pull points to Secret data/type/namespace/reference or node registry
access, rather than another request for a broader package role.

The wait filters for `imageID>""`, then independently reads the Pod and requires
exactly one `pull` container with a non-empty image ID. In pinned kubectl 1.35,
a value-less JSONPath wait accepts an existing empty string. Run 40188553698
therefore returned wait exit 0 before an image ID existed, and the fresh read
correctly refused it. A real-client loopback API regression now requires the
returned Pod to contain the populated ID after an empty-ID watch event.
The filter uses `>` because that client's wait parser splits `!=` at its single
equals sign. This preserves native watch deadlines and does not qualify SQL,
migration or adoption. See the pinned [wait implementation](https://github.com/kubernetes/kubectl/blob/v0.35.0/pkg/cmd/wait/json.go).

The probe gets no Kubernetes API token or database credentials. The pinned
`/usr/local/bin/migrate` receives an explicitly empty `PERFENG_DATABASE_URL`;
its reviewed `postgres.Open` rejects that value before any connection. A
nonzero probe exit is expected and irrelevant to image-pull success. The
`selected-image-kubelet-prerequisite.json` result establishes image pull only,
never migration, readiness or adoption. Later CI migration Jobs also set
`imagePullPolicy: Always`; the early probe must not let a cached image bypass
the migrator's own registry authentication. Keep the empty-URL behavior under
review when changing the selected executable. The fixture Pod/Secret remain
only in the owned disposable cluster until normal teardown; no production
manifest, credential rights, CI scenario or registry setting changes.

The latest rerun (40107957676, attempt 2, initial leg) progressed past registry
access and passed migration parity after the owner added Admin Actions access.
Its later ungranted runtime SQL probe exited 2 instead of the expected SQL
policy refusal at exit 3. The harness now emits
`[PREREQUISITE:database-connection]` and a constant
`runtime-grants-<case>.failure.txt` for that connection failure/loss, with Secret,
libpq and endpoint/auth/TLS guidance. Other unexpected outcomes retain a distinct
runtime-policy error. No connection failure is accepted as policy evidence.

The runtime worker's connection defect was subsequently reproduced with the
fixture's pinned PostgreSQL client in a network-isolated container. `PGDATABASE`
is a literal database-name default; putting the complete URI there does not
expand its host, user or password, so the original worker selected a local
socket. The worker now validates the existing canonical `runtime-url` Secret
against the local role, host, port, database, sslmode and 43-character URL-safe
password contract, then exports separate libpq connection variables. The URI is
unset before `exec psql`; credentials stay out of command arguments and logs.
No Secret format, SQL policy, grants or image pin changes. Invalid input exits
64 with `[PREREQUISITE:database-input]`; connection failure remains exit 2 and
the expected SQL policy refusal remains exit 3. The isolated native-client
before/after check proves endpoint selection only; full SQL/grant acceptance
still runs through the unchanged trusted-main disposable-cluster workflow.
See PostgreSQL's [environment-variable documentation](https://www.postgresql.org/docs/17/libpq-envars.html).

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

Startup diagnosis additionally uses a bounded native Pod watch during the existing
Argo wait. Its raw stream and stderr stay in the private fixture directory and
are removed after projection. `migration-image-<case>.startup.json` retains only
UUIDs of Pods controlled by the exact selected Job, allowlisted lifecycle and
scheduling/waiting reasons, numeric exit codes and constant authorization,
manifest-missing, TLS or network signals. Messages, node names, image URLs,
credentials and Pod specs are not uploaded. Unknown reasons become `Other`.

The observer is bounded to 275 seconds plus a five-second forced termination
grace, and is stopped/reaped immediately after the unchanged 270-second native
wait. The migration Job retains its 180-second deadline. Native wait status is
preserved. Capture and project before later worker-log/assertion failures; a Pod
deleted by deadline cleanup still has its earlier startup states recorded.
Watch failure, incomplete streams, missing owned Pods and observation/size limits
are explicit diagnostic limitations. These reports never satisfy migration
success or the expected PostgreSQL runtime-role refusal. Signal patterns are
hints for investigation, not proof of a particular package authorization defect.

Completed main run 40095134325 retained a deadline-expired Job and an empty worker
list because the old harness inspected Pods only after the terminal wait. This
establishes evidence loss, not the underlying startup/timeout cause. New live
evidence first runs after merge under the unchanged main-only guard; no timeout
increase, retry, grant change or speculative migrator repair is introduced.

Live OCI/schema acceptance first runs after merge. This phase establishes
selected migrator/schema parity only: it does not upgrade a retained database,
test a populated upgrade or restore, grant runtime table privileges, run the
control-plane server, establish readiness/adoption, authorize production
migration/promotion, or retire the supported host commands. The existing M04
manual pause/backup/migration/recovery process remains authoritative.

### Runtime database grant parity

After selected-image migration, the same trusted-main fixture applies the exact
reviewed grant transaction from `scripts/control_plane_migration.py`. The CI SQL
copy is byte-checked offline against that function. Fixture administration alone
applies grants; this is not a new production migration/grant WorkflowTemplate.

A CI-only Argo resource wrapper creates PostgreSQL client Jobs pinned to the
fixture server digest. Workers receive only the existing `runtime-url` Secret
key, authenticate over TCP, have no Kubernetes API token and run non-root with a
read-only root filesystem. No CI token scope or stored credential is added.

| Case | Required evidence |
|:--|:--|
| Ungranted | Worker exits 3 at the exact SQL privilege-matrix refusal. |
| First | Administrator applies reviewed grants; worker verifies the effective table privilege matrix, schema USAGE and no schema/database CREATE. |
| Repeat | Reapply the transaction; a fresh worker succeeds and table/schema ACLs match the first application. |
| Excess DELETE | Administrator deliberately grants DELETE on `runs`; a fresh worker refuses at the same exact policy boundary. No rows are deleted. |
| Corrected | Administrator reapplies the reviewed transaction; a fresh worker succeeds and ACLs match the first application. |

Successful workers execute real reads and zero-row INSERT/UPDATE statements on
permitted tables inside a rolled-back transaction. They also require actual
permission denials for schema DDL, DELETE and migration-ledger reads. All seven
application tables have precisely the reviewed table privileges; the ledger and
six retained native-worker tables have none of those runtime table privileges.
This checks the selected schema, not arbitrary future schemas or column grants.
A missing or unexpected table fails rather than silently expanding the policy.

Artifacts use `runtime-grants-<case>.{workflow,job,worker}.json`, constant outcome
markers, safe ACL/schema observations and sentinel OIDs. Expected refusal requires
failed Workflow/Job state, one worker exit 3 and the exact SQL policy error;
image pulls, connection errors and authentication errors cannot substitute.
Raw client logs, role/password snapshots, Secret snapshots and table data digests
stay private. Before/after checks preserve all table data, schema/relation OIDs,
ledger hashes/timestamps, sentinel bytes, role/password and Secret identities,
PostgreSQL Pod and cluster UID. Five distinct Workflows/Jobs remain queryable
until normal owned-cluster cleanup; failed attempts are never replaced.

Run offline checks with `make validate` and lint the CI wrapper alongside the
staged production templates with Argo. Live acceptance first runs on trusted main
after merge. This phase does not run the control-plane server, load its chart or
runtime package, prove full readiness/adoption, authorize retained-environment
grants/migrations, implement recovery or retire host entrypoints. Real server
wiring and complete readiness remain the next integration gate.

### Selected server rollout and HTTP parity

After runtime grants, the trusted-main job runs the actual selected server from
its pinned Helm chart and immutable Kustomize runtime package. The owner approved
the existing private-source token only for the pinned `perfeng-k8s` checkout,
with unpersisted credentials and no token passed into the cluster
([ADR 0022](decisions/0022-use-pinned-source-for-server-parity.md)). The job's
existing `GITHUB_TOKEN` authority remains unchanged. This source credential is
not the Git/registry token already used inside the disposable fixture.

Verify checkout origin, exact HEAD and clean content, then validate the selected
chart/bundle/package. Render with the reviewed Application values and existing
checksum-verified Helm pin. Check seven expected resources and exact immutable
ConfigMap bytes before applying. Fixture-only runtime/authentication/pull Secrets
reuse existing transient database/storage/package identities. The Deployment
keeps its original UID, starts the selected image without command overrides and
uses the chart's actual runtime mounts, probes, resources and Job-controller RBAC.
The one owned kind node receives the chart's required placement label. The
placeholder Deployment transitions via a complete API replacement guarded by
its observed UID/resourceVersion. A first strategic apply could retain its
old, differently named container; the guarded replacement avoids that merge
and refuses races without deleting or recreating the Deployment.

| Case | Required evidence |
|:--|:--|
| First | Original native Deployment/Service observer children succeed; selected image and runtime references match; installed production HTTP adapter validates real `/livez`, `/readyz` and `/metrics`. |
| Repeat | A fresh Workflow succeeds using the same server Pod and unchanged Service projection. |
| Stopped | Administrator scales to zero and waits for the server Pod to disappear. Fresh HTTP-only worker exits 2 with the exact `operational endpoint failed: livez` refusal. Generic image/executor failure is insufficient. |
| Corrected | Explicit scale-up; fresh native/HTTP Workflow succeeds with the original Deployment/Service and a new server Pod. |

No Application or foundation status is fabricated for this phase. The unchanged
HTTP adapter is imported from the installed verifier ConfigMap; production code
and its two-module/708-line budget remain intact. The HTTP main container masks
the default API token path and checks absence at runtime, while Argo's executor
retains its existing task-result identity. It receives no Git or runtime Secrets.

Artifacts use `server-<case>.{workflow,worker,pods,service}.json` as applicable,
constant HTTP outcomes and safe schema/ConfigMap observations. Raw HTTP logs,
render inputs with Secrets and credential/password snapshots remain private.
Compare schema/ledger/OIDs, sentinel bytes, role/password identities, runtime
Secrets/ConfigMaps and provider/cluster UIDs. Retain all four Workflow UIDs until
normal owned cleanup. Failed Workflows are never replaced or blindly retried.

The stopped case proves the adapter refuses while the real server is absent;
it does not distinguish all possible transport failures. The selected server's
`/readyz` is a static service signal, not a database/storage dependency check.
Full readiness-parent composition, real Argo CD reconciliation, foundation
chart/storage acceptance, workload/API behavior, recovery, promotion and script
retirement remain separate. Live server acceptance is pending trusted-main CI
after merge; no local cluster execution is permitted by this fixture.

### PostgreSQL chart and PVC retention

Trusted-main database setup now renders the repository's actual `charts/postgres`
from the event checkout, with reviewed defaults and the existing immutable
`perfeng-postgres-auth` Secret. This replaces only the main fixture's standalone
Pod/Service; read-only PR child parity keeps that original transient provider.
The shared SQL/log helpers select the corresponding Pod explicitly. Existing
external-state, selected migrator, runtime-grant and real-server checks all run
against `postgres-0` after the restart below.

Require kind's existing `standard` local-path provisioner with
`WaitForFirstConsumer`; no provisioner is installed or repaired. Label only the
owned node for chart placement and create the unmodified StatefulSet/Service.
The chart's retained claim policy, non-root/no-token Pod, image, resources and
claim templates are used as published. No Helm install or second reconciler is
introduced. These PVCs remain disposable with the owned kind node.

| Case | Required evidence |
|:--|:--|
| First | Native chart rollout; a bounded client Job authenticates over the actual TCP Service; unchanged production StatefulSet observer reports current generation/revision and one ready/updated replica. |
| Restart | Native `kubectl rollout restart` and bounded rollout wait replace the Pod. Require a new Pod UID, original StatefulSet UID, advanced generation and converged new revision. |
| Retention | Original bound PVC UID/PV binding, PV UID/claim reference, Service and immutable auth Secret remain unchanged; canary OID/bytes survive in the separate `postgres` database. |
| Downstream | Existing first-migration test still sees an empty `perfeng` application database; all subsequent database/grant/server gates use the restarted chart Pod. |

The chart's Unix-socket readiness can briefly observe initdb's temporary server.
The CI-only PostgreSQL client Job therefore independently authenticates over TCP,
before and after restart, with native bounded startup retries (three retries,
120-second deadline). It uses the same pinned client image, existing admin Secret
and no API token. A failed connection/SQL command cannot establish readiness.
This adds no probe override to the chart.

Fixture administration sets `log_line_prefix` through `ALTER SYSTEM` and reload,
then verifies it survives restart. This preserves the migrator's user/database/
attempt diagnostic without changing the chart's Pod template or weakening that
existing refusal criterion. Raw credentials and logs stay private. Artifacts use
`postgres-chart-<case>.{workflow,observation,storage,pv,service,client}.json`,
non-secret canary observations and old/new Pod UIDs. Native Workflows and client
Jobs remain until normal owned teardown.

This is Pod-replacement retention on kind local-path storage, not a backup,
restore, disaster recovery or retained-environment migration test. Local execution
remains prohibited. Live acceptance first runs after merge on trusted main.
SeaweedFS still uses its transient direct-S3 fixture; Argo CD convergence and
complete readiness/adoption, promotion and measured retirement remain open.

### SeaweedFS chart, proxy and PVC retention

Trusted-main bucket setup now uses the repository's actual `charts/seaweedfs`
with reviewed defaults: the real SeaweedFS server, HAProxy sidecar, Service,
proxy ConfigMap and retained claim. Read-only PR child parity keeps its original
direct-S3 transient provider. This changes only fixture composition, without
modifying a production chart, template, image pin or CI credential permission.

The chart's immutable `perfeng-s3-server-auth-v3` Secret receives only the existing
fixture server's approved `s3.json`. It is independent of the deliberately denied
`perfeng-s3-auth` client; fixture administration corrects only that client as
before. Secret snapshots cover the server identity throughout main acceptance.
These bootstrap credentials test storage/proxy behavior; they do not reproduce
or establish production least-privilege S3 IAM policy.

Render three expected chart resources without Pod-template overrides. Require
kind's existing standard local-path provisioner; reuse the owned node placement
established by PostgreSQL setup. The unchanged production StatefulSet observer
must report current generation/revision and one ready/updated replica. Actual
external-state preparation then exercises its unchanged first/repeat/refusal/
correction sequence through the chart Service on port 8333, which reaches the
private backend through HAProxy.

A separate credential-free/API-token-free native peer Job first connects to the
actual Pod's proxy port 8333. It then requires exact connection refusal for S3
backend HTTP/gRPC (9000/19000), master (9333/19333), filer (8888/18888) and volume
(8080/18080) listeners. An open private port, proxy refusal, timeout or generic
network error fails the Job; it cannot count as isolation. The image is the
existing pinned Python runtime and the Job has no retries and a 60-second bound.
This peer runs before and after restart; it is not a full network-security audit.

After complete external-state acceptance creates its existing sentinel, native
`kubectl rollout restart` must replace the SeaweedFS Pod and converge an advanced
StatefulSet generation/new revision. Preserve the original StatefulSet, bound
PVC/PV/claim identity, Service, proxy ConfigMap and immutable server/client
credentials. Compare sentinel object metadata and downloaded bytes across the
restart. The later selected server gate requires the same post-restart provider
Pod UID. No object overwrite, bucket deletion, Secret rotation or PVC recreation
is used as recovery.

Artifacts use `seaweedfs-chart-<case>.{workflow,observation,peer,storage,pv,service,proxy,object}.json`,
old/new Pod UIDs and non-secret sentinel bytes. Peer Job/worker state is captured
even on failure; upload only constant outcomes and allowlisted exception/open-port
signals, with raw peer logs private. Keep raw credentials and Secret
snapshots private. Workflows/peer Jobs remain until normal owned-cluster teardown.
Missing storage, failed native rollout, exposure or changed object/identity fails
without repair. The v1 bucket worker's hidden lookup stderr limitation remains:
this does not newly prove per-Job authorization-versus-provider-error causality.

Live chart/proxy/PVC acceptance is pending trusted-main execution after merge;
no local live run is authorized. Pod-replacement retention on kind is not backup,
restore or host/node-loss recovery. Production IAM-policy acceptance, real Argo CD
convergence, complete readiness/adoption, promotion and measured retirement remain
open. Both foundation charts and the selected server now have CI acceptance
paths; that is not a completed deployment migration or production-readiness claim.

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
`perfeng-release-verifiers-v3` ConfigMap and
`perfeng-verify-release-quiescence-v3`, `perfeng-adoption-records-v1` and
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

An independent second receipt invokes the unchanged native create step, then reaches a
[suspension barrier](https://argo-workflows.readthedocs.io/en/release-4.0/walk-through/suspending/).
The harness waits at most 120 seconds for that barrier and
[terminates](https://argo-workflows.readthedocs.io/en/latest/cli/argo_terminate/)
the fixture Workflow. The case requires a successful create node, a failed
terminated Workflow and no adoption-result output. A fresh native observation
must still read the original receipt and its first-attempt provenance.

Both response-loss and interruption receipts are read by fresh native operations
and evaluated independently; both must survive Workflow deletion. The runner
calls the production `plan_adoption` predicate with those real
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

### Native receipt response loss

[ADR 0030](decisions/0030-inject-disposable-ledger-response-loss.md) records owner
approval for a separate CI-only TLS proxy in the existing boundary job. Generate
a native resource fixture from the published receipt-create template, preserving
manifest meaning and its 60-second deadline. A fixture kubeconfig routes this
native create through the proxy. Fixed core discovery is served locally, never
forwarded; `--validate=false` skips only client OpenAPI lookup. Kubernetes still
validates the actual receipt, and only the fixed create options are accepted.
Argo appends `-o json`, so kubectl's incompatible `--raw` option cannot be used.
Subsequent observation/retry operations use unchanged production
templates directly. The generated fixture is linted offline before submission.

Resume the suspended writer only after its exact receipt and first-Workflow UID
are configured and the proxy is ready. Its public kubeconfig references its
short-lived ServiceAccount token file and temporary fixture CA. The proxy has
no token mount and forwards the caller's release-adoption token only to the
fixed Kubernetes API with the real cluster CA. No GitHub credential is present.
Both TLS hops remain verified; no system trust store changes. The existing pinned
Python image runs standard-library HTTP/TLS; certificates are generated using the
already locked runner dependency. The Pod is non-root, read-only and bounded.

Only one exact bounded receipt POST, with the pinned field manager/validation
query, can reach the API. An upstream 201 must carry
matching immutable content/provenance and a real UID. The proxy then closes the
client connection before sending any response headers, recording only a safe
status/UID/content-digest event. Further requests are refused without forwarding.
API denial, malformed output, TLS errors or a pre-commit transport failure cannot
count as a committed-response loss. No token/header/body or exception is logged.

Require the writer to fail without termination or success outputs, with a matching
release-adoption worker. A fresh direct native observation must read that exact
receipt with no pointer. A fresh unchanged direct create must report AlreadyExists;
another read must preserve its full object. The unchanged pure planner, using the
explicitly synthetic readiness/root inputs above, must skip receipt creation and
request only the pointer. Existing completion/race/retention cases continue with
the response-loss receipt's original UID and first-attempt provenance.

Safe proof is `ledger-response-loss.outcome.json`, accompanied by the failed
Workflow and worker projections. Keys, kubeconfigs, raw proxy logs and expected
payload configuration stay outside uploaded results. Missing proof or a failed
TLS/identity/content check fails acceptance; do not bypass TLS, enlarge grants,
reset history or treat a generic failed Pod as sufficient evidence. Live proof
awaits the PR's disposable-kind CI run. Complete-parent ambiguous-response
recovery, promotion and operational cutover remain separate.

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
`perfeng-verify-release-readiness-v3` child through a step-only wrapper using
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
`perfeng-release-preflight-v3` and existing narrow `release-preflight` grants.
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

## Actual Argo CD convergence

Trusted-main's six scenario jobs have an owner-approved 60-minute budget
([ADR 0034](decisions/0034-bound-disposable-acceptance-fixture-lifetimes.md)). The
shared credential-bearing S3 observer has the same bounded 3600-second deadline
and sleep, and remains subject to owned cluster cleanup. Run 40191776524 reached
later S3 retention checks after its former 900-second lifetime, causing exec into
a completed Pod. No release-operation or controller readiness deadline changes.

CRD readiness first waits for the exact `status.acceptedNames.kind`, then
requires `Established=True`, within one outer 90-second bound for both CRDs.
The [upstream naming controller](https://github.com/kubernetes/apiextensions-apiserver/blob/v0.35.0/pkg/controller/status/naming_controller.go)
writes accepted names and initialized conditions in the same status update.
This avoids kubectl's initial-null condition accessor failure without accepting
`Established=False`. A real pinned-client loopback API regression exercises
null, initialized/unready and established updates; it does not access a cluster.

Failed Redis, repository-server or application-controller rollout retains
`argocd-startup.failure.json`: allowlisted component/phase/container state/reason
and fixed signal categories only. Pod events are fetched with a five-second
request, matched to selected Pod UIDs in `argocd`, and projected to allowlisted
reasons and fixed categories, including HTTP authorization, throttling and
registry unavailability. `events: unavailable` distinguishes failed event
capture while retaining available status/log categories. Raw events, Pod JSON
and bounded relevant container logs
(40 lines/8192 bytes per container, five-second requests and one-second running
wait) stay private and are removed after classification. Names, specs, arguments,
environment, arbitrary messages and raw logs are excluded. Failed collection or
formatting emits only `diagnostics: unavailable`; the original rollout failure
still determines the outcome. Run 40202197628 has four Redis rollout timeouts;
its retained initial-leg report shows `ErrImagePull` after successful init. The
fifth leg has an independent initial-null CRD failure. Run 40214268159 later
retained UID-correlated Redis rate-limit events in two jobs after successful
init; the specific quota/account/IP attribution is unavailable. Selected provider
revision `4017bdda1e6d1f14592e2112ab78c1e67f710981` now supplies the approved
Docker Hub multi-platform digest and `IfNotPresent` policy. Actual cold-pull and
complete-parent acceptance await the post-merge main-only path. No additional
credentials, permissions, scenarios or deadlines are introduced.

After foundation chart and selected-server acceptance, trusted-main installs the
infrastructure lock's Argo CD bootstrap from its validated clean pinned checkout.
Earlier phases retain the minimal CRD; this phase installs the actual
controller and never writes Application status.

The owner-approved [ADR 0023](decisions/0023-authorize-disposable-argocd-source-access.md)
extends the existing contents-read credential to that checkout and one immutable
repository Secret for the exact private `perfeng-k8s` URL. Its underlying rights
are not attenuated. An exclusive private runner file transfers the token from a
dedicated staging step; it is absent from the acceptance step environment, Git,
Helm values and artifacts. Remove it after Secret creation and on failure;
owned cluster teardown removes the Secret. PR CI receives no source token.

Mark the root `skip-reconcile` before installing the controller. The selected
Application keeps its exact committed sources/values/destination and disabled
automatic sync. Manual operations do not prune, force or replace. Each fresh
operation must complete with its case marker, exact compared sources/revisions,
`Synced`/`Healthy` and no conditions. The unchanged production Application observer
then succeeds first/repeat. Compare stable observation fields; generation can
change across controller status and operation updates.

Change the live Service target port to 8081. Require the controller to report
that Service `OutOfSync`, with no pending operation, while the wrong port remains.
A fresh manual sync restores the selected `http` target and observer success.
Compare resource UIDs, PVC bindings, ConfigMap/Secret data, schema/roles and
sentinel bytes across adoption/correction. Secret snapshots and raw diagnostics
remain private; results contain selected Application status, observer Workflows
and constant drift evidence.

Live evidence comes only from post-merge trusted-main CI. This does not establish
root/foundation Application adoption or complete release readiness. Production
templates/verifier budget and original host entrypoints remain unchanged; there
is no script-retirement credit.

## Complete release-readiness parent

After actual Argo CD convergence, the same trusted-main job submits the unchanged
`perfeng-release-readiness-v3` with only the existing four request parameters.
It consumes the same pinned Git request, selected commit and actual cluster UID.
No synthetic preflight/resource arguments, alternate HTTP peer or copied parent
are supplied. Existing credentials, token exposure and job permissions remain
unchanged.

The full parent fetches/validates Git inputs, observes both actual foundation
StatefulSets, the real Application, selected Deployment and Service, then runs
the installed verifier and operational endpoint probes. First/repeat results
must validate against the readiness schema and bind the exact nested preflight,
lock bytes/revision/image, cluster UID and API-observed resource UIDs/generations.
Worker evidence must identify the dedicated readiness ServiceAccount and the
successful production verifier's constant outcome. Results are compared exactly;
this fixture performs no rollout during these attempts.

| Case | Required owning boundary |
|:--|:--|
| Wrong request digest | Preflight identity Pod exits 2 with the exact byte-digest refusal; no foundation/Application/Deployment/Service/verification step runs. |
| Revoked Application-read grant | Nested preflight and both foundation observers succeed; the named native Application observer exits 64 with the exact get-denial diagnostic for its ServiceAccount, resource, API group and namespace, before Deployment/Service/HTTP verification. |
| Explicit grant restoration | Reapply only the existing bootstrap Role/Binding, prove named read access, then submit a fresh parent whose full result matches the first. |

Both failures must leave the parent result unresolved and publish no
`PerfengReleaseReadiness` result at any node. A successful nested preflight result
is permitted for the access refusal. Worker/node identity, exit code and exact
owning diagnostics distinguish intended refusals from image, artifact, network
or timeout failures. Keep raw worker logs private and upload constant outcomes,
Workflow/worker projections and validated results only.

Run 40207959980's `pointer-create-conflict` leg reached first/repeat readiness
and correctly refused the revoked Application read. Its recorded native worker
exited 64; the former verifier and invented offline fixture expected 1. This
was an acceptance-contract defect after the API correctly denied the read.
The corrected check binds the dedicated worker UID/account/node, native
`application` templateRef and exact recorded node diagnostic. The matching
worker log may contain raw stderr or the structured logger's escaped form;
unrelated forbidden text cannot qualify. Python's wrong-digest refusal retains
its separate exit-2 contract. Neither 64 alone nor any generic nonzero exit is
accepted. No production template, access grant, scenario or deadline changes.

Compare existing workload/Pod/RBAC/PV/PVC identities, ConfigMap/Secret data,
schema/roles, Git/repository credentials and database/object-store sentinels
across all five attempts. Retain every Workflow UID. Check that the readiness
identity cannot patch Applications/workloads, create Jobs/Secrets, list Secrets
or exec into Pods. Fixture administration temporarily removes and explicitly
restores its existing Application-read RoleBinding; this does not add authority.

Live acceptance runs only after merge in trusted-main CI. Complete readiness
evidence covers the selected control-plane/foundation gate, not dependency
recovery (`/readyz` is static service availability), product smoke/benchmark
qualification, adoption, promotion, root/foundation Application activation or
script retirement. Production templates, verifier budget and operator commands
remain unchanged.

## PostgreSQL Application adoption

After complete readiness-parent acceptance, trusted-main CI adopts the prepared
PostgreSQL chart through its real Application. The owner-approved
[ADR 0024](decisions/0024-authorize-environment-source-for-foundation-parity.md)
adds a second immutable repository Secret for the exact private environment Git
URL using the existing read token. The same private staging file creates both
approved repository Secrets before removal. Underlying token rights are not
narrowed by URL configuration; PR exposure, job permissions and stored tokens
remain unchanged. Raw Secret documents/snapshots stay private and cleanup covers
both temporary documents plus the token file.

The existing PostgreSQL Application is copied only into the private fixture,
with its source pinned to the selected commit and automatic sync/self-healing/
pruning disabled. Production manifests and chart defaults remain unchanged.
Reuse the native Argo CD sync adapter for single-source revision requests;
control-plane multi-source sync still uses its original revision array. Require
fresh first/repeat/corrected operations, exact compared source and sync-result
revision, `Synced`/`Healthy` and no conditions. Each success must also pass the
unchanged StatefulSet observer and authenticated TCP client Job.

Change the Service port name from `postgres` to `pg-ci-drift`, leaving its numeric
port/target routing intact. Require controller-produced Service `OutOfSync`, no
automatic repair and the actual drifted name before explicit manual correction.
All state comparisons after correction must match the initial prepared baseline:
StatefulSet/Service/PVC/PV UIDs/binding, claim/image/Secret references, credentials,
schema/migration ledger, roles/ACLs and canary/sentinel OIDs/bytes. A legitimate
initial adoption rollout may replace the Pod; repeat and Service-only correction
must retain the adopted Pod. `postgres-application-pod-uids.json` records the
before/after identity for later phases. Native Workflows and client Jobs remain
retained. Other Application specs/UIDs are compared and the root stays skipped.

Live acceptance runs only after merge in trusted-main CI. This is PostgreSQL
Application ownership parity, not root/SeaweedFS Application activation,
complete release adoption/promotion, backup/restore, operational cutover or
script retirement. Existing production templates/verifiers and host paths remain.

## SeaweedFS Application adoption

After PostgreSQL Application acceptance, the same trusted-main run adopts
SeaweedFS through its real Application. This reuses ADR 0024's existing exact
environment repository access; no credential, permission or controller changes
are introduced. The shared foundation input validator permits only PostgreSQL
and SeaweedFS, pins their existing source to the selected commit and disables
automatic sync/self-healing/pruning only in the private CI copy. Production
Applications and chart defaults remain unchanged.

Require fresh first/repeat/corrected native sync operations with exact compared
source and sync-result revision, `Synced`/`Healthy` and no conditions. Each
success runs the original StatefulSet observer and credential-free peer Job:
proxy 8333 must connect and all established private HTTP/gRPC listener checks
must return exact connection refusal. Authenticated S3 reads must preserve the
existing sentinel's metadata and bytes through the real proxy. No alternate
object store, administered status or synthetic peer replaces these checks.

Change only the live Service port name from `s3` to `s3-ci-drift`, preserving
numeric 8333 routing and its target. The controller must report that Service
`OutOfSync` while its drifted name remains and automation stays disabled. A
fresh manual sync restores the selected name. State comparisons after all three
successes preserve StatefulSet/Service/proxy ConfigMap/PVC/PV UIDs and binding,
proxy data, claim/Pod configuration, credentials and object metadata/bytes.
Tracking metadata is expected to change during adoption and is excluded from
data comparisons.

Initial adoption may legitimately replace the SeaweedFS Pod; repeat and
Service-only correction must preserve its adopted UID. Record before/after in
`seaweedfs-application-pod-uids.json` for later phases. Retain all native Workflow
and peer Job UIDs. Compare existing PostgreSQL storage, credentials, schema,
roles/ACLs and canary/sentinel OIDs/bytes before/after the entire phase, and require
its adopted Pod UID to remain unchanged. Other Application specs/UIDs remain
unchanged and the root stays skipped. Raw snapshots/logs remain private;
artifacts contain selected Application status, native/peer evidence and constant
drift outcomes only.

Live acceptance executes only after merge in the existing owned trusted-main
kind fixture. It does not activate the root, execute the complete release-adoption
or promotion parent, prove production S3 IAM/recovery or retire host commands.
Published templates/verifiers and operator paths remain unchanged.

## Bounded root reconciliation

After both foundation Application phases, ADR 0025 authorizes reconciliation of
the real root in this disposable trusted-main job only. Render the unchanged
Kustomize tree and require exactly four Namespaces, three AppProjects and seven
Applications. Before unskipping root, mark all six non-control-plane children
`skip-reconcile`: PostgreSQL, SeaweedFS, sample SUT, native search, resource
metrics and benchmark authority. Missing children are created with the skip
annotation already present; existing children retain their UIDs. Control-plane
remains active with its committed manual policy.

Root automatic sync remains disabled. Its canonical source still tracks `main`,
while each manual operation explicitly selects the request's full commit. A
moving `main` fails the revision gate; no source rewrite or newer commit is
silently accepted. First/repeat/corrected operations must report fresh markers,
exact source/revision, complete root inventory, `Synced`/`Healthy` and no
conditions. Change only the live `perf-sut` environment label, require actual
`OutOfSync` with the drift still present, then explicitly correct it by root sync.

Root restores committed foundation source/automation specs. Their separately
managed skip annotations must survive actual normal apply, with unchanged child
operation records and UIDs. Compare workload specs/Pod UIDs, credentials,
database schema/roles/ACLs/canary/sentinel, volume identities/binding and S3
metadata/bytes after every operation. No rollout is expected or permitted.
Private state snapshots stay outside artifacts; inspect `root-*.application.json`,
`root-*.children.json` and `root-drift.outcome.txt` for safe evidence.

This proves root-owned object convergence only. Skipped children do not establish
full-tree health; no full release-adoption/promotion parent, ledger write,
retained deployment, production recovery or script retirement is included.
Actual live acceptance remains pending the post-merge trusted-main run.

## Complete existing-release adoption parent

After bounded root convergence, trusted-main installs the unchanged published
`perfeng-release-adopt-existing-v3` and `perfeng-plan-release-adoption-v3`, using
ADR 0017's existing ledger namespace/bootstrap grants and `release-adoption`
identity. No job permission, source credential, controller or production
manifest/verifier changes. The same four request parameters still point to the
existing pinned Git request for this disposable cluster. Generated CI intent is
test input, not human authorization to adopt a retained environment.

The first submission must run fresh nested readiness/preflight, read the actual
root and missing records, run all three planner phases, create exactly one
immutable receipt and the initial current pointer, then verify readbacks before
publishing a schema-valid result. Compare planner output with the unchanged pure
policy using actual observations. Bind nested readiness to the selected lock,
live workload identities/generations and original preflight. Verify exact request
bytes, receipt payload, first Workflow/request provenance and receipt UID binding
against actual stored objects. Early planner results must remain JSON `null`.

Submit an exact repeat as a fresh Workflow: both create nodes must be skipped,
and complete stored objects, original provenance and result identities must be
unchanged. Revoke only `perfeng-local-root` from the existing Application-read
Role, retaining control-plane access. A fresh parent must finish readiness then
fail at its native root read with exact `Forbidden`/identity/resource evidence,
one main exit 1, no planner/create operations and no adoption result. Other
infrastructure failures do not qualify. Stored records must remain unchanged.
Restore only the checked-in grant and submit a fresh successful correction.

Then change only the live `perf-sut` environment label to `ci-drift` and require
the actual root to report `OutOfSync`. A fresh complete parent must finish
readiness and native root/current/receipt reads, then refuse at `plan` with main
exit 2 and the exact `root is stale, syncing or not at the reviewed selection`
diagnostic. No create, later planner or adoption result may run. Compare native
ledger observations with stored objects and require the complete ledger to
remain unchanged. A passing readiness check cannot waive this root-state gate.

Require the full fourteen-object root inventory at the selected revision, with
only the `perf-sut` Namespace out of sync, no conditions/active operation, root
auto-sync disabled and six child skips intact. The label must still be wrong
after refusal. Reconcile root only through a fresh manual operation at the
selected commit, require label restoration and exact controller evidence, then
submit another fresh complete parent. Both creates must again be skipped and
the original baseline identities, bytes and provenance preserved. Do not patch
status or ledger records, change the root source or retry completed nodes.

Reuse the root phase's private snapshots for workload specs/Pod UIDs, credentials,
database schema/roles/ACLs/canaries, volume identities/binding and S3 metadata/bytes.
All six child skips, child UIDs/operations and root's selected revision/manual
policy remain required after every case. The planner conservatively compares
root observations: only generation, resourceVersion and reconciledAt refresh
bookkeeping is excluded from equality under ADR 0036. Both samples must pass
root validation and every other field must match. Do not pause the controller,
fabricate status or automatically retry until green.

Prove denied ledger patch/delete, workflow-configuration writes, Secret listing,
Job creation, workload mutation and Application sync. Retain eight distinct
Workflow UIDs, then foreground-delete **only the original completed disposable
Workflow** and require complete receipt/pointer objects to survive unchanged.
Keep raw logs and record/state snapshots private. Uploaded evidence is limited
to `adoption-parent-*.workflow.json`, worker identity/exit projections, successful
result JSON and constant refusal/retention outcomes.

Concurrent exact retries are tested only after the baseline exists. Submit the
CI-only `adoption-lock-workflow.yaml`: one native Suspend template, the existing
ServiceAccount and the same published release mutex, with a 300-second lifetime.
Require its root Suspend node to run and its native status to hold that mutex.
Submit two unchanged production parents before releasing it. Both must report
`Pending`, the exact mutex/holder identity, the published effective synchronization
specification and original request parameters, with no worker Pods or results.
While they wait, require unchanged ledger and workload/data snapshots.

Resume the holder explicitly; timeout or holder failure is not success. Each
queued UID must later pass the existing complete-parent evidence checks with
fresh readiness/planning and skipped creates. Require controller entrypoint-node
intervals to begin after holder completion and not overlap, allowing either
retry to run first. Preserve full receipt/pointer objects, provenance, result
identities and all root/child/workload/data boundaries. Keep holder/queue snapshots
and `adoption-lock.result.json` as non-secret controller evidence. The fixture is
included in offline Argo lint; it adds no worker image or operator command.

This proves serialization of complete exact retries by the existing local mutex.
It does not prove racing initial creates, partial writes, ambiguous responses,
multi-controller/database locking or exclusion of host operators and Argo CD.

### Receipt-only recovery scenario

Owner-approved `initial` and `receipt-only` matrix legs run the existing job on
independent runners/owned clusters. Fail-fast is disabled; permissions, credential
rights and pinned sources remain unchanged. Each job now has the approved
60-minute budget described above. Initial keeps
its existing request ref. Recovery appends `-receipt-only` to its run/attempt ref;
artifact names identify both scenarios. Publisher/cleanup reject unknown scenarios
and cross-scenario ownership. PR fixtures cannot select receipt-only mode.

Require only `kube-root-ca.crt` in the fresh recovery ledger, then create the CI
quota with `count/configmaps: "2"`. Wait for hard two/used one before submitting
the unchanged parent. Readiness, initial planning, receipt creation/readback and
pointer planning must succeed; only `create-pointer` may fail, with main exit 1
and exact quota/resource evidence. Require no final result, an absent pointer and
the actual immutable receipt's exact bytes/UID/provenance. Early results remain
`null`; no later read/planner may run.

After hard/used two, raise only the fixture quota to three using atomic original
UID/current-resourceVersion/old-limit tests. Wait for initialized hard three,
then submit `recovered` as a fresh complete parent. It must skip receipt creation,
preserve the entire failed Workflow's receipt, create only the pointer with its
correct receipt UID and publish a schema-valid result. Verify hard/used three
and retain a safe quota projection. Never edit/delete history or retry successful
nodes to repair the partial attempt.

Use the recovered result as this leg's comparison baseline. Run the same repeat,
refusal, correction and concurrent retries with full record/workload/data
retention. The base cases contribute eight adoption Workflow UIDs in initial and nine in recovery, including
the failed origin and fresh recovery. Delete only the original Workflow (the
failed origin in recovery) and prove completed ledger retention. Raw records/logs
stay private and artifacts remain scenario-specific.

This proves deterministic pointer-admission failure followed by fresh complete
parent recovery, not a lost response or racing initial creates. Live acceptance
awaits the post-merge trusted-main run in both scenarios.

Actual acceptance runs only after merge in the existing trusted-main owned kind
job. This does not prove recovery from racing initial creates, other partial
writes or ambiguous responses, promotion or pointer advancement, full-tree health, retained adoption,
export/restore, production admission or script retirement. Native storage and
pure recovery tests retain their existing separate, narrower claims.

### Adoption request boundary acceptance

[ADR 0028](decisions/0028-publish-adoption-request-boundary-fixtures.md) records
owner approval to publish three deterministic requests in each existing temporary
commit/ref. The original is byte-compatible. Changed-request adds one newline
with identical parsed JSON; upgrade-request changes only expectedCurrent to the
original digest. All three pass the v1 schema before publication and bind
distinct exact-byte identities/paths to the same delivery commit and reviewed
selection. Ref journaling and conditional cleanup are unchanged.

After successful baseline/recovery/concurrent cases, before retention and
destructive receipt tests, submit both inputs as fresh complete adoption parents.
Verify each nested preflight's schema, path, commit, digest, selection and lock
identities, then actual readiness and native root/ledger reads. Require a healthy
selected manual root and the original pointer. The candidate receipt must be
absent both in the workflow and direct API. Only plan may fail, with exit 2 and:

- Changed-request: `current pointer conflicts or references a missing/replaced receipt`.
- Upgrade-request: `request/readiness identity differs or adoption is not initial`.

No later worker or final result may appear. Original receipt/pointer objects
must equal their accepted baseline around both attempts; workload/data retention
and six child skips remain required. Upload safe UID/digest refusal reports and
generated request/parameter files, keeping raw ledger/logs private. Quota errors
cannot count as plan refusals. Both scenarios continue the existing retention
and destructive identity checks. Live proof awaits both post-merge legs; this
does not implement promotion.

### Receipt identity refusal acceptance

[ADR 0027](decisions/0027-inject-disposable-adoption-receipt-identity-faults.md)
records owner approval for destructive receipt fault injection only after the
existing retention proof in both disposable scenarios. No new runner, scenario,
request ref or credential permission is added. Including request-boundary cases,
initial now has twelve adoption Workflow UIDs; receipt-only has thirteen,
including its failed origin.

Recheck cluster identity and require a fresh exact ledger match against the
accepted baseline. The pure unchanged policy must validate the original
receipt/pointer against its verified readiness/root observations. Generate private
DeleteOptions with the receipt's original UID and current resourceVersion.
Native `kubectl delete --raw` sends that body to the exact namespace/name URL;
a changed identity/version must conflict rather than delete a different object.
Wait for absence and submit a fresh production adoption parent.

Readiness/root/native ledger reads must succeed and match the actual API. Only
`plan` may fail, with main exit 2 and the exact diagnostic
`current pointer conflicts or references a missing/replaced receipt`.
Require no plan/final result and no downstream worker. The pointer and all
remaining ledger records must equal their pre-attempt snapshots.

Recreate only the same receipt from original immutable payload, labels and
provenance, dropping server-assigned metadata. In receipt-only, first wait for
quota usage to fall to two. Native create cannot overwrite an existing winner.
Require the new receipt's UID to differ while the pointer still binds the old
UID. A second fresh parent must refuse at the same precise boundary. Preserve
the full before/after records privately and require exact equality around each
refusal. Both cases run existing workload/storage/credential/data retention,
manual-root and six-child skip checks.

Only safe UID decision reports, Workflow/worker evidence and constant outcomes
are uploaded. No successful adoption result is produced by these cases. Leave
the deliberate conflict intact until owned-cluster teardown; never repair the
pointer or delete additional history. Live proof awaits both post-merge legs.
This does not prove initial-create races, ambiguous API responses, restoration,
retained recovery or operational cutover.

### Adoption API authorization refusals

Both the native-ledger boundary job and the existing trusted-main adoption
scenarios source `adoption-authority.sh` before receipt identity fault injection.
The helper impersonates `release-adoption` with its namespace, all-ServiceAccount
and authenticated groups explicitly specified. Kubernetes already supplies the
default ServiceAccount groups when impersonation omits them; this is additional
API refusal evidence, not a correction to group handling.

Eleven actual API operations must return exit 1 and Kubernetes `Forbidden` for
the exact user, verb, resource, API group and namespace: patch/delete the receipt
and pointer, create a workflow ConfigMap or Secret, list Secret names, create a
Job, patch the control-plane Deployment, and patch the root/control-plane
Applications. Every write uses server dry-run, including deletion, so an
unexpected grant cannot persist the probe. Secret listing prints names only;
an unexpected grant can expose Secret bodies to the client, never to evidence.
ConfigMap, Secret and Job probes use single-object manifests with generic
`kubectl create --dry-run=server --validate=false`. API validation remains active;
only client schema lookup is disabled. Collection creates have no object name
in the authorization attributes; grouped resources can be qualified (`jobs.batch`)
in the diagnostic prefix. The check requires the standard server Forbidden marker
and the complete authorization subject/operation, without guessing those prefixes.
An early four-case smoke (three creates and name-only Secret listing) runs against
the real API and actual adoption RBAC before Argo installation or any ledger
records. The full eleven-operation matrix runs after ledger persistence. Offline
tests exercise parsing, safe aggregation, manifests and failure propagation;
they do not substitute a fake API for Kubernetes authorization proof.
Allowed ledger get/create and denied Pod exec remain authorization reviews;
connect subresources have no equivalent safe dry-run and no exec is attempted.

Cluster identity is checked before and after. Canonical snapshots of the selected
receipt and pointer must remain identical. Only constant decisions are uploaded
in `adoption-authority.tsv` and the early `adoption-authority-smoke.tsv`; success
requires the terminal `suite verified` row (tab separated). Each API row includes
exit status and six 0/1 matches: marker, subject, verb, resource, group, namespace.
Independent probes are collected even after a mismatch; any mismatch fails the
suite and blocks downstream work. Raw API diagnostics and snapshots remain private. Unexpected
success, transport errors, missing objects, mismatched refusals or changed
records fail closed. This tests API authorization through privileged fixture
impersonation, not ServiceAccount token authentication or production admission.
Offline tests exercise the Bash protocol with an isolated command boundary;
actual Kubernetes proof awaits the existing CI jobs. No permissions, production
templates, jobs, credentials, scenarios or request refs are added.

### Disposable run qualification and stage evidence

`stages.tsv` identifies bootstrap, authority smoke, controllers, registry
prerequisite, maintenance, ledger, database, bucket, registry, readiness, observer,
preflight and cleanup boundaries. The trusted-main branch has its own parent
stage. Started/succeeded/failed states preserve the worker exit code; the wrapper
never catches a source failure and continues. This separates a prerequisite or
fixture failure from a policy refusal. Existing bucket allocation and registry
error categories remain the detailed diagnostics for their stages.

After teardown, `run-outcome.json` records the exact checked-out commit,
`kube-system` UID, overall exit code and cleanup exit code. A passing qualification
requires a nonempty UID, zero exits, both successful authority reports and all
boundary stages completed. `kubernetes-version.json` records the actual pinned
client/server versions. No token, Secret body or raw client diagnostic is added.

For approved pre-handoff acceptance, dispatch the existing workflow on the issue
branch: `gh workflow run release-workflows-kind.yml --ref feature/issue-N`.
Observe the resulting run's exact `headSha`; never qualify a different revision.
The PR #231 stabilization requires two successful runs of the same final commit
on fresh clusters, with distinct namespace UIDs and successful cleanup. A PR run
can supply one pass. Every code correction resets this qualification. Record run
URLs and safe evidence in the PR, then pause for review. No retained cluster,
self-hosted runner, extra token right or trusted-main dispatch is authorized by
this command. Future integration changes require actual disposable acceptance
before handoff when dispatch is authorized; offline tests alone are insufficient.

### Complete-parent receipt response loss

[ADR 0031](decisions/0031-exercise-complete-adoption-response-loss.md) records
owner approval for a third trusted-main `response-loss` scenario. Its temporary
request ref and uploaded artifact use the same scenario-qualified ownership and
cleanup checks as `receipt-only`. Job permissions, private source access and the
main-only execution guard are unchanged.

The failed origin uses a generated CI-only parent copy. It preserves the complete
published composition except for a bounded native suspension after the real
planner and the existing exact-receipt transport adapter on receipt creation.
Offline checks reverse those differences to the exact published parent/resource
templates; live evidence also checks the controller-stored fixture composition.
The production templates themselves are unchanged.

Trusted-main retains its runner GitHub credential for the owned temporary-ref
cleanup. Invoke the response-loss helper in a subshell that clears `GITHUB_TOKEN`
and `GH_TOKEN` and disables its inherited EXIT trap. The unchanged helper guard
therefore sees no runner token, including in subprocesses. Only the parent owns
cleanup and retains its original exported credentials even if the helper exits
explicitly. After success, the caller records the two verified Workflow labels
and recovery baseline/origin; child shell variables are not imported. A failed
helper must abort before that bookkeeping or later cases.

All four create-fault scenarios retain `adoption-<scenario>.barrier.json` before
fault setup or resume. The existing 240-second poll distinguishes a running
barrier on a running parent, a terminal parent, an API read failure and deadline
expiry. Each API read is bounded to ten seconds. Only `barrier-ready` continues;
an unavailable Pod observation does not hide a Workflow failure. The report
contains bounded, allowlisted step/type/phase, container state/reason/exit code
and fixed message categories, including parent-level errors without nodes.
Raw Workflow/Pod responses, messages, arguments, outputs and logs stay private.
Unknown fields become `unknown`; truncated node lists are marked. A category is
diagnostic evidence, not proof of the underlying cause or adoption acceptance.
Inspect this report before changing a grant, template or deadline, then confirm
the cause with the relevant prerequisite evidence. A failed report blocks setup.

At the barrier, recompute the actual plan and bind the proxy to its exact receipt
and originating Workflow UID. Require an empty ledger, start the existing TLS
proxy and explicitly resume. Successful real readiness/planning must precede a
failed receipt-create worker, with no downstream write or adoption result. A
single proxy event must prove HTTP 201, matching content digest/UID and zero
successful responses forwarded. Fresh direct reads must find only that receipt.
Timeout, denial, TLS failure, missing event or wrong provenance cannot qualify.

A fresh submission of the unchanged production parent then uses direct transport,
skips receipt creation and creates only the absent pointer. Compare the full
receipt before/after recovery. All existing repeat, refusal, concurrency,
authority, Workflow-deletion retention and later identity-fault checks continue.
No ledger record is removed to reset this scenario.

Safe fault proof is `adoption-response-loss.outcome.json`, accompanied by
`adoption-parent-response-loss.workflow.json` and its worker projection. Recovery
is verified by `adoption-parent-recovered.workflow.json`, its result and retained
record comparisons. TLS material, raw proxy events, worker logs and configuration
remain private. The fixture copy is explicitly distinguished from the unchanged
recovering parent; neither offline evidence tests nor the native-ledger job prove
this scenario passed live. Full-parent live proof awaits post-merge trusted-main
CI. Pointer response loss, promotion and retained-environment recovery remain
separate.

### Complete-parent pointer response loss

[ADR 0032](decisions/0032-exercise-pointer-create-response-loss.md) authorizes the
fourth isolated trusted-main `pointer-response-loss` leg, including its distinct
temporary ref/artifact. It reuses the existing parent generator, token-free
subshell, TLS proxy and ServiceAccount identity. Production templates and token
rights remain unchanged.

The fixture preserves normal receipt creation/readback and both initial planner
phases. Its native barrier is immediately before pointer creation. Recompute the
exact pointer against the real receipt UID, root and readiness; only that receipt
may exist before proxy setup/resume. The proxy permits one exact pointer create
and must validate every data field plus namespace/name, ownership and assigned
UID. A changed receipt reference, extra binary data, immutable pointer or owner
reference cannot qualify. Receipt response-loss behavior remains separately
verified.

Require an exit-1 pointer worker after successful prior phases, no downstream
worker/result, and one `pointer-committed-response-dropped` event proving HTTP
201 and zero successful responses forwarded. Fresh direct API reads must bind
both records to the actual plan and event. `adoption-pointer-response-loss.outcome.json`
is the safe fault report; raw events and setup material remain private.

The fresh `pointer-recovered` production submission must skip both creates and
produce a freshly verified result. Compare complete receipt/pointer snapshots
before/after retry; preserve both UIDs and original receipt provenance. Use that
recovered result as the leg's baseline for existing retries, refusals, concurrency
and retention checks. No record is reset or repaired. Full-parent live proof
awaits the post-merge main-only leg; this does not implement promotion or retained
recovery.

## Complete-parent initial receipt-create conflict

ADR 0033 adds the owner-approved fifth trusted-main `receipt-create-conflict`
scenario with its own ref/artifact and clean cluster. The existing CI parent
identity/barrier is reused, but only the pre-receipt Suspend is added: receipt
creation retains the production native `templateRef` and transport. No response
proxy or TLS fixture is created. Removing the barrier restores the exact parent.

After successful real readiness, absent-ledger observations and recomputed plan,
the administrative fixture writer creates exactly the planned immutable receipt
and privately saves the API response. Planned provenance names the paused origin;
this simulates a competing create, not a second production Workflow. Resume only
after that create succeeds. Require the native receipt worker's actual
AlreadyExists for the selected receipt, executor exit 64, no downstream worker
and no adoption result. Qualified pinned-executor evidence includes the context
`error when creating "/tmp/manifest.yaml"`; timeout or generic conflict text does
not qualify. The fresh API receipt must equal the complete saved winner snapshot,
and the pointer must remain absent. The safe report is
`adoption-receipt-create-conflict.outcome.json`; raw diagnostics stay private.

The fresh unchanged `recovered` parent must create only the missing pointer,
reverify and publish a result while preserving the complete receipt, UID and
original provenance. Continue all existing refusal/concurrency/retention checks
before the already approved receipt identity faults. Keep GitHub credentials in
the sole cleanup owner outside the token-free fixture subshell. No records are
reset. Full-parent proof awaits post-merge trusted-main execution and its earlier
migrator prerequisite; offline tests do not establish a live pass. Pointer
conflict live acceptance, promotion and retained recovery remain separate gates.

## Complete-parent initial pointer-create conflict

[ADR 0035](decisions/0035-exercise-initial-pointer-create-conflict.md) adds the
approved sixth `pointer-create-conflict` leg with its own temporary ref/artifact
and unchanged 60-minute ceiling. The CI parent adds only a Suspend immediately
before the original native pointer templateRef. Real readiness, receipt creation,
receipt readback and exact pointer planning must precede the barrier. The existing
fixture writer creates exactly the planned pointer; no proxy or alternate
transport is created. Preserve the original receipt and full winning API response.

Resume and require native pointer AlreadyExists, exit 64 and the manifest-path
context naming `perfeng-local-current`. Require no downstream worker or adoption
result. The winning pointer must equal fresh API state, and the original receipt
must equal its actual readback. Both conflict modes explicitly collect fresh
Application/platform/ledger state before verification; stale API evidence cannot
qualify. The safe report is `adoption-pointer-create-conflict.outcome.json`;
raw worker diagnostics and the saved winner remain private.

A fresh unchanged `pointer-recovered` parent must reverify, skip both creates and
preserve both full canonical records, UIDs and receipt provenance. Continue the
existing refusal, concurrency and retention checks before approved final receipt
identity faults. No reset, overwrite, credential right, production template,
deadline or response-proxy change is introduced. Live proof remains post-merge
behind the main-only guard; offline checks do not qualify prerequisites, adoption,
promotion, retained recovery or measured retirement.

## Verification and remaining gates

The initial leg now follows root/child interruption with
`maintenance-deployment-pause.sh`. It reuses the reversible partial-pause copy
after the Deployment patch, proves exact Stop with no parent checkpoint, then
requires settled generation/zero status and raw matching-Pod absence within a
120-second read-only bound. Keep the raw PodList resourceVersion/pagination
evidence separate from ordinary kubectl workload snapshots. Paused-state/data
retention must pass before an independent unchanged checkpoint parent is submitted.
Bind its successful read-only workers and result to the same request, cluster and
resource identities; native and fixture resourceVersions are separate samples.
Only a validated checkpoint permits manual child sync and same-ReplicaSet Pod
replacement, fresh root/native/HTTP health and checked recovery retention.

Inspect `maintenance-partial-deployment`, `maintenance-partial-deployment-checkpoint`
and `maintenance-partial-deployment-recovery` stages and their validated outcomes.
Raw command/Workflow/worker/API/Secret evidence stays private. Original snapshots
remain immutable; later receipt faults reference only the verified new Pod
baseline. The existing happy and root/child cases remain intact. See
[ADR 0041](decisions/0041-exercise-deployment-pause-interruption.md) for the approved
Deployment-only recovery exception. Heavy validation is delegated to existing PR
CI at the owner's request; full parent proof remains post-merge trusted-main.

After the initial leg's stale-guard cases, `maintenance-partial-pause.sh` checks
two reversible parent copies with one suspension immediately after the real
root or child pause patch. Before stopping that exact Workflow, require actual
selected readiness, original adoption records, baseline planning and successful
native worker identities/exits. Require `shutdown: Stop`, the same Workflow UID,
specific controller stop evidence, unchanged workers and no later write or
checkpoint output. Fresh retention keeps the recovered Pod baseline, workload,
storage, ledger, Secret/config and database/S3 identities/data intact. Publish
only `maintenance-partial-<resource>.barrier.json` and validated outcome JSON;
raw evidence stays private. Setup, stop, timeout or retention failure stops the
job without recovery or baseline advancement. This reuses the existing job,
scenarios, refs, credentials and budget; production manifests are unchanged.
See [ADR 0040](decisions/0040-exercise-maintenance-partial-pause.md). Main-only
post-merge execution supplies live proof; offline tests do not.

The trusted-main `initial` leg additionally runs the unchanged maintenance-pause parent after adoption retention and before receipt identity faults. `maintenance-pause.sh` installs only the staged pause templates/approved authority, checks native sequence and quiescence, then explicitly syncs the selected control-plane child for test-only recovery. Original snapshots remain immutable; only a verified same-ReplicaSet server Pod replacement can advance the later retention baseline. Ledger, storage, foundation Pods, Secrets, config, database and S3 data must remain intact. Root auto-sync stays disabled and six other children stay skipped. No other matrix leg changes. See [ADR 0038](decisions/0038-exercise-maintenance-pause-and-checked-recovery.md). Full live proof remains post-merge main-only; this fixture is not a local operator command.

Inspect `maintenance-pause` and `maintenance-recovery` in `stages.tsv`, `maintenance-pause.workflow.json`, the sanitized worker projection, validated checkpoint and fixed recovery outcome. Raw API/Secret/retention snapshots stay private. A failed pause or retention check must not trigger recovery; a failed recovery must not advance Pod/baseline references. The existing six scenarios, ref/artifact ownership, credentials, rights and 60-minute budget are unchanged.

After recovery, the `initial` leg also invokes `maintenance_baseline_refusal` in each existing adoption receipt fault window: once while the receipt is missing, then after its approved recreation with a different UID. It runs no new fault injection or repair. `maintenance-missing-receipt` and `maintenance-replaced-receipt` stages require successful fresh preflight/readiness, the original pointer and exact observed fault, and a sole baseline-planner exit 2. The fixed redacted worker message is bound to the precise ledger cause by replaying the unchanged policy. No guard/write/checkpoint worker or successful checkpoint output is allowed. Before/after full retention checks and the recovered adoption baseline remain strict. Inspect each stage's Workflow/worker projection and validated `maintenance-<case>.outcome.json`; raw logs and API/Secret snapshots stay private. All other legs remain unchanged. These are post-merge main-only gates, not claims of live acceptance before the job runs.

Before receipt deletion, that same helper checks `changed-request` and `upgrade-request` using the already published fixtures in the same owned Git commit/ref. The candidate must preserve the reviewed selection/cluster; only equivalent JSON bytes or the explicit `expectedCurrent` upgrade field may differ from the accepted request. Require schema-valid fresh candidate preflight/readiness, the exact original ledger records, a native missing candidate receipt confirmed by independent before/after API reads, and the precise baseline refusal. For the pure stored-baseline replay, only readiness's request digest is restored to the accepted digest after proving the fixture relation and identical pinned request/selection commits; all fresh resource identities and observations remain unchanged. This check issues no authorization or synthetic readiness output. The shared failure predicate still requires actual exit 2, the fixed runtime message, no later guard/write/checkpoint and no successful result. Original evidence, recovered state and the accepted ledger remain strict. Inspect `maintenance-changed-request`/`maintenance-upgrade-request` stages and their validated outcomes. No new requests, refs, jobs/scenarios, credentials, permissions, budget or production code are introduced.

The existing adoption receipt/request gates use the same published redacted runtime message after their exact-cause policy replay. Their former detailed-reason log expectation could reject correct refusals before maintenance acceptance; synthetic test logs had hidden that mismatch. Offline regression now invokes the actual runtime CLI and policy for all four existing receipt/request faults. Production logging, worker exit/identity checks, downstream/result refusal and ledger retention remain unchanged.

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

The existing initial job also runs three CI-only stale-guard cases after checked recovery/request refusal and before receipt faults ([ADR 0039](decisions/0039-exercise-maintenance-stale-guard-conflicts.md)). Each copy inserts one suspension between the real root, child or Deployment guard and its unchanged native patch; reversal and installed-spec checks bind it to the published parent. Two UID/resourceVersion/spec-guarded fixture writes add/remove a top-level Application annotation or Deployment label. The label avoids Deployment annotation generation increments and ReplicaSet propagation. Resume requires restored metadata/spec/UID/generation and a resourceVersion different from the guard. Require native Invalid refusal, sole patch exit 64, no later write/checkpoint/result, and strict recovered-state/data/ledger retention. Inspect `maintenance-stale-<resource>` stages, safe barrier reports and validated outcomes; raw API/log/Secret snapshots stay private. Setup, timeout, permission, retention or unrelated worker failures never satisfy acceptance. A failed setup stops without automatic repair/resume; any marker remains only in the disposable cluster until cluster cleanup. Production templates, scenarios, credentials, refs and budgets remain unchanged. Full live proof remains post-merge trusted-main; local execution stays deferred.

Before the happy maintenance parent is submitted, the trusted-main initial job audits the unchanged maintenance identity through real API requests. The existing runner-admin impersonation pattern supplies its full ServiceAccount groups without obtaining or forwarding a token. The three named native JSON patches must pass server dry-run and return exactly the expected pause spec. Twenty negative patch/delete/create probes cover other names/namespaces, foundation resources, Git Secret mutation, ledger mutation and forbidden verbs; every mutating command remains server dry-run even if RBAC regresses. Filename handles prevent a preliminary GET denial from being mistaken for a PATCH refusal. Require the actual Forbidden wrapper, exit 1 and matching subject, verb, API group, resource, namespace and named/collection target. Pod exec is reviewed with `auth can-i` only, without a connection. An allowed dry-run can retry at most three fresh observations only when API Invalid plus unchanged UID/spec/ownership and a changed resourceVersion demonstrate a stale read; other errors and malformed successful responses fail immediately. Fresh retention and cluster identity checks must pass before `maintenance-authority.tsv` records `suite verified` and real maintenance starts. Raw responses/logs stay private. This reuses existing rights, job, scenarios, refs and budget, changes no production manifests, and remains a post-merge live gate. It audits these boundaries and does not add field-level RBAC or complete multi-user security.
