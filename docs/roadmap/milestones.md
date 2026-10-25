# Perfeng implementation milestones

## TL;DR

- This is the single checked-in roadmap for the Perfeng repositories and their
  shared `k8s-infrastructure` substrate.
- GitHub owns live issue and milestone assignment; this document owns intended
  outcomes, dependencies and sequencing.
- Published milestone numbers and titles are not silently renamed or reused.
- M01 — Local Performance Decision Loop established the polyrepo foundation and
  local evidence path.
- M02 — Declarative Local Operations completed the reversible migration of local
  workload composition to Argo CD.
- M03 is deferred pending a suitable laptop; M04 is in progress and M05 is planned. M06 remains deliberately deferred until a real
  non-local deployment is required.

## Ownership and update rules

This roadmap spans `perfeng-contracts`, `perfeng-control-plane`,
`perfeng-analysis`, `perfeng-k6`, `perfeng-playwright`, `perfeng-k8s` and
`perfeng-environment`, plus the project-neutral `k8s-infrastructure` repository
during extraction and integration. Component repositories link here and keep
only their local implementation and operating instructions.

- GitHub is authoritative for issue state, assignee, labels and milestone
  membership.
- This file is authoritative for planned outcome, dependency and repository
  participation.
- Use exact published titles in issues and pull requests.
- Do not renumber a published milestone.
- Update this file in the same planning change that moves a dependency or
  changes a milestone outcome.
- Split work that contains independently valuable or risky outcomes before
  implementation.

## Milestone index

| Milestone | Status | Outcome | Repositories |
|:--|:--|:--|:--|
| M01 — Local Performance Decision Loop | Complete | Execute k6, retain artifacts, normalize results, evaluate policies and baselines, and recover the control plane locally. | All current `perfeng-*` repositories |
| M02 — Declarative Local Operations | Complete | Operate local workloads through reversible, observable GitOps ownership while preserving state and explicit recovery boundaries. | `perfeng-environment`, `perfeng-k8s`, `perfeng-control-plane` |
| M03 — Native Browser Decision Loop | Deferred | Schedule and observe Playwright runs on qualified Windows workers through the control plane. | `perfeng-playwright`, `perfeng-control-plane`, `perfeng-environment`, `perfeng-analysis`, `perfeng-contracts` |
| M04 — Kubernetes Benchmark Decision Loop | In progress | Run kube-burner benchmarks through control-plane execution, analysis, baseline and reporting. | `perfeng-k8s`, `perfeng-control-plane`, `perfeng-environment`, `perfeng-analysis`, `perfeng-contracts` |
| M05 — Operator Visibility and Diagnostics | Planned | Make runs, failures, decisions and correlated telemetry understandable without inspecting individual Pods or scripts. | Primarily `perfeng-control-plane`, `perfeng-environment`, `perfeng-analysis` |
| M06 — Production Readiness | Deferred | Provide a secure, recoverable, upgradeable multi-user deployment when a non-local environment becomes an actual requirement. | All affected `perfeng-*` repositories and `k8s-infrastructure` |

## M01 — Local Performance Decision Loop

Delivered capabilities include:

- versioned contracts and repository boundaries;
- Kubernetes k6 execution with immutable runner selection;
- raw artifact publication to retained S3-compatible storage;
- normalized statistical analysis and versioned baselines;
- control-plane run, artifact, reconciliation and report workflows;
- native Windows Playwright capture and diagnostic evidence;
- local PostgreSQL, SeaweedFS, deployment locks and end-to-end smoke checks;
- cross-platform validation and release-image verification; and
- a complete local k6 path through the deployed control plane.

This milestone remains historical. Corrections to its documentation do not move
new feature work back into M01.

## M02 — Declarative Local Operations

**Outcome:** Operate the local platform through reviewed declarative state with
explicit ownership, drift visibility, persistence evidence and reversible
migration.

Completed or active slices:

- centralize the cross-repository roadmap and establish the hardened
  contributor and engineering harness in this repository before adapting it to
  the component repositories;
- select Argo CD through an accepted architecture decision;
- bootstrap a pinned local Argo CD control plane and private-repository access;
- manage the sample SUT and control plane through child Applications;
- improve bounded reconciliation diagnostics and troubleshooting;
- adopt PostgreSQL without replacing state, prove rollback and re-adoption, and
  enable controlled self-healing;
- adopt SeaweedFS without replacing state, prove restart and rollback, re-adopt
  it and enable controlled self-healing;
