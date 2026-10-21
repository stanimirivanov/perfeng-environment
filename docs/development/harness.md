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
| Disposable-kind release integration | `make release-workflows-integration` | Separate GitHub-hosted Linux CI tier; maintenance, ledger recovery, external-state Jobs, readiness HTTP/native observation and Git/schema/preflight-child parity with synthetic inputs/workloads. Uses job-scoped package-read permission and package-side access grants. Refuses local/self-hosted runners and is absent from offline aggregates. See the [fixture guide](../release-workflows-integration.md). |
| Live operation | `task bootstrap`, `task status`, `task smoke`, `task destroy` | Access or mutate the dedicated local environment; follow the operating guides and obtain required authority. |

The metadata-only `pull_request_target` workflow checks a PR's title, leading
closing reference, and issue branch against one issue number. It checks out the
exact base commit and runs no code from the PR branch. The policy reads only
GitHub's local event file, emits no PR body content, and has read-only repository
permissions. Dependabot PRs are exempt because their generated branch and title
follow the dependency-update policy. This event-specific check is separate from
the offline `make verify` inner loop.

Unavailable checks follow the constrained-environment protocol in
[CONTRIBUTING.md](../../CONTRIBUTING.md#verification-and-constrained-environments).

## Harness principles

GitHub delivery follows [CONTRIBUTING.md](../../CONTRIBUTING.md#development-workflow):
use an authenticated `gh` executable to create/reuse the issue before implementation,
then create the issue branch, verify, commit, push and open the PR. Keep actual
verification evidence in the PR; return only its URL and wait for human review.
Requested revisions or delivered CI failures use the same branch and the reusable
[fix comment](../../.github/PR_FIX_COMMENT_TEMPLATE.md). Do not auto-merge.

Git/GitHub delivery is an explicitly authorized external workflow, not a dependency
of `make verify` or `make validate`. Repository checks MUST NOT create issues,
push branches, post comments or require GitHub credentials/network access (except
the documented advisory lookup in the separate supply-chain tier). Template
regressions use local fixtures. Missing `gh` or authentication is a delivery
blocker, not grounds for installing tools or changing credentials implicitly.

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

`make release-selection-check` also verifies the control-plane chart, bundle and
versioned runtime package at one pinned deployment commit. It verifies the native
target's pinned chart, publisher record and exact fixture source, then renders it
offline. CI uses a separate checkout for the native target pin. `task native:target-health`
is a distinct explicit read-only GHCR/Kubernetes acceptance tier, not a CI gate.

The same pinned integration tier renders the infrastructure-lock-selected metrics
chart with the real public serving CA, enforcing TLS, control-plane placement and
exact resource identities. Normal `make verify` checks its separate AppProject and
Application without sibling or cluster access. `task kubernetes:metrics-health`
is live read-only acceptance, not deployment or certificate provisioning.

## Extending the harness

`task kubernetes:acceptance` previews the M04 decision-loop/recovery exercise
without processes, network or writes. Its separately confirmed execution path
can submit Runs, explicitly approve a selected baseline and gracefully restart
one verified controller Pod. It MUST NOT become a bootstrap, status, smoke or CI
dependency. Deterministic acceptance regressions are part of `make test`;
real execution follows [the acceptance guide](../kubernetes-benchmark-acceptance.md)
only after the deployment cutover. Source tests never complete the milestone.

`make check` validates the Kubernetes benchmark profile and local schema checksum
offline. `make release-selection-check` compares the snapshot with the exact
contracts commit. `task kubernetes:qualify` is a separate explicit live read-only
tier that creates ignored receipts, never telemetry or benchmark workloads.
UNKNOWN/REJECTED captures cannot establish comparable qualification.

Prefer extending an existing guide, target, or checker over adding a parallel entry point. Product-specific environment rules stay here even when mechanical policy checks later move to a versioned shared tool. Changes to live-operation boundaries, persistent ownership, or cross-repository release selection require the same architectural review as product changes.
