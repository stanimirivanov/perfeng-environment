# ADR-0012: Select verified resource metrics in a separate GitOps project

- Status: Proposed
- Date: 2026-10-15
- Milestone: M04 — Kubernetes Benchmark Decision Loop
- Deciders: Repository owner
- Supersedes: None

## TL;DR

- Select the merged infrastructure chart with real externally provisioned public CA values.
- Keep the Perfeng workload AppProject unchanged; use separate restricted add-on authority.
- Delegate generic fresh-metrics health to the pinned provider, never its mutable checkout.
- Leave certificate operations, benchmark qualification and runtime promotion separate.

## Context and decision

M04 qualification correctly stays UNKNOWN when resource usage is unavailable. The provider
now packages verified-TLS Metrics Server and explicit kubelet certificate operations; the
owner completed serving setup and supplied the actual public serving CA. Select provider
revision `5970bffa1f3d200e73d5a55e0cd4ef34ed9ef3b7` in the existing infrastructure lock, CI
checkout and new chart Application together. Compatibility checks preserve kind, node-image,
Argo CD and persistent-resource identities.

The consumer root owns a new `perfeng-local-resource-metrics` AppProject/Application. Permit
only the infrastructure source, local server, `monitoring`/`kube-system` and the seven required
resource kinds. Do not add infrastructure to the existing Perfeng workload Project or grant
that Project cluster-scoped permissions. ADR 0005's source-development/authorization boundary
remains intact; this is an explicitly reviewed add-on selection, not workspace-derived access.

Keep the real public CA in the Application as the single desired-state value. Private keys
remain in the separately provisioned immutable Secret. Use control-plane placement and an
explicit taint toleration. Argo CD renders and reconciles the chart; no second Helm deployment,
certificate hook or approval controller is introduced. Initial auto-sync is enabled while
pruning, self-healing and cascading deletion remain off.

## Alternatives and consequences

Broadening the Perfeng Project would authorize cluster-scoped add-on work for unrelated
Applications. Copying the chart or generic certificate/metrics logic would duplicate provider
ownership. Implicit preflight installation would change infrastructure while claiming to
observe it. These alternatives are excluded.

The consumer validates desired state and exact live owner/CA binding. Generic health delegates
to the selected provider through the existing isolated archive wrapper, which exposes only
metrics `health`, not mutation commands. The environment therefore locks the same maintained
cryptography dependency needed by provider execution and public CA validation. Offline checks
do not use a wall clock; actual CA validity/fresh observations remain live provider checks.

## Security, rollout and rollback

AppProject permissions constrain source/destination/kinds, not every RBAC rule or resource
name; pinned chart review and exact render checks remain required. Source-token read access
must include the private infrastructure repository. No credentials or retained state are rotated.
Fresh clusters need explicit certificate provisioning after ordinary namespace/GitOps bootstrap;
the staged foundation must not wait for an absent external TLS prerequisite.

After merge, let the root reconcile, require complete GitOps/metrics health, then capture new
all-PASS integration qualification. Revert a bad selection to a prior compatible chart through
Git/Argo CD; retain keys, node backups and data. A first-install revert does not uninstall
resources with pruning off. Full decommissioning and node rollback require explicit review.

## Verification

Run `make validate` and `make release-selection-check`. Test public-only CA parsing, source,
Project, placement and TLS rejection, isolated provider execution, read-only health failures
and staged bootstrap behavior. Real API aggregation, kubelet scraping and all-PASS qualification
must be verified after merge. They are prerequisites, not proof that M04 is complete.
See the [operating guide](../local-resource-metrics.md).