- retire the superseded sample-SUT Helm path while preserving preview-first
  commands that remain bootstrap, credential, migration or explicit rollback
  boundaries;
- separate stateful credential and bucket bootstrap from retained Helm rollback
  commands so a fresh cluster can enter GitOps ownership without first deploying
  PostgreSQL or SeaweedFS imperatively;
- expose the reviewed bootstrap, status, smoke and destructive teardown
  boundaries through a thin cross-platform Taskfile without duplicating the
  Python safety and diagnostic implementation;
- retire the superseded control-plane Helm reconciler while preserving its
  GitOps-independent workload health and product acceptance checks; and
- retire the redundant GitOps deploy action, audit the retained imperative
  boundaries and record successful completion against ADR 0001.

Post-milestone decisions and follow-up:

- [ADR 0004](../decisions/0004-separate-kubernetes-infrastructure.md)
  selects a project-neutral Kubernetes infrastructure repository and retains
  Perfeng composition, roadmap and acceptance ownership here; extraction is
  follow-up work and does not reopen M02;
- [ADR 0005](../decisions/0005-adopt-k8s-infrastructure-name.md) records the
  created `k8s-infrastructure` repository name, adds it to source-workspace
  composition and keeps workspace membership separate from GitOps deployment
  authorization;
- import the local-kind and Argo CD implementation histories into
  `k8s-infrastructure`, harden and pin their executable lifecycle, then cut the
  environment operator tasks over to that immutable provider while preserving
  consumer composition and an explicit rollback boundary; the cutover passed
  live health, stateful-identity and smoke acceptance, and the superseded
  lifecycle source was subsequently removed;
- define the supported production-composition boundary rather than implying
  that the local kind topology is production; and
- reconcile GitHub milestone status with this roadmap.

Completion means a contributor can bootstrap, inspect, recover and validate the
supported local platform from reviewed declarative state without competing
reconcilers or undocumented data risk. That outcome is complete for the supported
local environment; see the
[migration completion record](../gitops-migration-completion.md).

## M03 — Native Browser Decision Loop

**Status:** Deferred at the owner's request pending a suitable laptop. Retain
the implemented source, connectivity acceptance and rejected qualification
reports. Resume its existing promotion and live acceptance gates once a host
qualifies; do not relax Windows/cohort requirements to declare completion.

Planned outcomes:

- register and authenticate native Windows workers;
- advertise qualification and execution capabilities;
- schedule, lease, time out, cancel and recover native runs;
- publish artifacts without shared local-filesystem assumptions;
- analyze browser results and compare approved baselines; and
- prove the complete path on a representative Windows laptop.

The browser capture, CDP diagnostics and environment qualification created in
M01 are foundations, not evidence that this control-plane loop is complete.

### Six completion batches

The remaining work is organized by substantial outcomes rather than individual
files or layers. Known-attempt inspection is already implemented. The separate
HTTPS upload-signing origin is a completed control-plane prerequisite.

1. `perfeng-k8s`: publish the complete immutable native Windows runtime bundle,
   including source, qualification, recipe, policy and analysis/report selection.
   Real qualification evidence and candidate identities remain promotion gates.
2. `perfeng-environment`: establish trusted local API/S3 connectivity and the
   controlled target, then select the reviewed releases and worker identity.
   The opt-in loopback gateway and its checks are prepared; native bundle/image
   selection and signed PUT/internal read-back acceptance are not yet complete.
   Connectivity was advanced ahead of bundle promotion to obtain real inputs.
3. `perfeng-contracts`: define worker-scoped discovery for a lost acquisition
   response before the attempt ID is known, without restoring lease authority.
   Source complete in `143daa5`; bundle 0.21.0 / Native Worker API 2.2.0.
4. `perfeng-control-plane`: implement discovery with durable worker-scoped,
   expiry and concurrency semantics.
   Source complete in `365ecb8`; a verified Linux AMD64 image is published,
   but this environment still selects the previous service and runtime bundle.
5. `perfeng-playwright`: complete explicit manual recovery using authoritative
   server state and retained local evidence, with no inferred lease authority.
   Source complete in `c918cdb`; functional Windows checks passed. Real
   Windows/control-plane recovery acceptance remains part of batch 6.
6. `perfeng-environment`: prove qualification through approved-baseline decision
   on a representative Windows host, including cancellation and restart recovery.

