# Perfeng environment coding harness

## TL;DR

- Use the [documentation map](../README.md) to load only task-relevant guidance.
- Run `make bootstrap` once, `make verify` while editing, and `make validate` before handoff.
- Helm rendering and immutable sibling-repository validation are explicit specialized tiers.
- `task bootstrap`, `task status`, `task smoke`, and `task destroy` are live operator actions and never run from validation aggregates.
- Turn recurring review findings into clearer guidance or an early deterministic check.

## Verification tiers

| Tier | Command | Purpose and constraints |
|:--|:--|:--|
| Setup | `make bootstrap` | Resolve the locked Python environment; network may be required. |
| Inner loop | `make verify` | Run repository-local formatting, policy, static, configuration, and unit checks without cluster access. |
| Render acceptance | `make render-check` | Lint and render charts offline; requires Helm. |
| Native gateway configuration | `make native-gateway-check` | Adapt the real Caddyfile offline using reviewed Caddy 2.11.7; no server, CA or trust provisioning. CI verifies archive checksums on Windows/Linux. |
| Supply chain | `make supply-chain` | Check the lock and known vulnerabilities; vulnerability data may require network access. |
| Complete non-live acceptance | `make validate` | Aggregate verification, rendering, and supply-chain checks; advisory lookup may require network access. |
| Pinned integration | `make release-selection-check` | Validate exact sibling revisions selected by deployment locks; requires the adjacent repositories and their selected commits. |
| Live operation | `task bootstrap`, `task status`, `task smoke`, `task destroy` | Access or mutate the dedicated local environment; follow the operating guides and obtain required authority. |

Unavailable checks follow the constrained-environment protocol in
[CONTRIBUTING.md](../../CONTRIBUTING.md#verification-and-constrained-environments).

## Harness principles

The explicit `native-gateway.yml` Windows tier builds the serving binary from
locked Go inputs, audits it and exercises real isolated TLS/Host/URI and child
cleanup. It creates temporary loopback listeners/CA state but never changes a
trust store or contacts Kubernetes. This is separate from offline adaptation
and `make verify`; the main-only publication job requires `publish=true`.
Operators use `native:setup`, `native:doctor` and `native:up` after the first
reviewed serving release has been selected. Production connectivity and M03
acceptance remain separate live tiers.


`make native-target-image-check` is a separate explicit Docker build and isolated
loopback image verification tier. It can fetch the pinned base and is not an
offline validation aggregate. CI exercises it without publishing or deploying;
the main-only publisher requires an explicit publication choice.

The root Makefile is the stable engineering command surface. CI invokes those targets instead of duplicating their internals. Repository validation stays offline and does not infer that a live cluster is healthy. Live Task commands remain separate because they require credentials, inspect external state, or mutate retained resources.

Checks should fail with the affected path, violated invariant, likely correction, and canonical guidance. New checks must be deterministic, cross-platform when part of `make verify`, and covered by focused tests. Do not weaken a check because a sandbox lacks its prerequisite.

`make check` validates both checked-in browser cohorts offline. The optional
laptop cohort does not replace the mainstream CLI default; live qualification
and assembly require the same explicit profile selection.

`make release-selection-check` also verifies the native target's pinned chart,
publisher record and exact fixture source, then renders it offline. CI uses a
separate checkout from the older control-plane deployment pin. `task native:target-health`
is a distinct explicit read-only GHCR/Kubernetes acceptance tier, not a CI gate.

## Extending the harness

Prefer extending an existing guide, target, or checker over adding a parallel entry point. Product-specific environment rules stay here even when mechanical policy checks later move to a versioned shared tool. Changes to live-operation boundaries, persistent ownership, or cross-repository release selection require the same architectural review as product changes.
