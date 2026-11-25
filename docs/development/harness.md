# Perfeng environment coding harness

## TL;DR

- Use the [documentation map](../README.md) to load only task-relevant guidance.
- Run `make bootstrap` once, `make verify` while editing, and `make validate` before handoff.
- Run `uv run --locked --offline python -m harness offline` to check repository policy, GitOps composition, and reviewed script and Make routes.
- Helm rendering and immutable sibling-repository validation are explicit specialized tiers.
- `task bootstrap`, `task status`, `task smoke`, and `task destroy` are live operator actions and never run from validation aggregates.
- Turn recurring review findings into clearer guidance or an early deterministic check.

## Verification tiers

| Tier | Command | Purpose and constraints |
|:--|:--|:--|
| Setup | `make bootstrap` | Resolve the locked Python environment; network may be required. |
| Inner loop | `make verify` | Run repository-local formatting, policy, static, configuration, and unit checks without cluster access. |
| Render acceptance | `make render-check` | Lint and render charts offline; requires Helm. |
| Native gateway configuration | `make native-gateway-check` | Adapt the real Caddyfile offline using reviewed Caddy 2.11.7; no server, CA or trust provisioning. CI verifies archive checksums on Linux. |
| Supply chain | `make supply-chain` | Check the lock and known vulnerabilities; vulnerability data may require network access. |
| Complete non-live acceptance | `make validate` | Aggregate verification, rendering, and supply-chain checks; advisory lookup may require network access. |
| Pinned integration | `make release-selection-check` | Validate exact sibling revisions selected by deployment locks; requires the adjacent repositories and their selected commits. |
| Disposable-kind release integration | `make release-workflows-integration` | Separate GitHub-hosted Linux CI tier; maintenance, ledger recovery, external-state Jobs, readiness HTTP/native observation and Git/schema/preflight-child parity with synthetic inputs/workloads. Uses job-scoped package-read permission and package-side access grants. Refuses local/self-hosted runners and is absent from offline aggregates. See the [fixture guide](../release-workflows-integration.md). |
| Joint fixture safety | `make argus-perfeng-fixture-check` | Offline pin validation and ownership/failure tests; [explicit database preparation](../argus-perfeng-fixture.md) is separate from product acceptance. |
| Live operation | `task bootstrap`, `task status`, `task smoke`, `task destroy` | Access or mutate the dedicated local environment; follow the operating guides and obtain required authority. |
| Trusted-main composition/image parity | `release-preflight-main.yml` | Main-only GitHub-hosted CI with approved contents write/package read and one real-UID request; PostgreSQL and SeaweedFS chart/PVC restart plus S3 proxy isolation, preflight/maintenance/external-state parents and selected migrator/schema and explicit runtime table-grant parity, followed by selected server/native/HTTP parity, then guarded cleanup. Never run locally or on PR code. See [ADR 0020](../adr/0020-publish-disposable-ci-release-requests.md) and [ADR 0021](../adr/0021-authorize-trusted-main-external-state-parity.md). |

The metadata-only `pull_request_target` workflow checks a PR's title, leading
closing reference, and issue branch against one issue number. It checks out the
exact base commit and runs no code from the PR branch. The policy reads only
GitHub's local event file, emits no PR body content, and has read-only repository
permissions. Dependabot PRs are exempt because their generated branch and title
follow the dependency-update policy. This event-specific check is separate from
the offline `make verify` inner loop.

