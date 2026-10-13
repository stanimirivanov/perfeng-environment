# Immutable controlled browser candidate

## TL;DR

- Build the existing search fixture as `ghcr.io/stanimirivanov/perfeng-native-search`; its published digest will be the Run candidate identity.
- Use maintained unprivileged NGINX, a digest-pinned base, a five-file build context including the project license and no custom application server.
- The main-only publisher is opt-in and verifies the pushed digest after pulling it back. Build-only checks allocate no published identity.
- Publication and the [image-backed target selection](local-native-target.md) are separate boundaries; neither promotes a native bundle or enables acquisition.
- Qualification remains strict. A rejected host report is not an approved environment artifact, and fixture timing is not a customer-performance baseline.

## Ownership and immutable meaning

`perfeng-environment` already owns the controlled integration fixture under `native-gateway/search`. This image packages that fixture, not production application logic. `perfeng-k8s` still owns Kubernetes workload packaging; a subsequent native promotion must deploy the exact candidate digest and route the HTTPS target to that deployment before declaring the candidate tested.

The [selected target](local-native-target.md) now uses the published image and a
pinned reusable chart behind the proxy-only gateway. Checkout files remain
verification expectations/build source, not a serving fallback. Image publication,
GitOps target selection and native runtime promotion remain separate boundaries.

The application content fingerprint remains the hash of the names and exact `index.html`/`search.js` bytes used by the existing gateway. The image digest additionally binds NGINX, configuration and layers. OCI labels record source repository, exact source revision, target version and content fingerprint. Neither fingerprint is a browser-host or browser-environment evidence hash.

The base is NGINX unprivileged 1.30.5 Alpine slim, selected by the manifest digest in `image.json` and `Dockerfile`. The [upstream repository](https://github.com/nginx/docker-nginx-unprivileged) documents its non-root runtime, temporary paths and digest-based pinning. The Docker packaging is Apache-2.0; NGINX and base packages retain their upstream licenses. Updating a base digest requires review and real image verification; a pinned digest and successful functional test do not prove the absence of image vulnerabilities. Review current base/runtime advisories before promotion under the [dependency policy](development/dependency-policy.md).

## Local verification

Preview without Docker or state writes:

```powershell
uv run --locked --offline python scripts/native_target.py validate
uv run --locked --offline python scripts/native_target.py build
```

To build and exercise an isolated development image explicitly:

```powershell
make native-target-image-check
```

The development build labels its source `development`, not a fabricated commit. It cannot be used as a published candidate identity. Build and verification require a local Docker socket and never access Kubernetes. The smoke container runs as UID/GID 101 with a read-only root, dropped capabilities, no new privileges, bounded CPU/memory/PIDs and a small `/tmp` tmpfs. Its port binds only loopback and is assigned dynamically.

The check validates image metadata, exact HTML/JavaScript/health bytes, CSP/no-store/nosniff headers, HEAD semantics, unknown-path denial and modifying-method denial. It refuses redirects and bounds reads. It removes only its exact container ID after verifying invocation ownership; a cleanup conflict retains the container. An image is retained locally for inspection. No image or volume owned by another task is removed.

CI builds and verifies the same image without publishing or deploying. The explicit image tier stays outside offline `make verify` and `make validate` aggregates because Docker builds may fetch the public pinned base.

## Publish after merge

On `perfeng-environment/main`, run **Build and optionally publish controlled browser candidate** with `publish=true`. The default is build-only. The workflow tests the source, builds Linux AMD64 with the exact GitHub source SHA, verifies the local image, logs into GHCR only for publication, pushes a uniquely named temporary tag, extracts the allocated repository digest, pulls it back and repeats verification against that immutable reference.

Copy the summary's four non-secret fields: verified candidate image, source revision, target SHA-256 and platform. Use the final `@sha256:` reference, never the temporary tag or a Docker image config ID. No digest is invented or reserved ahead of publication.

The package is associated with this repository through its OCI source label. Confirm its visibility and ensure the reviewed deployment pull identity can read it. Publication uses the repository's `GITHUB_TOKEN` with job-scoped package-write permission; no new long-lived publication credential is required. Existing repository-content/GHCR read tokens are not browser-worker credentials.

## Host qualification failures

The current `windows-mainstream/1.0.0` reference requires OS build at least 22631, eight available logical cores, 1920×1080 at 125% and 60 Hz, Balanced power, and background CPU no higher than 10%. Check the rejection reasons before changing anything. The profile is a defined comparison cohort, not a claim that every reasonable laptop must match it.

Braces and case around a Windows power-plan GUID do not change identity; the checker compares parsed GUIDs while preserving the raw observation. Different GUIDs still reject. This corrects a false mismatch without changing the profile or its fingerprint.

Real hardware/display differences need an explicitly reviewed separate profile/version or a matching host. Do not silently rewrite this profile, lower the OS floor or relax calibration to accept the available machine. High background CPU requires another live capture under the approved conditions, not editing the report. Keep `results/` outside Git; existing reports are never overwritten or converted from REJECTED to ACCEPTED.

The separate `windows-four-core-4k/1.0.0` cohort is now available through
explicit selection in the [Windows host guide](windows-browser-host.md#select-a-comparison-cohort).
It uses four available cores and 3840×2160 at 100% and the observed integer
59 Hz, but retains OS build at least 22631 and background CPU at most 10%.
Selecting it does not qualify the earlier rejected capture or promote a bundle.

After host approval and a real runner capture, assemble `browser-environment/v1` following the [Windows host guide](windows-browser-host.md). A qualification JSON hash alone is not that environment artifact's hash.

## Promotion and recovery

Next, select the published candidate, qualified environment artifact, exact Playwright source, compatible analysis/report images and the newer control-plane image in the complete native bundle. Preserve the existing immutable deployment until that review is complete. Then select the native bundle and content-addressed runtime inputs, provision the distinct worker identity and prove signed upload/internal read-back plus the full decision loop.

Rollback stops candidate use and restores the previous selection; it does not delete evidence, rotate credentials, remove CA trust or rewrite prior bundles. Publication failure leaves no approved promotion input. A failed local check reports only a safe operation boundary rather than raw Docker logs. M03 remains incomplete until qualified live Windows/control-plane acceptance, cancellation and explicit recovery pass.
