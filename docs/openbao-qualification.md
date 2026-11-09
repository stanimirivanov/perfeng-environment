# Local OpenBao identity and delivery qualification

## TL;DR

- Run the opt-in gate with the exact pinned OpenBao Linux x86-64 binary.
- The fixture checks OpenBao JWT policies, Agent delivery, rotation and revocation.
- Use this receipt for local synthetic qualification only. Live identity and
  publication require separate hosted evidence.

Download the [OpenBao v2.7.2 archive](https://github.com/openbao/openbao/releases/tag/v2.7.2)
for Linux x86-64. Check its SHA-256 against the
[fixture lock](../tests/qualification/openbao/fixture.lock.json) before
extracting `bao` into a private temporary directory. The gate independently
checks the executable hash before launching it.

```sh
make openbao-qualification OPENBAO_BIN=/absolute/path/to/bao
```

The fixture starts a disposable in-memory OpenBao server bound to loopback.
It generates signing keys and credentials for that invocation. It keeps the
private Agent files in a temporary directory under `/dev/shm`. HTTP stays on
loopback, and server and Agent output is suppressed so tokens and rendered
values stay out of logs. The fixture stops its own processes and removes its
temporary directories on completion. Run it only on a trusted local Linux host.

The server's real [JWT auth](https://openbao.org/docs/auth/jwt/) validates
separate synthetic workload and CI issuer keys. The workload role checks the
service-account subject and audience. The CI role also checks repository,
protected ref and environment claims. The gate verifies wrong identities and
out-of-scope reads, writes and listing fail. A real
[OpenBao Agent template](https://openbao.org/docs/agent-and-proxy/agent/template/)
renders the workload credential to a private file. Restarting Agent renders
the rotated value, then revoking its previous token denies access while the
new token still works.

The receipt covers synthetic signed claims, local KV v2 and file delivery.
Qualification of real Kubernetes projected tokens and TokenReview, GitHub OIDC
discovery and hosted identity, actual registry reload, TLS, and persistent
recovery belongs to separate gates. This fixture requires no cluster context,
registry credentials or hosted deployment.
