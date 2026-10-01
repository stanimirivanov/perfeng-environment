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
| Supply chain | `make supply-chain` | Check the lock and known vulnerabilities; vulnerability data may require network access. |
| Complete non-live acceptance | `make validate` | Aggregate verification, rendering, and supply-chain checks; advisory lookup may require network access. |
| Pinned integration | `make release-selection-check` | Validate exact sibling revisions selected by deployment locks; requires the adjacent repositories and their selected commits. |
| Live operation | `task bootstrap`, `task status`, `task smoke`, `task destroy` | Access or mutate the dedicated local environment; follow the operating guides and obtain required authority. |

Unavailable checks follow the constrained-environment protocol in
[CONTRIBUTING.md](../../CONTRIBUTING.md#verification-and-constrained-environments).

## Harness principles

The root Makefile is the stable engineering command surface. CI invokes those targets instead of duplicating their internals. Repository validation stays offline and does not infer that a live cluster is healthy. Live Task commands remain separate because they require credentials, inspect external state, or mutate retained resources.

Checks should fail with the affected path, violated invariant, likely correction, and canonical guidance. New checks must be deterministic, cross-platform when part of `make verify`, and covered by focused tests. Do not weaken a check because a sandbox lacks its prerequisite.

## Extending the harness

Prefer extending an existing guide, target, or checker over adding a parallel entry point. Product-specific environment rules stay here even when mechanical policy checks later move to a versioned shared tool. Changes to live-operation boundaries, persistent ownership, or cross-repository release selection require the same architectural review as product changes.