Batch 2's controlled target has published [immutable candidate packaging](../native-browser-candidate.md)
and [image-backed GitOps selection/gateway cutover](../local-native-target.md),
pinning chart revision `a20633e` and the candidate digest. Live target/HTTPS
acceptance and native runtime selection remain promotion gates. The earlier host report is REJECTED against
`windows-mainstream/1.0.0`. The separately identified
`windows-four-core-4k/1.0.0` profile now represents the available laptop's
CPU/display conditions while retaining OS and calibration gates; a fresh
accepted live capture is still required. Do not treat a qualification-report
hash as a browser-environment artifact.

Managed native connectivity is prepared under [ADR 0010](../decisions/0010-manage-native-connectivity-workflow.md):
setup, independent doctor checks and one supervised foreground session replace
manual Go builds and four terminals. First serving release publication/selection
and live acceptance remain explicit gates; this workflow does not promote the
native runtime or accept a rejected host report.

M03 remains incomplete until live acceptance passes. An operator-started
`run-once` worker and safe manual recovery are the intended scope; an unattended
Windows service is not required. Do not count mocks or readiness as completion.

## M04 — Kubernetes Benchmark Decision Loop

**Status:** In progress, independently of deferred M03.

The agreed sequence is nine coherent repository-sized slices:

1. `perfeng-contracts`: Kubernetes admission/evidence semantics (bundle 0.22.0).
2. `perfeng-environment`: read-only local integration qualification and immutable receipts.
3. `perfeng-k8s`: contained kube-burner execution and verified publication.
4. `perfeng-control-plane`: scoped dispatch with durable exclusive target reservations.
5. `perfeng-analysis`: OCI provenance adoption and complete decision fixture coverage.
6. `perfeng-control-plane`: owned workload cancellation, cleanup and restart recovery.
7. `perfeng-k8s`: complete immutable Kubernetes runtime bundle promotion — merged.
8. `perfeng-environment`: reviewed runtime selection and GitOps deployment — source merged;
   [explicit maintenance cutover and live acceptance](../kubernetes-runtime-deployment.md) pending.
9. `perfeng-environment`: [automated decision-loop/cancellation/recovery acceptance](../kubernetes-benchmark-acceptance.md)
   — source implemented; live acceptance and reviewed success evidence remain pending.

After contracts, qualification, execution packaging and analysis can proceed
in parallel. Promotion waits for their integration and safe lifecycle support.
Reuse existing dispatch/collection/normalization/reporting; do not duplicate them.
The first target is bounded warm-image pod startup, not production capacity
testing or cluster/CNI-upgrade comparison. The local cohort is integration-only.
Missing metrics remain UNKNOWN and do not justify automatic telemetry installation.
See [qualification](../kubernetes-benchmark-qualification.md).

Promotion prerequisite: the merged infrastructure package provides verified-TLS resource
metrics and explicit certificate setup. The [environment selection](../local-resource-metrics.md)
adds a separate restricted GitOps Project and pinned provider health. Live reconciliation,
fresh node usage and a new all-PASS qualification remain acceptance gates before slice 7;
this is not broad M05 telemetry or completion of the runtime decision loop.

Planned outcomes:

- schedule and execute reviewed kube-burner definitions through the control
  plane;
- qualify the target cluster and environment;
- retain resource-pressure and interference evidence;
- select baselines and produce regression decisions; and
- prove reporting and restart recovery end to end.

## M05 — Operator Visibility and Diagnostics

Planned outcomes:

- expose a run timeline and stage status;
- retain actionable failure and diagnostic evidence;
- add Prometheus service metrics and useful dashboards;
- correlate API, reconciliation, runner, storage and analysis activity;
- add operational alerts and maintained runbooks; and
- introduce a CLI or minimal UI only from demonstrated operator needs.

Minimal service metrics may be added earlier when required to operate a
deployment. Broad correlation remains here so observability does not block the
first working decision loops.

## M06 — Production Readiness

This remains deferred until a real non-local deployment is required. Its scope
includes:

- SSO, authorization and service identity;
- external secret management and credential rotation;
- network policy and workload isolation;
- PostgreSQL and object-storage backup and restore;
- high availability and capacity planning;
- artifact retention and deletion policy;
- supply-chain and artifact verification;
- environment promotion, rollback and upgrades; and
- disaster-recovery exercises.

