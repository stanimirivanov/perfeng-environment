# Perfeng implementation milestones

## TL;DR

- This is the single checked-in roadmap for all `perfeng-*` repositories.
- GitHub owns live issue and milestone assignment; this document owns intended
  outcomes, dependencies and sequencing.
- Published milestone numbers and titles are not silently renamed or reused.
- M01 — Local Performance Decision Loop established the polyrepo foundation and
  local evidence path.
- M02 — Declarative Local Operations is active and is replacing imperative
  workload composition incrementally through reversible GitOps changes.
- M03 through M05 are planned; M06 remains deliberately deferred until a real
  non-local deployment is required.

## Ownership and update rules

This roadmap spans `perfeng-contracts`, `perfeng-control-plane`,
`perfeng-analysis`, `perfeng-k6`, `perfeng-playwright`, `perfeng-k8s` and
`perfeng-environment`. Component repositories link here and keep only their
local implementation and operating instructions.

- GitHub is authoritative for issue state, assignee, labels and milestone
  membership.
- This file is authoritative for planned outcome, dependency and repository
  participation.
- Use exact published titles in issues and completion reports.
- Do not renumber a published milestone.
- Update this file in the same planning change that moves a dependency or
  changes a milestone outcome.
- Split work that contains independently valuable or risky outcomes before
  implementation.

## Milestone index

| Milestone | Status | Outcome | Repositories |
|:--|:--|:--|:--|
| M01 — Local Performance Decision Loop | Complete | Execute k6, retain artifacts, normalize results, evaluate policies and baselines, and recover the control plane locally. | All current `perfeng-*` repositories |
| M02 — Declarative Local Operations | In progress | Replace imperative local workload composition with reversible, observable GitOps ownership while preserving state and rollback paths. | `perfeng-environment`, `perfeng-k8s`, `perfeng-control-plane` |
| M03 — Native Browser Decision Loop | Planned | Schedule and observe Playwright runs on qualified Windows workers through the control plane. | `perfeng-playwright`, `perfeng-control-plane`, `perfeng-environment`, `perfeng-analysis`, `perfeng-contracts` |
| M04 — Kubernetes Benchmark Decision Loop | Planned | Run kube-burner benchmarks through control-plane execution, analysis, baseline and reporting. | `perfeng-k8s`, `perfeng-control-plane`, `perfeng-environment`, `perfeng-analysis`, `perfeng-contracts` |
| M05 — Operator Visibility and Diagnostics | Planned | Make runs, failures, decisions and correlated telemetry understandable without inspecting individual Pods or scripts. | Primarily `perfeng-control-plane`, `perfeng-environment`, `perfeng-analysis` |
| M06 — Production Readiness | Deferred | Provide a secure, recoverable, upgradeable multi-user deployment when a non-local environment becomes an actual requirement. | All affected `perfeng-*` repositories |

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
  it and enable controlled self-healing; and
- retire the superseded sample-SUT Helm path while preserving preview-first
  commands that remain bootstrap, credential, migration or explicit rollback
  boundaries;
- separate stateful credential and bucket bootstrap from retained Helm rollback
  commands so a fresh cluster can enter GitOps ownership without first deploying
  PostgreSQL or SeaweedFS imperatively; and
- expose the reviewed bootstrap, status, smoke and destructive teardown
  boundaries through a thin cross-platform Taskfile without duplicating the
  Python safety and diagnostic implementation; and
- retire the superseded control-plane Helm reconciler while preserving its
  GitOps-independent workload health and product acceptance checks.

Remaining completion questions:

- complete the final migration audit and declare the GitOps migration complete
  against ADR 0001 acceptance while recording the imperative boundaries retained
  for credentials, migrations, stateful rollback and product acceptance;
- decide whether reusable infrastructure becomes a separate project and what
  smaller responsibility remains in `perfeng-environment`;
- define the supported production-composition boundary rather than implying
  that the local kind topology is production; and
- reconcile GitHub milestone status with this roadmap.

Completion means a contributor can bootstrap, inspect, recover and validate the
supported local platform from reviewed declarative state without competing
reconcilers or undocumented data risk.

## M03 — Native Browser Decision Loop

Planned outcomes:

- register and authenticate native Windows workers;
- advertise qualification and execution capabilities;
- schedule, lease, time out, cancel and recover native runs;
- publish artifacts without shared local-filesystem assumptions;
- analyze browser results and compare approved baselines; and
- prove the complete path on a representative Windows laptop.

The browser capture, CDP diagnostics and environment qualification created in
M01 are foundations, not evidence that this control-plane loop is complete.

## M04 — Kubernetes Benchmark Decision Loop

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

Cloud deployment is included only when it becomes an explicit requirement.

## Planning review

Review this roadmap when a milestone completes, a repository boundary changes,
or priorities are deliberately reordered. Record durable topology or ownership
changes in an ADR. A milestone may defer work only when its outcome still holds
and the deferral is explicit.
