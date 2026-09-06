# Windows browser host

Browser performance runs execute natively on an owned Windows machine rather
than inside the local Linux kind cluster. The versioned
`windows-mainstream/1.0.0` profile defines one controlled reference machine; it
is not a statistical claim about the average customer laptop.

Validate the profile on any development platform:

```sh
uv run --locked python scripts/browser_host.py validate
```

On the Windows execution host, run the read-only qualification immediately
before starting Playwright:

```powershell
uv run --locked python scripts/browser_host.py check
```

The command reads Windows CIM data and the time-service status. It does not
change power, display, browser, or operating-system settings. An accepted result
requires the configured physical-host, OS, CPU, memory, GPU, display, AC-power,
Balanced-plan GUID, idle-CPU, and clock conditions. A rejected result lists every
observed mismatch and must prevent a comparable baseline run.

Write qualification evidence through the script rather than shell redirection.
The destination is UTF-8 and is never overwritten:

```powershell
uv run --locked python scripts/browser_host.py check --output results/browser-host-qualification.json
```

The canonical profile SHA-256 is the environment fingerprint supplied to the
Playwright v2 configuration. The qualification report preserves the observed
host snapshot, including installed, physical, and process-available CPU cores.
The Playwright runner still owns its actual browser version and measurement
artifact.

After the runner writes `playwright-measurements/v2`, assemble both sources into
the immutable environment artifact:

```powershell
uv run --locked python scripts/browser_environment.py assemble --qualification results/browser-host-qualification.json --measurements results/playwright-measurements.json --output results/browser-environment.json
```

Assembly requires an accepted qualification captured no later than the start of
the measurement window. It verifies the profile identity and fingerprint,
native Windows platform and architecture, and actual browser name and headless
mode against the selected profile. On success it prints the output SHA-256 and
byte count. Existing outputs are never replaced.

This step produces `browser-environment/v1` bytes only. Upload, artifact
references, diagnostic-manifest construction, and control-plane registration
remain separate integration boundaries.

## Publish local browser artifacts

After the runner and environment assembler succeed, preview the storage bridge:

```powershell
uv run --locked python scripts/browser_upload.py upload `
  --measurements results/playwright-measurements.json `
  --environment results/browser-environment.json `
  --receipt results/browser-upload.json
```

Add the output required by the selected diagnostic mode: `--observations`,
`--trace`, or both `--heap-snapshot-before` and `--heap-snapshot-after`. Add
`--execute` only after reviewing the Pod preview.

Execution uses a short-lived locked-down AWS CLI Pod and the restricted uploader
Secret already installed in the local cluster. Artifact bytes are streamed from
the Windows host without copying credentials out of Kubernetes. Every PUT is
conditional, every object is read back and checksum-verified, and a successful
run writes `local-browser-upload/v1` as an immutable local receipt. The receipt
contains raw `artifact/v1` references for later manifest construction; it is not
itself a platform contract or proof that a diagnostic capture was complete.

If publication fails, successfully written objects are retained and no receipt
is produced. The named uploader Pod is retained for inspection. Object deletion
and retry recovery are intentionally outside this step.

## Assemble diagnostic provenance

For a diagnostic run, combine the exact local outputs with both immutable
receipts after publication succeeds:

```powershell
uv run --locked python scripts/browser_diagnostics.py assemble `
  --measurements results/playwright-measurements.json `
  --environment results/browser-environment.json `
  --runner-receipt results/playwright-receipt.json `
  --upload-receipt results/browser-upload.json `
  --trace results/chrome-trace.json.gz `
  --output results/browser-diagnostics.json
```

Replace `--trace` with `--observations`, or with the paired heap-snapshot
options, according to the measurement mode. The assembler verifies the local
bytes against the runner and upload receipts, checks Run, test, workload,
environment, browser-lifetime, iteration, and timestamp relationships, and
writes `browser-diagnostics/v1` without replacing an existing file. Baseline
runs do not produce a diagnostic manifest.

The manifest records trace data loss as `INCOMPLETE`; it never converts a lossy
trace into complete evidence. Diagnostic manifest upload, raw-result assembly,
and control-plane registration remain later boundaries.

Recorded snapshots are accepted only through an explicit option for tests and
offline troubleshooting:

```sh
uv run --locked python scripts/browser_host.py check --snapshot tests/fixtures/windows-browser-host.json
```

Do not use a recorded snapshot to qualify a real run. Profile changes require a
new version and review because they create a different comparison cohort.