Preparatory release-architecture work can proceed before non-local deployment
is required; it does not mark M06 active or production-ready. [ADR 0014](../decisions/0014-separate-release-and-run-orchestration.md)
assigns finite release operations to Argo Workflows, keeps Argo CD as the sole
workload reconciler, and leaves performance Runs with the control plane. The
[deployment-script baseline](../deployment-script-retirement.md) measures the
local `bootstrap` and M04 `kubernetes:prepare` Python surface and sets a
greater-than-80% retirement gate. The [v1 release request](../release-request.md)
defines the immutable input and promotion precondition. Sequence the remaining
implementation as a pinned disposable-kind workflow pilot, external-state
and health parity, then separately proven M04 maintenance/recovery before any
cutover. Tekton or Dagger remains an upstream artifact-producer choice behind
the same input contract. The current M04 manual sync and rollback boundary
remains in force throughout this preparatory work.

The [read-only preflight pilot](../release-preflight-workflow.md) stages the
first WorkflowTemplate and narrow RBAC separately from the active local root.
Its offline checks do not satisfy the disposable-kind two-run gate or establish
a promotion receipt.

The [staged readiness gate](../release-readiness.md) adds native rollout waits,
selected control-plane identity checks and bounded operational endpoint probes.
Its versioned evidence covers control-plane/foundation readiness only; complete
platform health, live parity, M04 maintenance/recovery and script retirement
remain separate gates.

The [staged M04 maintenance checkpoint](../release-maintenance-checkpoint.md)
observes an already paused current selection and records bounded quiescence
evidence. It does not automate pause, migration or recovery, establish a
promotion receipt, or replace the independent live check before mutation.
Prior-release receipt/adoption, maintenance writes, complete health parity and
disposable-kind recovery acceptance still precede operational cutover.

The [staged adoption ledger](../release-adoption.md) records an explicitly
reviewed existing baseline in immutable ConfigMaps and creates its initial
current pointer without overwrite. It does not advance a pointer, execute a
promotion or establish full platform acceptance. Guarded advancement,
maintenance writes, live recovery/export and measured retirement remain open.

The staged [v2 verifier package](../release-verifier-package.md) consolidates
preflight, selection, readiness, quiescence and adoption into two modules
(708 physical lines total). Hash-audited source retirement removes superseded
helper copies from the active tree without pruning installed resources. This
is a prerequisite budget correction, not operational cutover or retirement
credit against the original 13 host entrypoints. Pointer advancement and the
remaining live acceptance/recovery gates are still open.

The [disposable-kind integration gate](../release-workflows-integration.md)
exercises the unchanged maintenance child with actual Argo execution, package
mounts and API permissions. Synthetic preflight/Application fixtures make this
an installed-runtime integration tier, not full release acceptance or evidence
that a retained environment may switch to the staged workflow.

The same gate also exercises native adoption-ledger reads, immutable writes,
competing create conflicts and retention after fixture Workflow deletion. It
uses generated schema-valid bytes rather than an approved Git request or full
adoption-parent execution. Promotion guards, ambiguous-outcome recovery and
operational adoption/cutover remain separate requirements.

The CI recovery slice terminates a fixture coordinator after its confirmed
receipt write, then checks fresh native reads with the unchanged pure adoption
planner on the runner. Partial/exact retry, changed-root and missing/recreated
receipt refusals are covered with synthetic readiness/root status. This does
not establish full-parent recovery or an ambiguous API-response outcome;
those gates still precede operational use.

The same isolated CI tier adds native database-role child parity against
transient PostgreSQL: first/repeat creation and privilege/login verification,
stable role/Secret identity, retained Jobs, unsafe-role/password refusal and
fresh execution after explicit fixture correction. It does not run the complete
external-state parent or bucket/registry preparation, apply schema migrations,
prove persistent-storage recovery or retire host scripts.

Native bucket-child parity adds first/repeat preparation against authenticated
transient SeaweedFS, preserved bucket/object/Secret state and denied-credential
refusal after explicit 404/403 probes. Only the fixture administrator corrects
its deliberately wrong initial credential. Direct internal S3/emptyDir does not
prove production proxy/storage behavior. Registry and full-parent release
acceptance, migration and operational cutover remain separate gates.

The registry CI slice uses the owner-approved ephemeral repository identity
and package-read permission ([ADR 0019](../decisions/0019-use-ci-identity-for-registry-parity.md)).
It checks all three manifest digests on first/repeat/corrected native Jobs and
an invalid-credential refusal, retaining Job and Secret evidence. Package owners
must grant repository Actions Read access for private packages. This does not
prove layer downloads, full manifest-byte hashing, parent release execution or
retirement of the host verifier.