Unavailable checks follow the constrained-environment protocol in
[CONTRIBUTING.md](../../CONTRIBUTING.md#verification-and-constrained-environments).

## Offline harness pilot

After `make bootstrap`, run these commands from the repository root.

```sh
uv run --locked --offline python -m harness --help
uv run --locked --offline python -m harness offline
```

The stage runs repository policy, GitOps composition, and the command-route
guard in that order. It passes through each validator's output and stops at the
first failure with that validator's exit status. If a check exceeds the two-minute
deadline, the harness identifies it on stderr and returns status 124. The
[check manifest](../../harness/checks.toml) controls execution.

Follow the `fix:` diagnostic on a policy failure and use `make docs-check` to
rerun that validator directly. To investigate a composition failure, run
`uv run --locked --offline python scripts/gitops.py validate` directly.
The pilot needs no network access, credentials, or Kubernetes context. Run
`make verify` for all repository-local checks and `make validate` for complete
non-live acceptance.

## Reviewing command routes

A command route is a repository-owned way to invoke work independently. The
[reviewed baseline](../../harness/command-routes.toml) records each supported
script, module, or Make target with an exact ID, owner, purpose, and SHA-256 digest of
its callable definition. A baseline record grants review coverage. Dispatch
authority comes from the separate [check manifest](../../harness/checks.toml).
Application, fixture, and live-operation routes keep their owners and invocation.
Imported helpers and sourced-only shell phases stay with their callers.
[ADR 0077](../adr/0077-separate-harness-execution-from-command-route-review.md)
records the ownership decision.

The guard reads regular Git-tracked and non-ignored untracked files from the
working tree. Run it from the repository root with the Python version in
[.python-version](../../.python-version). The route fingerprints require
Python 3.12 so the same parsed sources produce the same fingerprints. A clean
checkout supplies the same inputs in CI. The guard uses Git for file names
and inspects source without importing or executing it.

| Route | Supported evidence and definition |
|:--|:--|
| Script or module | The exact repository path identifies each entry source. A file's owner execute bit, shebang, shell suffix, Python `__main__.py` or top-level `if __name__ == "__main__"`, and the observed Node ESM direct-run guard can establish callability. A sole `unittest.main()` guard in a `tests/test_*.py` module remains ordinary test discovery unless there is independent invocation evidence. |
| Interpreter call | Simple literal file references for Python, shell, Node, and `jq -f`, plus Python `-m` names, establish routes regardless of file mode. In shell sources and known Makefile, Taskfile, workflow, or Dockerfile locations, references can be repository-relative or use the integration harness's same-directory `$source_dir` convention. Literal-only `echo` and `printf` lines are help or status output. A shell file with observed `source` or `.` use remains a shell fragment unless its execute bit or a recognized independent invocation establishes callability. |
| Separately invoked payload | An exact Python caller path and named payload identify code handed to another interpreter. Supported `-c` arguments include module-level string constants, including a literal `.strip()`, and `Path(imported_module.__file__).read_text(...)` for a direct absolute from-import of a local Python file. The guard recognizes the Python `sys.executable` forms with no option before `-c`, `-I`, or `-IB`. The artifact uploader has specific rules for the Pod source-staging command and the `upload_script(source.reference)` program and its builder. The definition covers the recognized interpreter expression and the payload text, source bytes, or builder syntax. |

The script fingerprint covers the entry source and its owner execute bit.
For Python it also includes any applicable shebang and uses parsed syntax so
comments and source formatting can change independently. Other scripts use
source bytes since whitespace, comment-like lines in heredocs, and interpreter
directives can affect execution.

The interpreter scan recognizes the documented literal forms without resolving
arbitrary variables, templates, generated code, or dynamic dispatch. References
in Make, Task, and workflow files are evidence for file routes. Task targets,
workflow steps, and hooks have separate review coverage boundaries. For opaque
script invocations, prefer an explicit file or named payload the guard can
verify. For a necessary new opaque form, add a narrow rule tied to the exact
declaration and a fixture before recording its definition.

### Make targets

Each literal target in a conventional `GNUmakefile`, `makefile`, or `Makefile`
has an ID such as `make:Makefile#verify`. The guard checks target names, the
first ordinary target used as the default, `.PHONY` membership, the ordered
literal prerequisite list, and exact tab-prefixed recipe text. This covers
aggregation targets such as `verify` and `validate`, which have prerequisites
without recipes. Non-recipe comments and baseline purpose text can change
independently of execution definitions.

The guard traces plain `$(NAME)` and `${NAME}` recipe references and their
transitive repository definitions through simple `=`, `:=`, and `?=`
assignments. It also records which recipes are guarded by
`ifneq ($(strip $(NAME)),)` and captures the condition's variable definitions
at the point where Make would test it. External variable values remain inputs
to each native Make invocation. The guard reads source without starting Make
or expanding variables, running functions, or invoking shell recipes.

The supported forms cover the repository's conventional Makefiles. Includes,
computed names, Make functions outside the documented condition, continued
lines, repeated target or variable definitions, target-specific settings, and
special execution settings such as `.DEFAULT_GOAL` or `SHELL` produce an
`unsupported-make` finding. Multiple conventional names in one directory also
fail because GNU Make would choose one by filename precedence. Use a supported
literal declaration, or add focused static coverage with a fixture and
maintainer review when a new construct is required.

For a finding, use the reported declaration and `fix:` line. Reuse an existing
route when it has the same owner and purpose. For a deliberate addition or
definition change, review the callable source, then record the proposed digest
alongside the exact identity and purpose. Remove the corresponding record when
retiring a route. The guard leaves baseline editing to the contributor.

The scan accepts up to 8,192 Git path records and 1 MiB of path data. It reads
up to 2 MiB per candidate source and 32 MiB of candidate source overall. The
baseline has its own 2 MiB limit. Each Makefile can have up to 4,096 lines,
256 target names, 256 variable declarations, and 256 referenced variable names.
For checkout and runtime failures, use the
[offline troubleshooting steps](../troubleshooting.md#offline-command-route-check).

Request explicit maintainer sign-off in the PR for changes to the manifest,
baseline, guard, fixture expectations, or gate wiring. The maintainer approves
the route and its ownership even when the updated digest passes locally.
The metadata-only PR check retains its exact-base and read-only boundary.

## Harness principles

For GitHub delivery, use the [issue tracker configuration](../agents/issue-tracker.md).
Repository checks run independently of authorization or GitHub credentials,
with the documented advisory lookup in the separate supply-chain tier.
Template regressions use local fixtures.

The explicit `native-gateway.yml` Linux tier builds the serving binary from
locked Go inputs, audits it and exercises real isolated TLS/Host/URI and child
cleanup. It creates temporary loopback listeners/CA state but never changes a
trust store or contacts Kubernetes. This is separate from offline adaptation
and `make verify`; the main-only publication job requires `publish=true`.
Operators use `native:setup`, `native:doctor` and `native:up` after the first
reviewed serving release has been selected. Production connectivity and M03
acceptance remain separate live tiers.

PR jobs running the full unit suite require a checkout with `fetch-depth: 0`
and credential persistence disabled. With `GITHUB_BASE_REF` set, the immutable
verifier retirement audit compares against the merge base with `origin/<base>`
and reads historical source bytes. The native gateway Linux tier has the same
history requirement as main validation; a shallow PR merge checkout is insufficient.


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

Integration behavior needs actual disposable acceptance before PR handoff when
the owner has authorized dispatch. Use the existing `release-workflows-kind.yml`
manual event on the exact issue-branch commit; preserve all hosted-runner,
ownership, credential and cleanup guards. The current stabilization requires two
clean-cluster passes of one commit, including successful cleanup and distinct
cluster UIDs. A subsequent source change invalidates that evidence. Record run
URLs, commit and safe outcomes in the PR. Do not call offline mocks real API
acceptance or broaden permissions to make a negative test pass. See the
[qualification evidence](../release-workflows-integration.md#disposable-run-qualification-and-stage-evidence).

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
