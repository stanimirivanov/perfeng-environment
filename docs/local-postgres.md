# Local PostgreSQL

This optional foundation provides the metadata database from proposal sections
37-38 and 83. It is a single-instance development database, not production
storage, a SUT database, or the object store for raw artifacts.

## Deploy and check

First complete `cluster.py up --execute` and `cluster.py deploy --execute` as
described in [local Kubernetes](local-kubernetes.md). PostgreSQL requires the
`perf-platform` namespace and kind's `standard` local-path StorageClass with
`WaitForFirstConsumer` binding. No provisioner is installed by this script.

From this repository, in PowerShell or a POSIX shell:

```sh
uv run --locked python scripts/validate_cluster.py
uv run --locked python scripts/postgres.py deploy
uv run --locked python scripts/postgres.py deploy --execute
uv run --locked python scripts/postgres.py health --execute
```

Without `--execute`, no cluster calls, credential generation, or writes occur.
Execution verifies the local Docker endpoint and dedicated kind kubeconfig before
any Kubernetes access. It prints progress without revealing child output.
The initial image download can take several minutes. A failed deployment leaves
resources and credentials in place for inspection; it does not roll back or
delete data. Redeploy after correcting the cause.

Deployment generates a 256-bit random password only on the first deployment,
creates an immutable `perfeng-postgres-auth` Kubernetes Secret via private stdin,
then installs release `postgres`. There are no passwords in arguments, chart
values, Helm release data, Git, or host files. Subsequent deployments reuse the
owned Secret and do not rotate it. If a StatefulSet or PVC exists without the
Secret, the script refuses to create a replacement password. Restore the matching
Secret using your private backup/recovery process; do not delete the PVC to bypass
the check. Concurrent credential creation fails rather than overwriting a Secret.

The health command verifies rollout, a Bound PVC, and an authenticated `SELECT 1`
through the Service DNS name. It does not create tables or prove backup recovery.
The existing `cluster.py health` still checks only nodes, CoreDNS, and the SUT;
run PostgreSQL health separately when using this optional component.

## Runtime and ownership

- Image: official `postgres:17.11-bookworm`, pinned to OCI index digest
  `sha256:051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0`.
  Resolved from Docker Hub on 2026-09-02; supports Linux AMD64 and ARM64 among
  other architectures. Source revision: `2603e26e245e558218728ee14e0a42dcb020dc7f`.
- Namespace/release/StatefulSet: `perf-platform` / `postgres` / `postgres`.
- Database: `perfeng`; bootstrap administrator: `postgres`.
- Service: `postgres.perf-platform.svc.cluster.local:5432`, headless ClusterIP,
  no host port, NodePort, or ingress.
- One non-root instance (UID/GID 999), no service-account token, read-only root
  filesystem, dropped capabilities, bounded CPU/memory, and readiness probes.
- Placement: local control-plane node, with the control-plane taint tolerated.
  Database activity therefore avoids the two workload workers, but still shares
  the physical host and competes with Kubernetes. This is not a calibrated lab.
- PVC: `data-postgres-0`, 2 GiB, `ReadWriteOnce`, kind local-path storage.

The environment owns the server deployment, bootstrap credentials, and storage
configuration. `perfeng-control-plane` will own application roles, tables,
migrations, and their upgrade tests. No prototype SQL, test users, application
schema, or automatic extensions are installed here. Do not give a future runtime
service the bootstrap superuser credentials; least-privilege roles come with the
control-plane persistence step.

## Security boundaries

The password is mounted read-only using `POSTGRES_PASSWORD_FILE`. TCP connections
use SCRAM password authentication; local Unix-socket access inside the database
container uses trust authentication for administration. A user with Kubernetes
Secret-read or pod-exec permission can obtain database administrator access.
Kubernetes Secrets are not a substitute for an external secret manager; this
local setup does not configure encryption at rest, TLS, or NetworkPolicy
enforcement. Do not expose it externally or store production data in it.

Never print the Secret, paste its YAML into issues, or put credentials in Helm
values. The Secret is immutable because changing an initialization password does
not change a password in an existing PostgreSQL data directory. Planned password
rotation requires coordinating SQL password changes with a replacement Secret;
it is intentionally not automated in this slice.

## Persistence, retention, and recovery

The StatefulSet explicitly retains its PVC when deleted or scaled down. A pod
restart or Helm redeploy reuses the same volume and credentials. There is no
automatic retention expiry or data cleanup. Inspect disk use as local data grows;
the 2 GiB request is not a storage quota enforced by all local-path filesystems.

Retention is limited to the lifetime of the kind node and its Docker storage.
`cluster.py down`, Docker storage removal, or loss of the host destroys local
data. Helm uninstall retains the StatefulSet PVC and the separately created
Secret, but deleting the namespace removes both. PVC deletion can delete the
backing local-path data. None of these operations constitutes a backup.

Before teardown when data matters, export with PostgreSQL `pg_dump` to a secure
location outside the cluster and preserve credentials separately. Use `pg_dump`
custom format with a file destination and binary-safe copying, not a Windows
PowerShell text pipeline. Test `pg_restore` into a separate disposable database
before relying on the export. Backup scheduling, encryption, retention policies,
automated restore, and major-version migration are not implemented here.

Manual persistence acceptance after a successful deployment (local fixture only):

```sh
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local -n perf-platform exec postgres-0 -- psql -X -U postgres -d perfeng -v ON_ERROR_STOP=1 -c "CREATE SCHEMA IF NOT EXISTS environment_smoke; CREATE TABLE IF NOT EXISTS environment_smoke.persistence_probe (id integer PRIMARY KEY); INSERT INTO environment_smoke.persistence_probe VALUES (1) ON CONFLICT DO NOTHING;"
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local -n perf-platform rollout restart statefulset/postgres
uv run --locked python scripts/postgres.py health --execute
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local -n perf-platform exec postgres-0 -- psql -X -U postgres -d perfeng -At -v ON_ERROR_STOP=1 -c "SELECT id FROM environment_smoke.persistence_probe WHERE id = 1;"
```

The final query must return `1`. This deliberately restarts only the local
PostgreSQL pod and leaves a small fixture table for repeatable inspection.
It validates restart persistence, not host-loss recovery or backups.

## Provenance and next boundary

Retains the StatefulSet/PVC intent of `performance-platform` commit
`57c1b5074898d4d86476e8b4f99c19eff3a77018`, `infra/charts/postgres/` and
`infra/local/postgres.yaml`. The original files are untouched. Hardcoded test
credentials, prototype application SQL, duplicate PVC definitions, and the old
namespace are not copied.

The next storage slice adds S3-compatible raw-artifact storage before durable
k6 Jobs. This PostgreSQL deployment does not satisfy raw-artifact durability.

References: [official PostgreSQL image](https://hub.docker.com/_/postgres),
[PostgreSQL 17.11 release](https://www.postgresql.org/docs/release/17.11/),
[StatefulSet storage retention](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#persistentvolumeclaim-retention),
[PostgreSQL backup and restore](https://www.postgresql.org/docs/17/backup.html).