Readiness-child runtime/HTTP parity uses the unchanged verifier with an actual
pinned Git artifact and the mounted verifier package, synthetic workload and
preflight observations, and an isolated HTTP peer on the internal Service.
First/repeat/corrected results, policy refusal before probes, non-200/redirect/
invalid-metrics refusal and restricted authority are checked in disposable kind.
This does not establish native resource-observer or full readiness-parent
execution, actual control-plane health, promotion or host-script retirement.

Native readiness-observer parity adds real Deployment/StatefulSet controller
state for synthetic sleepers, a fixture Service and explicitly administered
Application status. The unchanged Argo resource children collect first/repeat
projections, reject degraded status and revoked named-read access, recover after
fixture correction and complete a pending Deployment wait after explicit
scale-up. API snapshots and retained UIDs bound this installed-observer proof.
Actual workload health, Argo CD reconciliation, full reviewed-request parent
acceptance, promotion and host-script retirement remain open.

Git/schema/preflight-identity parity uses an actual committed CI request with
an all-zero fixture UID and a separately pinned reviewed Git selection. The
unchanged child templates fetch both revisions, validate the schema and lock
bytes, record repeat/corrected synthetic-UID evidence and reject bad digest/path
parameters. A native namespace read is checked separately; the complete
production preflight must reject the same request for wrong cluster UID.

The next full-preflight gate is implemented by a separate trusted-main CI
workflow ([ADR 0020](../decisions/0020-publish-disposable-ci-release-requests.md)).
It publishes a run-owned request after kind assigns the actual UID, executes
the unchanged parent twice and conditionally deletes its exact temporary ref.
PR checks stay read-only. Live acceptance is pending the post-merge main run;
full release composition, promotion/recovery and script retirement remain open.
Admission of an operator-reviewed request for a retained environment, full parent
composition, promotion/recovery and host-script retirement remain separate gates.

The next composition slice extends the same trusted-main job with the unchanged
complete maintenance-checkpoint parent and its real-UID Git request. First/repeat
checkpoint identity/version checks, preflight refusal before observation,
unpaused/revoked-read refusals and fresh correction are required. This phase
adds no production template or authorization changes and is pending post-merge
main acceptance. Applications remain fixtures; actual maintenance writes,
full readiness/external-state/adoption composition, recovery/export, promotion
and measured host-script retirement remain open.

Complete external-state composition is the next trusted-main slice: the same
real-UID request must pass preflight/selection and bucket/database/registry Jobs.
First/repeat, zero-Job bad-digest refusal, denied-bucket and unsafe-role stopping
boundaries, fresh correction and retained provider/credential identities are
required. The owner approved package-read access in the existing write-capable
main job ([ADR 0021](../decisions/0021-authorize-trusted-main-external-state-parity.md)).
Live acceptance is pending its post-merge run. Providers remain transient;
real readiness/adoption, migrations, promotion/recovery/export, full registry
byte verification and the original 13-entrypoint retirement gate remain open.

Selected migration-image parity is the next prerequisite for real readiness:
the trusted-main fixture runs the actual pinned `/usr/local/bin/migrate` after
fresh complete maintenance checkpoints. Runtime-role CREATE denial, the first
eighteen exact source hashes and repeat ledger/OID/sentinel preservation are
required on the initially empty disposable database. Post-merge live acceptance
is pending. This introduces no production migration template and changes no
supported host path. Populated upgrade/backup/recovery, runtime grants/server
wiring, full readiness/adoption, promotion and measured retirement remain open.

Runtime database grant parity follows the selected-image migration in the same
trusted-main fixture. It applies the exact existing grant transaction and runs
runtime-only PostgreSQL client Jobs for missing grants, first/repeat application,
excess DELETE refusal and correction. Exact table privileges, actual allowed
reads/zero-row writes and denied DDL/DELETE/ledger access are checked while
preserving data, schema/ledger/OIDs and credentials. Post-merge live acceptance
is pending. No production grant owner, template or CI permission changes; actual
server/chart/runtime-package wiring and complete readiness/adoption remain open,
alongside populated upgrades/recovery, promotion and measured script retirement.

