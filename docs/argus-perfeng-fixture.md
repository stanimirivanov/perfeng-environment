# Disposable Argus–Perfeng preparation fixture

## TL;DR

- Run `make argus-perfeng-fixture-check` for offline validation and safety tests.
- Select `--execute` explicitly to build pinned candidates and migrate two fresh databases.
- A passing result proves artifact/database preparation, not running-product acceptance.
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
`evidence.json`. Two separate PostgreSQL containers have ephemeral memory-backed
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
runtime identities, and `cleanup: passed`. `productAcceptance: not-run` is deliberate:
no product service, protected object store, Kubernetes runner, k6 flow, analysis
publication or read-only projection is exercised here. Those need joint flow
composition before any integration claim; Argus SRE has no runtime in this fixture.

On failure, inspect the private evidence's resource names and the run label
`dev.perfeng.joint-fixture`. Do not print container inspection output: PostgreSQL
initialization passwords are present in container environment metadata. Restore
Docker access, verify exact name/label/ID ownership, and remove only those owned
containers followed by their network. Never prune or touch an existing cluster,
volume or deployment. A new invocation always uses fresh names; it does not adopt
or repair a previous run. See [troubleshooting](troubleshooting.md).
