# Engineering standards

## TL;DR

- Preserve environment, repository and resource ownership boundaries.
- Keep validation offline and deterministic; make live mutation explicit,
  identity-checked and bounded.
- Prefer small cohesive Python modules, typed boundaries and precise failures.
- Treat manifests, locks and published configuration as compatibility contracts.
- Test observable behavior, including ownership conflicts and partial failure.
- Document invariants, side effects, healthy signals, rollback and recovery.
- Pin dependencies and run the same checked-in checks locally and in CI.

## Policy strength

Normative terms use the meanings defined in
[CONTRIBUTING.md](../../CONTRIBUTING.md#policy-language-and-sources-of-truth).
MUST and MUST NOT are reviewable requirements; SHOULD and SHOULD NOT are strong
defaults that require a recorded reason to deviate; MAY is optional. Other
imperative or advisory wording explains recommended practice and becomes
mandatory only when an issue, ADR or checked-in tool makes it explicit.

## Purpose and applicability

These standards apply to Python scripts, tests, Helm charts, Kustomize and Argo
CD resources, workspace metadata, lock files and operational documentation.
They supplement accepted ADRs and focused operational guides.

The goal is maintainable correctness, not ceremonial compliance. A deviation
from a strong default MAY be accepted when its trade-off is explicit and
tested. A durable change to security, state ownership, topology or
compatibility requires an ADR.

## Architecture and ownership

Organize code around environment capabilities such as workspace composition,
cluster identity, object storage, database provisioning, GitOps reconciliation
and acceptance evidence. A module should have one cohesive operational purpose.

- Scripts coordinate environment operations; they do not absorb component
  business logic.
- Charts package workloads; environment configuration selects reviewed releases
  and supplies environment-specific values.
- `perfeng-contracts` owns cross-repository schemas and compatibility meaning.
- Component repositories own executable behavior and their release artifacts.
- External Secrets, retained volumes, Helm releases and Argo CD Applications
  have explicit, non-overlapping owners.

Use SOLID and patterns as reasoning tools, not quotas. Prefer composition,
precise names, consumer-owned interfaces and direct code over generic managers,
helpers or speculative frameworks. Add an abstraction only when a demonstrated
variation, ownership boundary or test seam requires it.

## Python

- Use the Python version declared by `pyproject.toml` and dependencies resolved
  by `uv.lock`.
- Ruff formatting is authoritative. Do not hand-format against it.
- Type public functions and operational boundaries. Normalize YAML, JSON,
  subprocess output and environment values before core logic uses them.
- Treat `Any` as an isolated escape hatch, not a propagated default.
- Prefer `pathlib`, context managers and standard-library facilities before new
  dependencies.
- Keep import-time behavior side-effect-free. Perform filesystem, process,
  network and cluster access only from explicit execution paths.
- Use precise exceptions and preserve causes. Do not catch `Exception` merely
  to discard the original classification.
- Use small immutable values or dataclasses when identity and invariants deserve
  a type. Do not add an abstract base class by default.
- Inject or isolate clocks, identifiers, randomness, filesystem roots and
  process execution when determinism depends on them.
- Docstrings explain public purpose, contracts, errors, units and non-obvious
  side effects without restating the signature.

## Commands and external processes

- Build subprocess arguments as sequences, never interpolated shell strings.
- Never place credentials in command arguments or diagnostic output.
- Capture text as UTF-8 with deliberate error behavior; do not depend on the
  host code page.
- Bound commands by deadlines and emit progress for operations that can take
  longer than the normal feedback interval.
- Preserve command exit status and include a safe, actionable failure boundary.
- Redact credentials and bound external error text before showing it.
- Preview mode performs no network, process mutation, credential generation or
  local-state writes.

## Kubernetes, Helm and GitOps

- Use `.local/perfeng-local.kubeconfig` and `kind-perfeng-local`; refuse
  redirected state or a foreign cluster.
- Verify the local Docker endpoint and named kind cluster before live mutation.
- Use exact namespaces and owned labels. Refuse to adopt resources with an
  unexpected owner or identity.
- Set explicit timeouts for rollouts, Jobs, waits and API operations.
- Keep readiness checks distinct from persistence, authorization and semantic
  health checks.
- Render and validate charts and Kustomize offline before cluster access.
- Preserve StatefulSet names, selectors, Services, claim templates, PVC/PV
  bindings and external Secret identities during ownership changes.
- Never run Helm and Argo CD as concurrent reconcilers for the same workload.
- Automated pruning is disabled until a separately reviewed retention and
  recovery policy proves it safe.
- Self-healing is enabled one workload at a time after drift and rollback
  acceptance.

## Configuration, locks and compatibility

Treat CLI behavior, workspace manifests, deployment locks, Helm values,
Kubernetes resource identities, object keys and receipt formats as compatibility
boundaries.

- Validate exact structure and reject unknown or ambiguous fields where they
  could change behavior.
- Pin immutable source revisions, image digests and content hashes when an
  execution must be reproducible.
- A branch may be a workspace-development default; it is not an artifact or
  production deployment pin.
- Additive changes define defaults and old-reader behavior. Breaking changes
  need a versioned transition or explicit migration plan.
- Content-addressed resources compare exact canonical bytes before treating an
  identity match as an exact retry.
- Generated output is reproducible and checked in only when consumers require
  it without running the generator.

## Errors, idempotency and recovery

- Distinguish invalid input, ownership conflict, missing prerequisite,
  unavailable dependency, timeout, cancellation and internal defects.
- Do not expose secret-bearing vendor or command output as a public error.
- Define idempotency using stable identity plus immutable-content comparison.
- A partial operation leaves enough evidence for safe inspection and retry.
- Never silently rotate credentials, replace a persistent resource or delete
  retained evidence as recovery.
- Destructive actions resolve exact targets, state what is retained and require
  an explicit documented path.
- Rollback restores the prior owner or selection without pretending a data
  migration occurred.

## Testing

Test behavior at the lowest boundary that provides convincing evidence:

- pure validation and document construction tests;
- orchestration tests with mocked external process boundaries;
- offline Helm/Kustomize rendering and schema validation;
- focused live acceptance only for behavior mocks cannot establish; and
- an end-to-end smoke path for the integrated local platform.

Tests are deterministic, isolated, parallel-safe and independent of wall clock,
network, locale, host paths and execution order unless one is the subject.
Defect fixes include a regression test. Cover important rejection, ownership,
duplicate, stale revision, authorization, timeout, partial failure and recovery
paths. Never weaken assertions or lengthen timeouts without diagnosing the
cause.

## Documentation

- Public commands document purpose, prerequisites, preview behavior, mutations,
  outputs, failure classes and recovery.
- Operational guides identify healthy signals and distinguish readiness from
  durable acceptance.
- Comments explain decisions and constraints, not syntax or obvious control
  flow.
- Long documents follow the TL;DR policy in CONTRIBUTING.md.
- Operational changes update troubleshooting in the same pull request.
- Accepted ADRs are immutable historical records and are superseded by a new
  decision when necessary.

## Security and dependencies

- Deny by default and use least privilege for GitHub, GHCR, Kubernetes and S3
  identities.
- Secrets come from environment input or owned Kubernetes Secrets through
  private stdin; never from committed defaults.
- Do not log credentials, authorization headers, Secret data or sensitive
  payloads.
- Bound collection sizes, diagnostic text, retries, execution time and
  concurrency.
- A dependency requires a current need, compatible license, maintained release,
  pinned resolution and a replacement boundary proportionate to its risk.
- New operational behavior documents rollout, rollback, healthy signals and
  operator recovery.

## Required verification

Every change runs the repository checks in CONTRIBUTING.md plus focused checks
for affected manifests and charts. A live operation records only non-secret
evidence and never substitutes for deterministic tests.
