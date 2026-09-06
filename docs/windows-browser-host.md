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

The canonical profile SHA-256 is the environment fingerprint supplied to the
Playwright v2 configuration. The qualification report preserves the observed
host snapshot. The Playwright runner still owns its actual browser version and
measurement artifact; a later integration step will combine both sources into
the full `browser-environment/v1` artifact and register it with the control
plane.

Recorded snapshots are accepted only through an explicit option for tests and
offline troubleshooting:

```sh
uv run --locked python scripts/browser_host.py check --snapshot tests/fixtures/windows-browser-host.json
```

Do not use a recorded snapshot to qualify a real run. Profile changes require a
new version and review because they create a different comparison cohort.