Selected server parity follows runtime grants in trusted-main CI. Render the
actual pinned Helm chart and immutable Kustomize package using the owner-approved
private checkout ([ADR 0022](../decisions/0022-use-pinned-source-for-server-parity.md)).
Require original native Deployment/Service observations and production HTTP
probes on the actual image, first/repeat success, stopped-server refusal and
fresh correction with retained Deployment/Service/credential identities. Live
acceptance is pending its post-merge main run. Static operational endpoints do
not establish dependency recovery. Complete readiness/adoption, real Argo CD
reconciliation, foundation storage proof, populated upgrades/recovery, promotion
and measured retirement remain open.

PostgreSQL chart/PVC parity replaces the main fixture's standalone database
provider with the actual repository chart, before migration and server phases.
Native observation, authenticated TCP client checks and a controller-driven Pod
restart must preserve PVC/PV, canary, Service and Secret identities while advancing
the StatefulSet generation/revision. The canary is outside the application DB.
Post-merge live acceptance remains pending; this proves local Pod-replacement
retention only. SeaweedFS chart/storage, real Argo CD convergence, full readiness/
adoption, retained-environment recovery, promotion and measured retirement remain
open. PR child parity and production manifests/permissions remain unchanged.

SeaweedFS chart/proxy/PVC parity replaces the main direct-S3 provider with the
actual repository chart and independent immutable server-auth Secret. The
unchanged external-state parent uses the real proxy, while credential-free peer
Jobs require reachable 8333 and exact refusal on private HTTP/gRPC listeners.
A native restart must retain sentinel metadata/bytes, PVC/PV, Service, proxy
configuration and credentials, with the restarted provider UID retained through
selected-server acceptance. Live acceptance is pending the post-merge main run.
PR child fixtures and production charts/templates remain unchanged. Production
S3 IAM policy, real Argo CD convergence, complete readiness/adoption, backup/
restore and host-loss recovery, promotion and measured retirement remain open.

Actual Argo CD acceptance adds the pinned infrastructure bootstrap after chart/
server parity. ADR 0023 records owner-approved source-token exposure to its pinned
checkout and an exact-URL repository Secret in disposable trusted-main CI. The
root is skipped; unchanged selected control-plane sources are manually synced.
Fresh operations and native observers must prove first/repeat convergence, real
Service drift and explicit correction while preserving identities/data. Live
acceptance awaits the post-merge main run. Complete readiness/adoption, foundation
Application adoption, production security/recovery and script retirement remain
open; no production verifier/template or host entrypoint changes.

Complete readiness-parent acceptance now composes the unchanged production parent
after real Argo CD, actual foundation charts and selected-server prerequisites.
The same pinned Git request must produce schema-valid first/repeat/corrected
results bound to nested preflight and API identities/generations. Wrong-digest
and revoked Application-read cases must stop at their owning nodes, with no
readiness result or downstream verification. Existing grants/credentials remain
unchanged; live acceptance awaits the post-merge trusted-main run. Adoption,
promotion/recovery, root/foundation activation, product qualification and measured
script retirement remain separate; production templates/verifiers are unchanged.

PostgreSQL Application acceptance follows complete readiness, with ADR 0024's
owner-approved exact environment repository configuration using the existing
read token. A CI-only pinned/manual Application must converge first/repeat,
observe real Service-name drift and explicitly correct it while preserving
database, credential and storage identities/data. Native StatefulSet and TCP
checks remain required; live acceptance awaits the post-merge trusted-main run.
Root and SeaweedFS Application adoption, complete release adoption/promotion,
recovery/security and measured script retirement remain separate. Production
Application/chart/template/verifier and operator paths remain unchanged.

SeaweedFS Application acceptance follows PostgreSQL using the already approved
environment repository. The shared foundation validator pins the CI-only manual
Application; first/repeat/corrected controller and native observations must
preserve proxy/configuration/credential/storage identities and S3 sentinel
metadata/bytes. Credential-free peers must still reach 8333 and refuse private
listeners, and deliberate Service-name drift requires explicit correction.
PostgreSQL adoption/data and other Application specs remain unchanged; root stays
skipped. Live acceptance awaits the post-merge trusted-main run. Root activation,
complete release adoption/promotion, production IAM/recovery/security and
measured script retirement remain separate; production manifests/verifiers and
operator paths are unchanged.

