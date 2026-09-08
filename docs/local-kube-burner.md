# Local kube-burner artifact publication

The kube-burner uploader validates a completed `perfeng-k8s` pod-startup
capture and publishes its three frozen JSON measurements to local SeaweedFS.
It uses the restricted uploader identity, conditional object creation, and
read-back verification shared with browser publication. It never runs the
benchmark, modifies capture files, or assembles normalized results.

## Publish a completed capture

Run the preview first from `perfeng-environment`:

```powershell
uv run --locked python scripts/kube_burner_upload.py `
  ..\perfeng-k8s\.local\results\perf-20260908-120000-a1b2c3d4 `
  --receipt .local\kube-burner-manifest-request.json
```

The preview validates the execution receipt and its files without contacting
Kubernetes or storage. Review the generated uploader Pod, then execute:

```powershell
uv run --locked python scripts/kube_burner_upload.py `
  ..\perfeng-k8s\.local\results\perf-20260908-120000-a1b2c3d4 `
  --receipt .local\kube-burner-manifest-request.json `
  --execute
```

The command accepts only a successful `pod-startup` execution using
release-archive kube-burner provenance, the `pod-startup-smoke` workload, and
contracts 0.13. It requires exactly the job summary, individual pod-latency
observations, and derived quantiles declared by the execution receipt. Changed,
missing, empty, foreign, extra, or symlinked measurement files fail before an
upload starts.

Objects use run-scoped `raw/kubernetes` keys. Each conditional PUT is downloaded
again and checked for checksum, size, media type, artifact ID, and format before
the receipt is written. Existing objects and receipts are never overwritten.
Failed publication removes only its empty local receipt reservation and retains
any created uploader Pod for diagnosis.

The output is the exact request accepted by `perfeng-k8s`'s offline manifest
assembler. Use it with the same unchanged capture directory:

```powershell
Set-Location ..\perfeng-k8s
uv run --locked python -m scripts.manifest `
  .local\results\perf-20260908-120000-a1b2c3d4 `
  ..\perfeng-environment\.local\kube-burner-manifest-request.json `
  .local\raw-result.json
```

The local receipt proves that the uploader verified what it wrote at publication
time. The assembler independently rechecks the frozen local bytes and contract
semantics.

Publish the assembled manifest last at the stable discovery key:

```powershell
Set-Location ..\perfeng-environment
uv run --locked python scripts/raw_result_upload.py `
  ..\perfeng-k8s\.local\raw-result.json `
  --receipt .local\kube-burner-raw-result-upload.json
```

Review the preview and repeat with `--execute`. The publisher accepts only the
reviewed contracts 0.12 and 0.13 raw-result/v2 envelopes, requires unique
run-scoped raw artifact identities and locations, and writes
`runs/<run-id>/raw-result.json` only after all declared evidence is durable.
Publication is conditional and the object is read back before its local receipt
is committed.

Durable production storage, object locking, per-run credentials, registration,
normalization, and retention remain later boundaries.
