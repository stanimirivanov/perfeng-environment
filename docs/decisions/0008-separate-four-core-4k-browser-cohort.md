# ADR-0008: Separate the four-core 4K browser cohort

- Status: Proposed
- Date: 2026-10-12
- Milestone: M03 — Native Browser Decision Loop
- Deciders: Repository owner

## TL;DR

- Add `windows-four-core-4k/1.0.0` as an explicitly selected laptop profile.
- Preserve `windows-mainstream/1.0.0`, its fingerprint and the default selection.
- Change CPU capacity and display conditions only; retain OS and calibration gates.
- Require fresh live qualification and matching measurement identity before promotion.

## Context

The available physical Windows laptop reports four available logical cores and
a 3840×2160 display at scale factor 1 and integer refresh rate 59 Hz. These
conditions differ materially from the existing eight-core, 1080p reference.
The owner chose a separate laptop profile rather than rewriting that reference.
The captured OS build and background CPU also fail independent gates; changing
hardware cohorts must not disguise those failures.

## Decision

Add `profiles/browser/windows-four-core-4k.yaml` with identity
`windows-four-core-4k/1.0.0`, minimum four available logical cores and exactly
the observed display tuple. The 59 Hz value describes the collector's integer
observation; do not broaden it to 60 Hz or reinterpret it as a precise physical
refresh measurement. Confirm display resolution/scaling when qualifying a host.

Keep the existing OS build floor 22631, minimum total/available memory of
16/8 GiB, hardware acceleration, headed Chromium Chrome for Testing, physical
Windows x64 host, AC/Balanced power, clock synchronization and maximum 10%
background CPU. These are controlled reference conditions, not an estimate of
the average customer laptop. CPU/memory requirements are minimums, not exact
hardware identity; actual observations remain in environment evidence.

Selection is explicit through the existing `--profile` options. Host reports,
Playwright configuration and assembled environment evidence must name the same
profile/version and canonical fingerprint. No public schema changes are needed.

## Alternatives considered

- Use a host matching the original profile: preserves one cohort, but excludes
  the available laptop; the owner selected a separate cohort instead.
- Relax or rewrite the original profile: easier locally, but changes comparison
  meaning under an existing identity and is rejected.
- Lower the OS floor or raise background CPU limits: could remove remaining
  rejection reasons, but weakens independent acceptance criteria and is excluded.

## Consequences

The available hardware can be represented without changing earlier evidence.
A second cohort requires explicit selection and its own comparable baselines;
it must not be mixed with the mainstream cohort. Changing any profile condition
after review requires a new version and review, not an in-place rewrite.

## Compatibility and migration

The mainstream profile remains the CLI default and byte-for-byte unchanged.
Existing reports, worker configuration, baselines, deployment selections and
runtime bundles remain unchanged. Promotion must select fresh evidence for the
new cohort; there is no conversion of earlier rejected reports.

## Security and operations

Validation is offline. Qualification reads host state and never changes OS,
power, display or calibration settings. Reports stay outside Git and output
files are never overwritten. Rollback selects the previous profile for future
runs without deleting or relabeling existing evidence. No host update, trust
installation, credential rotation or deployment is authorized by this decision.

## Validation

`make validate` validates both profiles and tests qualification success,
unchanged OS/calibration rejection, capacity/display drift and cross-cohort
evidence rejection. Before live acceptance, run a fresh host check with the
explicit laptop profile, then assemble real measurement evidence using that
same selection. Offline fixtures do not qualify a real run.
