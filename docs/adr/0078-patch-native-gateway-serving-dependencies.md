# ADR-0078: Patch the native gateway serving dependencies

- Status: Proposed
- Date: 2026-11-25
- Amends: [ADR 0029](0029-require-patched-native-gateway-toolchain.md)

## TL;DR

- Require Go 1.27.4 and lock the gateway build to patched OpenTelemetry modules and x/crypto.
- Keep symbol-level auditing of the built executable and review publication and serving selection separately.

## Context and decision

The Linux serving build in [PR #405](https://github.com/stanimirivanov/perfeng-environment/pull/405)
reported reachable findings for `net/http` and OpenTelemetry. Require Go 1.27.4
in the build and audit modules, CI and serving verification to address
[GO-2026-6859](https://pkg.go.dev/vuln/GO-2026-6859).
Lock the affected OpenTelemetry modules to v1.47.0 for
[GO-2026-6974](https://pkg.go.dev/vuln/GO-2026-6974) and
[GO-2026-6975](https://pkg.go.dev/vuln/GO-2026-6975).
Use x/crypto v0.59.0, the stable release following the module fix for the
module-only [GO-2026-5932](https://pkg.go.dev/vuln/GO-2026-5932) observation.

Build the locked Caddy executable and require the existing binary audit and TLS
smoke to pass. Keep the selected asset's recorded compiler and checksum. New
setup and start continue to require a separately published and reviewed asset
built with the required compiler.
