# Disposable Argus–Perfeng fixture

## TL;DR

- Run `make argus-perfeng-fixture-check` for offline validation and safety tests.
- Select `--execute` explicitly to build pinned candidates and migrate two fresh databases.
- Use the separate serving driver to start and probe both products in a fresh kind cluster.
- The fixture removes only its run-owned resources; retain private evidence after failure.

## Execution and evidence

From `perfeng-environment`, use the locked Python environment:

```sh
uv run --locked --offline python tests/integration/argus-perfeng/fixture.py --execute
```

Execution requires Linux amd64, Git with read access to both product repositories,
Go (the pinned toolchain may download), Docker and network access to GitHub,
Go modules and the pinned PostgreSQL image. No sibling checkout is changed.
`inputs.json` pins exact merged sources, Go and the PostgreSQL digest. Argus has
no published runtime image: these binaries are fixture candidates, not releases.
Changing pins requires review; the active deployment lock remains independent.

Each invocation creates a private mode-0700 temporary directory, builds the actual
migration/server commands, and records source revisions and artifact hashes in
`evidence.json`. The Argus pin includes the merged authorized exporter. The fixture
also builds the catalog, selection, request and evidence operator commands from
that same source revision and records each binary's SHA-256. Two separate PostgreSQL containers have ephemeral memory-backed
storage, CPU/memory/process limits, a private internal network and no host-published
ports. Each pinned static migrator is mounted read-only into its database container and
initializes its own schema over authenticated loopback TCP. Separate runtime roles
receive schema/table access without schema creation or database/role administration;
authenticated TCP checks verify those identities. These are fixture-only grants,
not production privilege qualification. Passwords travel through environment/stdin,
never command arguments; connection URLs remain in private mode-0600 files (migration is container-local;
runtime uses the run-owned network hostname).

The 15-minute preparation deadline excludes bounded cleanup (30 seconds per Docker
command). Cleanup inspects each exact random name and run label, removes by the
inspected ID in reverse order, and records `cleanup: passed` only after successful
removal and absence checks. SIGINT/SIGTERM attempt cleanup; SIGKILL/host loss cannot.
Private build caches and credentials remain outside Git for diagnosis. Delete the
printed run directory after reviewing evidence and confirming cleanup.

Flow drivers can import `Fixture` and use `with fixture.prepared():` to consume
these candidates and credentials on its run-owned network before cleanup. The
standalone preparation command exits the same context immediately after readiness.

## Boundaries and recovery

Success means `preparationReady: true`, both databases migrated with verified
runtime identities, and `cleanup: passed`. The preparation command covers
artifacts and databases. The serving driver adds
running services and storage. The Kubernetes runner, k6 flow, analysis publication
and projection are subsequent flow gates. Argus SRE has no runtime in this fixture.

On failure, inspect the private evidence's resource names and the run label
`dev.perfeng.joint-fixture`. Do not print container inspection output: PostgreSQL
initialization passwords are present in container environment metadata. Restore
Docker access, verify exact name/label/ID ownership, and remove only those owned
containers followed by their network. Never prune or touch an existing cluster,
volume or deployment. A new invocation always uses fresh names; it does not adopt
or repair a previous run. See [troubleshooting](troubleshooting.md).

## Serving readiness

Run the serving driver from the same locked environment.

```sh
uv run --locked --offline python -m tests.integration.argus-perfeng.serving --execute
```

This also needs kind, kubectl, OpenSSL, the pinned `perfeng-k8s` source in the
sibling checkout, and registry access for the pinned container images. It
starts a fresh loopback-only kind cluster using a private kubeconfig. Separate
control-plane and generator nodes satisfy the pinned Job selectors. It attaches
the two run-owned databases to its own Docker network, and runs the pinned
Argus and Perfeng binaries against those databases. It serves the tracked
checkout target and provisions a TLS-enabled, fixture-authenticated S3 endpoint.
The driver checks both product HTTP routes and verifies the Perfeng catalog
requires authentication and reports the fixture principal. The signed bucket
bootstrap stages the pinned report policy. The scoped uploader write/read checks
the HTTPS endpoint and writable volume capacity. Report access with the reader identity
is exercised by the subsequent consumption slice.