Bounded root acceptance follows both foundation Application phases. ADR 0025
records owner approval to reconcile the actual fourteen-object root tree while
keeping root automatic sync disabled and all six non-control-plane children
skipped. Explicit selected-commit first/repeat/corrected operations must prove
root convergence and Namespace-label drift correction without workload, storage,
credential or data changes. Root retains its canonical `main` source; a moving
selection fails. Live proof awaits the post-merge trusted-main run. Full-tree
health/activation, complete adoption/promotion, production recovery/security and
measured host-script retirement remain separate; no published contract changes.

Complete adoption-parent acceptance follows bounded root convergence. The
unchanged parent must execute fresh nested readiness, native ledger reads/creates
and all three planner phases using the existing pinned CI request and grants.
First/exact-repeat/corrected submissions must bind stored bytes, provenance and
receipt/pointer UIDs; a root-read refusal after successful readiness must leave
records unchanged. Deleting only the original disposable Workflow must preserve
the ledger. Workload/data retention and all six child skips remain required.
Live proof awaits the post-merge trusted-main run. Complete-parent recovery,
promotion/advancement, retained adoption, full-tree activation, production
admission/export/restore and measured script retirement remain separate.

Root-state refusal coverage extends complete adoption acceptance: deliberate
root-owned Namespace-label drift must stop a fresh parent at `plan` after
successful readiness and native reads, with no downstream writes or result.
Only explicit selected-commit root reconciliation and a fresh parent can restore
success; ledger bytes/UIDs/provenance and workload/data identities remain intact.
Live proof awaits the post-merge trusted-main run. This does not advance the
separate partial-write, concurrent or ambiguous-response recovery gates.

Concurrent exact-retry acceptance queues two unchanged complete parents behind
a bounded native suspended holder of the existing release mutex. Native wait
status, absence of worker Pods/results and unchanged state must precede release;
both queued UIDs must then complete with disjoint controller entrypoint intervals,
fresh verification and exact ledger/result reuse. Live proof awaits the post-merge
trusted-main run. Initial-create races, partial-write/ambiguous-response recovery,
external-operator exclusion and production locking remain separate.

ADR 0026 records owner approval for two isolated trusted-main scenarios. Initial
keeps successful first-adoption coverage; receipt-only uses a native quota to
allow receipt creation but reject the pointer. A guarded quota increase and fresh
unchanged parent must preserve the failed origin's receipt and create only the
pointer, then pass all existing cases and retention checks. Refs/artifacts are
scenario-specific and credential rights unchanged. Live proof awaits both
post-merge legs; racing initial creates, ambiguous responses, promotion and
production recovery/cutover/retirement remain separate.

ADR 0027 adds owner-approved missing/replaced receipt refusal acceptance to both
existing disposable scenarios after retention proof. Only the accepted receipt
is conditionally deleted with UID/resourceVersion guards and recreated with
original bytes/provenance but a new UID. Fresh complete parents must refuse at
the planner after successful readiness, preserving the original pointer and
workload/data identities without downstream writes or adoption results. Live
proof awaits both post-merge legs. This is deliberate fault injection, not
ledger repair, restoration or a retained-environment recovery authorization.

ADR 0028 adds two approved request fixtures in each existing owned commit/ref:
equivalent JSON with different bytes and an upgrade envelope naming the accepted
digest. Fresh complete parents must pass real preflight/readiness then refuse
at plan without writes/results, preserving original ledger/workload/data state
and absent candidate receipts. Existing retention and destructive tests continue.
Live proof awaits both post-merge scenarios; this does not implement promotion,
add credential rights or advance script retirement.

ADR 0030 adds the approved native-ledger response-loss prerequisite in the existing
disposable boundary job: a TLS proxy commits one exact receipt then drops its
successful response before headers. Failed native execution, fresh direct reads,
unchanged create conflict, pure-planner partial recovery and completed retention
must preserve the original bytes/UID/provenance. Coordinator interruption uses an
independent receipt without resetting history. Live proof awaits PR CI; production
templates/grants/jobs/refs are unchanged. Complete-parent ambiguous outcomes,
promotion, retained recovery and measured retirement remain separate.

The next adoption acceptance slice replaces review-only negative permission
checks with eleven actual API `Forbidden` checks in the existing native-ledger
and trusted-main fixtures. All writes use server dry-run with explicit
ServiceAccount groups; selected receipt/pointer snapshots must stay identical.
Pod exec remains review-only. This adds no grants, credentials, jobs, scenarios
or refs. Offline protocol tests do not establish live success; CI proof and
production token authentication remain separate. No retirement credit is added.

