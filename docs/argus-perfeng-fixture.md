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
starts a fresh loopback-only kind cluster using a private kubeconfig, attaches
the two run-owned databases to its own Docker network, and runs the pinned
Argus and Perfeng binaries against those databases. It serves the tracked
checkout target and provisions a TLS-enabled, fixture-authenticated S3 endpoint.
The driver checks both product HTTP routes and verifies the Perfeng catalog
requires authentication and reports the fixture principal. The signed bucket
bootstrap verifies the HTTPS endpoint. Report access with the reader identity
is exercised by the subsequent consumption slice.

Look for `servingReady: true`, `servingCleanup: passed`, and `cleanup: passed`.
The evidence also records the disposable cluster UID. `productAcceptance`
stays `not-run` until a later slice runs the change, k6, analysis consumption,
and projection export. For serving cleanup recovery, verify the exact kind
node name and cluster label, its attachment to the run-owned Docker network,
and the network's run label and ID before taking action with the private
kubeconfig. Keep the run directory private.