Look for `servingReady: true`, `servingCleanup: passed`, and `cleanup: passed`.
The evidence also records the disposable cluster UID. `productAcceptance`
stays `not-run` until a later slice runs the change, k6, analysis consumption,
and projection export. For serving cleanup recovery, verify the exact kind
node names and cluster label, their attachment to the run-owned Docker network,
and the network's run label and ID before taking action with the private
kubeconfig. Keep the run directory private.

## Signed change and checkout smoke

```sh
uv run --locked --offline python -m tests.integration.argus-perfeng.flow --execute
```

The driver creates two Git commits containing the checkout target and a single
OpenAPI change. Its private HTTPS GitHub API fixture serves the exact commit
contents and Git diff. The driver submits a signed pull-request webhook to Argus
and checks that Argus retained the same base, head, delivery and checkout
capability. The `changeIngress` receipt records those identities and an impact
digest.

The driver then uses Perfeng's authenticated Run API to execute the pinned
checkout smoke workload for that head. `checkoutSmoke` requires a successful k6
exit and the run-scoped raw, normalized and analysis artifacts. The smoke
request is submitted directly for fixture qualification. `productAcceptance`
remains `not-run` pending reviewed Argus dispatch, protected report consumption
and authorized export.

## Reviewed acceptance

```sh
uv run --locked --offline python -m tests.integration.argus-perfeng.acceptance --execute
```

This driver uses the pinned checkout scenario and images with a separately hashed
seven-second fixture profile. The short profile leaves time for analysis and
reporting before the provider enforces the one-minute catalog deadline. It first
runs the signed change and reference checkout smoke, verifies
the reference Run's artifact bytes, qualifies the baseline using observed HTTP
timings, and approves that disposable baseline through Perfeng. It publishes the
scoped catalog to Argus and uses a fixture-issued review for one exact selection.
Argus dispatches the approved request and consumes its matching report over
HTTPS with a read-only object credential. The exporter uses a separate restricted
database login and must refuse an unapproved position.

The private run directory contains the authorized projection after it passes
the Run and report correlation checks. The safe `jointAcceptance` receipt
records the Run ID and the request, report, intent and projection digests.
Require `productAcceptance: passed`, `servingCleanup: passed`, and
`cleanup: passed` for joint acceptance.

## Interruption and post-deadline recovery

```sh
uv run --locked --offline python -m tests.integration.argus-perfeng.recovery --execute
```

This variant waits for the reviewed Run and its durable report publication.
Before issuing the protected analysis review, it deletes the fixture Argus pod
with a UID precondition and verifies that no analysis was retained. A replacement
starts after the original request deadline and consumes the original report. A
second replacement replays the publication and must leave exactly one retained
analysis event and the same admission. The scoped exporter checks the same
Run, report digest and event. The two authorized exports must match byte for byte.

The receipt records three pod UIDs and the publication, deadline and recovery
timestamps. Delayed consumption does not change the publication time. The
projection marks a publication late only if the provider published it at or
after the deadline. This fixture exercises delayed consumption of a real
publication that normally predates the deadline.

## Advisory CI observation

The **Argus shadow observation** workflow runs after pushes to `main`. It can
also be dispatched against `main` for a fresh observation. The workflow uses the
disposable recovery driver with the same controlled Git change for the reference
checkout and the Argus-reviewed checkout. Each execution uses the pinned public
source and a fresh GitHub-hosted Linux runner.

The job summary records `passed`, `failed`, `not-run`, or `invalid-evidence`.
A passing run uploads a three-day receipt containing the two Run IDs, the report
and projection digests, and the retained event count and publication-lateness
flag. Failure receipts contain a fixed status. Private evidence, approvals,
credentials, and the projection itself stay in runner-local storage. Existing
validation jobs provide the merge and release checks. Disable the advisory
workflow in Actions to stop observations.