ADR 0031 adds the approved third trusted-main `response-loss` scenario. A generated
complete-parent fixture changes only the pre-receipt suspension and receipt
transport; real readiness/planning and an exact committed-response event must
precede failed execution with no pointer/result. A fresh unchanged production
parent must recover by creating only the missing pointer while retaining receipt
bytes/UID/provenance, then pass all existing checks. Credential rights and
production templates remain unchanged. Live proof awaits post-merge trusted-main
CI; pointer response loss, initial-parent races, promotion, retained recovery and
measured script retirement remain separate.

ADR 0032 adds the approved fourth `pointer-response-loss` scenario. Preserve real
receipt creation/readback and pointer planning, fault only the exact pointer
create response, and require a fresh unchanged parent to skip both creates while
preserving both full records/UIDs/provenance. Existing refusal/concurrency/
retention gates continue; production templates and rights remain unchanged.
Full-parent live proof awaits the post-merge main-only leg. Initial-parent races,
promotion, retained recovery and measured retirement remain separate.

ADR 0033 adds the approved fifth `receipt-create-conflict` scenario: after real
readiness/planning, a fixture writer creates the exact receipt before resuming the
unchanged native create. Require actual AlreadyExists, preserved winner identity
and a fresh unchanged parent creating only the pointer. No proxy, new grants or
production template changes are introduced. Live proof awaits post-merge main
acceptance and its migrator prerequisite. Pointer conflicts, promotion, retained
recovery, operational cutover and measured retirement remain separate.

Before adding more dependent adoption scenarios, the next prerequisite slice
retains sanitized migrator startup observations through native Job deadline
cleanup. The previous completed main run lost the Pod before inspection and
never reached adoption. The bounded observer preserves native deadlines and
acceptance rules; it diagnoses startup and does not claim a timeout root cause,
live qualification, promotion or retirement credit.

Early trusted-main prerequisites now separate authenticated runner image access
from actual kind kubelet credential delivery before controllers/database setup.
Both use the same generated pull-auth payload and the locked image; failures
provide safe category-specific remediation. The owner reports package Admin
Actions access; its subsequent initial-leg rerun passed migration parity and
failed later at the runtime SQL connection boundary. No further role escalation
or credential substitution is introduced. New prerequisite live evidence remains post-merge; image pull does not qualify
migration/adoption or advance script retirement.

The post-PR-245 main run exposed a prerequisite observer race before SQL:
value-less kubectl 1.35 JSONPath wait accepted an existing empty image ID.
The next corrective slice filters empty IDs inside the native wait, retains
independent fresh-read refusal and adds real-client watch regression coverage.
No grant, deadline, scenario or production-template changes are included.
Live main-only SQL/adoption acceptance and measured retirement remain open.

Run 40191776524 subsequently passed image pulls, migration, SQL grants and server
parity, then exposed the shared S3 observer's 900-second expiry during later
retention checks. The owner approved a 60-minute budget for the existing five
jobs and the same bounded observer lifetime. This corrective slice also retains
allowlisted Argo CD bootstrap status and fixed categories from bounded private
logs; three Redis rollout timeouts remain unproven. Readiness deadlines, grants,
jobs/scenarios/refs and production templates are unchanged. Full post-merge
adoption acceptance, promotion and measured retirement remain open.

ADR 0035 adds the owner-approved sixth `pointer-create-conflict` leg. Preserve
real receipt creation/readback and pointer planning, suspend only before native
pointer creation, then let the fixture writer create exactly the planned pointer.
Require native AlreadyExists refusal and an unchanged-parent retry that skips
both creates and preserves both complete records. Both conflict modes capture
fresh API state before verification. Credential rights, production templates,
proxy scope and per-job deadlines remain unchanged. Full live proof remains
post-merge; promotion, retained recovery and measured retirement stay open.

Cloud deployment is included only when it becomes an explicit requirement.
The [cloud Kubernetes evaluation](../cloud-kubernetes-options.md) records the
current zero-cost options. OCI OKE is the first candidate, but a cloud pilot is
blocked until the runtime has reviewed `linux/arm64` releases and the provider
capacity and zero-cost preflight pass. A free integration lab is not evidence of
production readiness.

## Planning review

Review this roadmap when a milestone completes, a repository boundary changes,
or priorities are deliberately reordered. Record durable topology or ownership
changes in an ADR. A milestone may defer work only when its outcome still holds
and the deferral is explicit.
