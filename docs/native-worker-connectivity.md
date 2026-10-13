# Local Native Worker connectivity

## TL;DR

- The worker and Docker Desktop must run on the same Windows host; this is not LAN ingress.
- Select the first audited serving release, run `task native:setup`, then `task native:doctor` and `task native:up` in one foreground terminal.
- The supervisor owns three tunnels and Caddy, reports readiness and stops only its children. No PATH change or local Go build is required.
- Caddy generates a private CA but does not install trust. Review its fingerprint before trusting it.
- Use `https://127.0.0.1:8443` for the API, `:9443` for S3 uploads and `:10443/` for the search target.
- `task native:check` proves selected target image/Pod identity, trusted TLS, API readiness, exact fixture bytes and anonymous S3 denial only.
- Native acquisition stays blocked until the newer service image, native bundle, worker credential and real qualification evidence are selected and signed PUT/internal read-back passes.

## Boundary and prerequisites

This opt-in gateway implements [ADR 0006](decisions/0006-loopback-native-worker-connectivity.md).
It uses existing ClusterIP Services; it does not install ingress, replace the
cluster, expose NodePorts or compete with Argo CD. All listeners bind IPv4
loopback. A worker on a different machine needs a separately reviewed topology.

Complete the [local platform setup](local-control-plane.md) and confirm Docker,
the dedicated kubeconfig and the existing Services are healthy. The explicit
managed setup acquires the reviewed Caddy 2.11.7 CLI; preview, doctor and startup
never download tools or install trust. Caddy is an Apache-2.0, maintained HTTPS server replacing the
need for a custom proxy. The replacement boundary is the small Caddyfile, not
application code. See its [release](https://github.com/caddyserver/caddy/releases/tag/v2.11.7),
[Host-forwarding behavior](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
and [local CA behavior](https://caddyserver.com/docs/caddyfile/directives/tls).

The checked-in `native-gateway/caddy-release.json` anchors SHA-512 checksums for
the official Windows/Linux amd64 archives. CI uses those binaries only to adapt
the configuration offline. For live serving, review the binary's build toolchain
and current advisories as well as its release version. A checksum proves identity,
not absence of vulnerabilities. In particular, use a patched Go toolchain when
building Caddy from the reviewed release; an upstream release built before a Go
security fix does not inherit the control-plane dependency update.

The official adaptation archive reports Go 1.26.8 and is not a serving fallback.
Serving has a separate reviewed asset/checksum/source selection in
`native-gateway/serving-release.json`. The publisher builds the standard entry
point as a dependency using locked modules, Go 1.26.9 and x/net >=v0.60.0,
without replacements. It checks real version/build output, audits the resulting
executable and exercises isolated Windows TLS startup/shutdown. Local setup
checks the selected bytes and metadata again. Build minima and a past audit do
not prove freedom from future advisories; security updates require release review.

## Managed setup

[ADR 0010](decisions/0010-manage-native-connectivity-workflow.md) defines this
workflow. The checked-in release starts unselected deliberately: no unpublished
binary or fabricated checksum can be installed.

One-time release-owner handoff, after merging the implementation:

1. Run **Build and optionally publish verified native gateway** on `main`, with
   `publish=true`. It refuses to overwrite existing releases. PR/push runs only
   build, audit and test; they never publish.
2. Download the `gateway-release-selection` artifact. Review its source revision,
   asset ID/checksum and workflow audit/TLS evidence. Keep it under ignored `results`.
3. Preview then apply the exact selection, review the Git diff and commit it:

```powershell
uv run --locked python -m scripts.native_gateway_release select --file results/release-selection.json
uv run --locked python -m scripts.native_gateway_release select --file results/release-selection.json --execute
```

Operator setup needs Windows AMD64, uv, Task and the existing cluster tools—not Go.
Private release downloads use the existing repository **contents-read** token,
not a packages-only GHCR token. If it is not already set in this terminal:

```powershell
$gatewayToken = Read-Host "Repository contents-read token" -AsSecureString
$env:PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN = [System.Net.NetworkCredential]::new("", $gatewayToken).Password
task native:setup
task native:doctor
```

Setup installs under `.local/native-gateway/tools/<sha256>/caddy.exe`, checks
bytes before execution and uses an absolute path thereafter. Repeating setup
reuses verified bytes. Modified installations and unknown existing builds are
not overwritten. New gateway state is created privately; existing state is
never repaired implicitly. If doctor reports a permission problem, review the
directory and any earlier CA exposure, then explicitly use `task native:permissions`.
It restricts the root to this account, SYSTEM and administrators, retains every
file and checks child ACLs. Separately protected/broad child ACLs require manual
review. No certificate trust is installed. Do not trust previously exposed keys.

The setup token is not needed after installation; clear it when no longer needed:
`Remove-Item Env:PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN`.

## Preview and start

From the repository root, preview each boundary without external access:

```powershell
uv run --locked python scripts/native_connectivity.py forward-api
uv run --locked python scripts/native_connectivity.py forward-s3
uv run --locked python scripts/native_connectivity.py forward-target
uv run --locked python scripts/native_connectivity.py gateway
uv run --locked python scripts/native_connectivity.py check
```

Normal operation uses one foreground terminal, from this repository:

```powershell
task native:doctor
task native:up
```

Doctor reports every independent prerequisite as PASS, FAIL, ACTION REQUIRED or
NOT CHECKED. Cluster reads have explicit command budgets; a foreign/unavailable
cluster prevents resource reads. Missing Windows/Node trust is a separate browser
action, not permission to bypass TLS. Occupied ports are conflicts: stop your old
independent forwarding/gateway sessions first; no foreign listener is adopted.

Up verifies state/tool/resource identities, owns three tunnels and Caddy, waits
for endpoints and checks CA-backed TLS, exact content and anonymous denial.
Only then does it print CONNECTIVITY READY and URLs. It stays in the foreground,
ends after at most one hour and stops all its children on Ctrl+C, partial startup
failure or any child exit. Windows Job ownership also cleans up assigned children
if the supervisor is terminated. It is not a service or automatic restart loop.
Restart the session after a target Pod replacement. Gateway state and CA keys remain under ignored
`.local/native-gateway/caddy-data`; do not commit, copy or delete that private
state as an implicit repair.

| Use | HTTPS origin | Upstream |
|:--|:--|:--|
| Worker API | `https://127.0.0.1:8443` | `127.0.0.1:18080` → `perf-platform/control-plane:8080` |
| Presigned artifact PUT | `https://127.0.0.1:9443` | `127.0.0.1:18333` → `perf-platform/seaweedfs:8333` |
| Search journey | `https://127.0.0.1:10443/` | `127.0.0.1:18081` → exact verified `perf-platform/native-search` Pod, HTTP port 8080 |

No path, query or Host rewriting is configured. Do not alter a presigned URL,
remove its port or change signed request headers. Automatic HTTP redirects,
the Caddy admin API, HTTP/3 and request logging are disabled. Operational logs
are also discarded because an upstream error can include a presigned query.
Use the credential-free bounded checks for diagnosis, not debug/access logs.

## Review trust explicitly

After the gateway starts, the public root certificate is at:

```text
.local/native-gateway/caddy-data/pki/authorities/local/root.crt
```

Before starting a gateway that creates CA keys, restrict the Windows directory
ACL for `.local/native-gateway` to the execution account and necessary system
administrators using the directory's Security settings. Verify that broad
workspace read permissions are not inherited by private key files. The wrapper
rejects redirected state but does not rewrite Windows ACLs; key access is an
explicit host-setup responsibility. The managed doctor/up also verify private
gateway permissions. Do not trust a CA whose private key has been
exposed to other untrusted users.

Inspect that file and its fingerprint locally. The `.key` files are private and
must not leave the gateway state directory. The Python probe takes the reviewed
CA directly and needs no trust-store change:

```powershell
task native:check
```

For Chromium on this Windows user account, explicitly review and install the
public CA into the current-user root store. This is a trust-store mutation;
perform it only after checking that this is the CA just created by your gateway:

```powershell
certutil -dump .local/native-gateway/caddy-data/pki/authorities/local/root.crt
certutil -user -addstore Root .local/native-gateway/caddy-data/pki/authorities/local/root.crt
```

Restart Chromium after trust changes. Node's trust path is separate. In the
terminal that will start the Native Worker, set the public CA before launching
Node (it is read at process startup):

```powershell
$env:NODE_EXTRA_CA_CERTS = (Resolve-Path .local/native-gateway/caddy-data/pki/authorities/local/root.crt).Path
```

Never use `NODE_TLS_REJECT_UNAUTHORIZED=0`, `ignoreHTTPSErrors`, `--insecure`,
`tls_insecure_skip_verify` or a browser certificate-error bypass. A failed trust
check is a failed preflight, not permission to weaken the worker boundary.

## Controlled target and identity

Open `https://127.0.0.1:10443/` in the trusted Windows browser. Results start
hidden; clicking `#search` replaces the result list and reveals `#results`.
Repeated clicks replace nodes rather than retaining an ever-growing list. The
target uses no external dependencies, fetches or third-party resources. Its CSP
permits only the same-origin script; responses are not cached.

The check prints a target SHA-256 derived from each filename, a NUL separator,
its exact file bytes and another NUL, in order `index.html`, then `search.js`.
Git enforces LF endings. Record that identity with the reviewed source revision
when selecting the native recipe. It is **not** a `browser-environment/v1`
evidence hash, profile fingerprint, candidate image digest or approved baseline.

Content is controlled; timing remains dependent on the host. Use this fixture
to prove integration, not to claim an average customer workload or real-world
regression threshold. Follow [Windows qualification](windows-browser-host.md)
for actual execution-host evidence.

## Promotion gates and credentials

The controlled fixture now has [immutable image packaging](native-browser-candidate.md).
The [target selection](local-native-target.md) now deploys the published candidate
through Argo CD and proxies the target route to its verified Pod. Checkout files
remain exact-byte expectations and build source only; there is no serving fallback.
Native runtime selection and qualified browser execution are still separate gates.

The existing deployment lock still selects the earlier k6 image and bundle.
This PR does not invent a published image digest or enable worker acquisition.
Before running a native attempt, the subsequent release selection must:

1. Select a verified, published control-plane image with Native Worker v2 and
   the separate signing endpoint implementation. Keep image/source provenance
   together; do not replace an immutable digest with a mutable tag.
2. Select the complete `perfeng-k8s` native runtime bundle, a real qualified
   `browser-environment/v1` evidence hash, the controlled target URL, and the
   actual candidate image digest. Do not use a recorded host snapshot.
3. Set `PERFENG_S3_NATIVE_UPLOAD_ENDPOINT=https://127.0.0.1:9443` in the reviewed,
   content-addressed runtime configuration. Preserve the internal endpoint
   `http://seaweedfs.perf-platform.svc.cluster.local:8333`, path-style addressing,
   region, bucket and credentials. Recompute the runtime ConfigMap identity and
   update its lock/Application together. Do not use `kubectl set env` to bypass
   desired state or patch the shared Secret.
4. Provision a worker-audience credential through the control plane's reviewed
   authentication configuration. Keep it process-environment-only as
   `PERFENG_NATIVE_WORKER_TOKEN`. GHCR/repository-read and operator tokens are
   not worker tokens. Never print tokens or grant URLs.
5. Set the worker config's `controlPlaneOrigin` to `https://127.0.0.1:8443`,
   `schemaVersion` to `2` and its environment ID/hash to the real reviewed
   qualification evidence; keep local configuration under ignored state.
6. Prove worker authentication, one authorized presigned PUT through `:9443`,
   checksum/metadata verification through the control plane's internal S3
   client, and artifact registration using the actual Native Worker workflow.
   Preserve signed Host/URI/headers and retain non-secret evidence. Neither a
   TLS handshake nor anonymous denial satisfies this gate.

Run the existing Playwright `run-once` workflow only after those gates are
complete; use its operating guide in the sibling component. Do not copy S3
credentials onto the Windows host or expose a fake grant endpoint. Missing
published image or qualification identities block promotion, not gateway use.

## Failure and recovery

Start with `task native:doctor`; it reports the exact executable/checksum/build,
dedicated API, resources, ports, CA fingerprint and trust actions without repair.
Errors have fixed classifications and next actions. Unknown display versions are
not accepted merely because compilation succeeded. Missing tools, rejected bytes,
timeouts, port conflicts and child exits are distinct failures, not a generic
"external output withheld" diagnosis. Only safe fixed tool metadata and
credential-free probe results are shown; raw Caddy request logs stay disabled.

The old `native:api`, `native:s3`, `native:target` and `native:gateway` tasks remain
advanced single-boundary diagnostics. They are not the normal onboarding path;
do not run them concurrently with `native:up` on the same ports.

The probe sends only credential-free GETs, refuses redirects and environment
proxies, reads at most 64 KiB per response and has a 10-second timeout per
request. It requires exact API readiness bytes, exact target files and an S3
403 with `AccessDenied`. Successful output explicitly says that native
authentication, signed upload and acquisition remain unverified.

- TLS failure: confirm the reviewed public CA and server identity; check the
  gateway session and restart Node/Chromium after trust setup.
- API unavailable or unready: inspect the API forwarding session and the
  dedicated cluster's Deployment; do not substitute a different endpoint.
- S3 denial check fails: inspect its forwarding session and bucket policy. A
  public bucket or a proxy's generic 403 is not healthy.
- Target unavailable or mismatched: run `task native:target-health`, inspect its
  fourth foreground forwarding session and pinned source/image bytes; do not
  update expected identity or fall back to local files to accept an unknown page.
- Signed PUT fails: check expiry, signed headers, exact Host/port/URI, gateway
  upstream and selected service image. Do not log the grant or retry by changing
  its URL. Use the worker's supported retry/retained-evidence behavior.

Stopping the supervised session (or the four advanced sessions) removes exposure without changing workloads,
objects, PVCs, Secrets or Applications. CA trust is retained deliberately. To
remove it, identify the exact installed certificate fingerprint and review
`certutil -user -delstore Root <that-fingerprint>` before executing it; never
delete unrelated roots or rotate the gateway CA automatically. Clear
`NODE_EXTRA_CA_CERTS` in the worker terminal when reverting its configuration.

## Verification tiers

- `make verify`: deterministic preview, identity, transport, fixture and Task tests.
- `make native-gateway-check`: real Caddyfile adaptation, offline and no trust/state provisioning.
- Windows gateway workflow: actual locked binary build/audit and isolated TLS/Host/URI plus owned-child cleanup; no cluster or system trust.
- `task native:doctor`/`task native:up`: explicit prerequisite inspection and supervised connectivity only, not worker/qualification acceptance.
- `task native:check`: explicit live TLS/readiness/fixture/anonymous-denial checks.
- Final M03 acceptance: actual qualified browser execution, signed upload,
  internal read-back, normalization, baseline decision, cancellation and recovery.

Only report the tiers actually executed. The last two are not run by CI or by
ordinary repository validation.
