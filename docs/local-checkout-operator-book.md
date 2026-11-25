# Perfeng operator book

## TL;DR

Use this chapter on the retained local deployment. Match the owner's selection,
submit one checkout Run, and verify its registered report before recording the
SLO observation. Use the same checkpoint to resume an interrupted attempt.
The Environment owner handles provisioning, migration and activation.

## 1. Identify the deployment and the people who can grant access

Start with the Environment maintainer responsible for the deployment. Ask for
the team's private handoff for **local checkout**, indexed by the Deployment
Lock digest. The maintainer records its durable location and access method in
the team's approved private channel and gives them to the second engineer.
If the entry is missing, the maintainer creates it before either engineer
submits. The handoff needs these fields:

| Field | Supplied by | Used for |
|---|---|---|
| Environment revision, lock SHA-256, cluster UID, selected Docker host and context | Environment owner | Choosing the retained deployment and detecting replacement |
| Operator principal, credential issuer, renewal route, private delivery method | API credential owner | Authenticated submission, Run and artifact reads as the same principal |
| Approved exact-object read or owner-brokered delivery, accountable owner and backup | Storage owner | Receiving the original bytes for the registered report only |
| Durable private checkpoint location, retention review date, accountable owner and backup | Operator and Environment owner | Resuming the original attempt and releasing it after the return visit |

The infrastructure repository owns the **substrate** (the named kind cluster).
Argo CD reconciles the **foundation** (including PostgreSQL and SeaweedFS) and
the control-plane **child Application** from the Environment **selection** (the
release pins and Deployment Lock). The benchmark environment **descriptor**
records a qualified cluster incarnation. The Control Plane accepts the Run and
records its artifact registrations in PostgreSQL. k6 measures the sample
checkout service, Analysis evaluates the reviewed policy, and SeaweedFS retains
the resulting report bytes. The API returns the registration used to identify
that object. [Domain glossary](../GLOSSARY.md)

Use a clean checkout at the handoff revision with adjacent
`k8s-infrastructure`, `perfeng-k8s`, `perfeng-contracts` and
`perfeng-control-plane` repositories containing the selected Git objects, including the checkout
Contracts 0.13.0 revision `badd9206a88a48042dda54a25c6e362088d23858`.
Install the locked Python 3.12 environment with `uv sync --locked`.
The owner-assisted path uses the dedicated kubeconfig at
`.local/perfeng-local.kubeconfig`, context `kind-perfeng-local`, Docker
with Linux containers and cgroup v2, kind v0.31.0 and kubectl. Use it in the
approved operator environment. Keep administrator credentials with their owner.

The qualified profile has three Linux AMD64 nodes and requires at least
500 millicores and 1 GiB allocatable per node. Record available host CPU,
memory and free disk in the private handoff before a live pass. These are
per-node qualification floors; the host also runs Docker and the stateful
services. Record actual start and end times for readiness, submission,
analysis and retrieval. The k6 measurement phase is 120 seconds. A command
timeout is a failure boundary, not a measured phase duration.
[Qualification profile](kubernetes-benchmark-qualification.md#ownership-and-profile)

## 2. Check selection and readiness

Use Bash from the Environment root. At the prompts enter the digest and UID
from the **private owner handoff**. Use the same values after a shell restart.

```sh
read -rp 'Owner-approved Deployment Lock SHA-256: ' approved_lock
read -rp 'Owner-approved cluster UID: ' approved_uid
uv run --locked --offline python -m scripts.checkout_operator preview \
  --lock-sha256 "$approved_lock" --cluster-uid "$approved_uid"
```

Preview reads reviewed inputs offline. Run the live identity check on the
owner-selected host before a deployment action or submission.

```sh
docker context show
uv run --locked --offline python -m scripts.checkout_operator check --execute \
  --lock-sha256 "$approved_lock" --cluster-uid "$approved_uid"
```

Expect `Verified retained deployment selection.` Stop on a different Docker
context, unavailable cluster or kubeconfig, unready API, lock mismatch or changed UID. Give the owner the failing check through
the private handoff. Keep the existing checkpoint and deployment. After the
owner restores access, rerun this check against the same approved values.
A replacement gets a new reviewed selection. See the
[qualification path](kubernetes-benchmark-qualification.md#preview-and-collect).

The Environment owner confirms the maintenance window is closed, the child is
Synced and Healthy, and the runtime is ready at the locked image. The operator
resumes here after any maintenance. The owner runs the control-plane health command below in the authorized
maintenance context. It verifies protected credentials and database grants.
The operator command checks workload identity, readiness and operational
endpoints without reading those Secrets. Other checks have additional
effects: GitOps health can refresh Applications and create a bucket-probe Job.
Run those checks only through the authorized owner procedure.

```sh
uv run --locked --offline python scripts/control_plane_health.py --execute
```

On an unready Application, storage error or active maintenance, give the
Environment owner the child name and failing phase. The
[cutover procedure](kubernetes-runtime-deployment.md#prepare-before-activation)
covers drain, migration, runtime preparation and manual activation. The
[GitOps guide](local-gitops.md#health-and-inspection) identifies health and log
locations. Recheck the selection and readiness when the owner releases the
deployment. No new installation is required for a healthy retained deployment.

### Owner handoff for missing or unverified policies

The owner checks both policies before activation. From the approved revision,
choose a fresh private directory outside Git. Keep it for receipts. Preview
both pinned IDs, versions, object locations and digests.

```sh
read -rp 'New private policy directory: ' policy_dir
mkdir -m 700 -- "$policy_dir"
uv run --locked --offline python -m scripts.checkout_policies \
  --lock-sha256 "$approved_lock" --cluster-uid "$approved_uid" \
  --evidence-dir "$policy_dir"
```

For each existing key, obtain its original bytes through the authorized route
and save them separately as `checkout-api.published.json` and
`pod-startup-k8s.published.json` with mode 0600 in that directory. For a key the owner has
confirmed missing, add `--publish-missing checkout-api` or
`--publish-missing pod-startup-k8s` to the following command for that key.
This authorizes conditional creation and readback on the verified deployment.

```sh
uv run --locked --offline python -m scripts.checkout_policies \
  --lock-sha256 "$approved_lock" --cluster-uid "$approved_uid" \
  --evidence-dir "$policy_dir" --execute
```

Require a successful exit and one receipt for **each** test ID. An interrupted
owner operation may have created its first object. Keep the evidence, check
both exact keys, and verify existing bytes in a fresh evidence directory
before considering activation. If the owner must activate a prepared runtime,
use the [manual sync procedure](kubernetes-runtime-deployment.md#activate-after-merge)
and return to the selection and readiness checks.

## 3. Submit once or resume the existing attempt

Choose a new checkpoint directory under a durable private parent outside Git
that will survive a reboot. The submit command creates the directory. Keep all credentials outside the
checkpoint and use the approved private delivery route each time. Use
owner-only regular files (mode 0600) for the credential and delivered report.

```sh
read -rp 'Durable private checkpoint directory: ' checkpoint
read -rp 'Private operator credential file: ' token_file
```

Before submission confirm that the storage owner can fulfill a GET of the
registered report for this Run. The bootstrap provisions an administrator and
workload uploader; those credentials stay in their owning workloads.

```sh
uv run --locked --offline python -m scripts.checkout_operator submit --execute \
  --lock-sha256 "$approved_lock" --cluster-uid "$approved_uid" \
  --checkpoint-dir "$checkpoint" --token-file "$token_file"
```

The checkpoint records the request and its idempotency key before submission.
Expect `Acknowledged checkout Run <run-id>.` followed on completion by
`Awaiting approved original bytes for <registered URI>.` On timeout, interruption
or denied access, reopen this book, restore the variables from the private handoff and use **resume**. Repeating
the submit command is never a recovery step.

```sh
uv run --locked --offline python -m scripts.checkout_operator resume --execute \
  --lock-sha256 "$approved_lock" --cluster-uid "$approved_uid" \
  --checkpoint-dir "$checkpoint" --token-file "$token_file"
```

If the acknowledgement was lost, the owner must independently identify the
original request before attaching a Run. Use the additional resume arguments
`--original-run-id` and `--original-run-source` to record the confirmed ID and
its source. Supply a private owner evidence JSON file for the source with
`requestKey`, `principal`, `runId` and `source` (the private audit locator).
The owner establishes this mapping from the original acceptance. The
authenticated Run must match the recorded request. Similar
timestamps, principal and profile alone cannot distinguish two identical
submissions. Leave an ambiguous attempt unresolved. A deliberate new attempt
uses a new checkpoint. On a failed terminal Run, retain the ID and use the
[Run diagnostics](troubleshooting.md#the-end-to-end-smoke-run-fails).

## 4. Verify the exact Analysis Report

After completion, the command saves the authenticated Run and artifact
registrations as `run.json` and `artifacts.json`. The verified report reference
is available under `reportRegistration` in `checkpoint.json`. Give the storage owner the validated
report reference from this Run. Use the exact registered bucket and key through the approved reader,
or have the owner deliver the original bytes privately. Record the fulfiller
and UTC fulfillment time (for example, an ISO timestamp ending in `Z`).
Keep an incoming file separate from the checkpoint's
verified evidence. On HTTP denial or missing bytes, leave the Run in place,
restore the approved access and resume the same checkpoint.

For a direct read, use the endpoint and exact-key AWS CLI v2 profile supplied
privately by the owner. This example uses the already authenticated checkpoint,
a new owner-only file and a range that includes one extra byte to detect an
oversized object. Stop on any failed command. For brokered delivery, skip this
block and use the file supplied by the owner.

```sh
read -rp 'Approved S3 endpoint: ' endpoint
read -rp 'Approved exact-key profile: ' profile
uri=$(jq -er '.reportRegistration.uri' "$checkpoint/checkpoint.json")
size=$(jq -er '.reportRegistration.sizeBytes' "$checkpoint/checkpoint.json")
report=$(mktemp "$(dirname -- "$checkpoint")/checkout-report.XXXXXXXX")
AWS_MAX_ATTEMPTS=1 timeout 30s aws --no-cli-pager --profile "$profile" \
  --endpoint-url "$endpoint" --cli-connect-timeout 5 --cli-read-timeout 15 \
  s3api get-object --bucket perfeng-artifacts --key "${uri#s3://perfeng-artifacts/}" \
  --range "bytes=0-$size" "$report" > /dev/null
printf 'Downloaded report file: %s\n' "$report"
```

```sh
read -rp 'Delivered original report file: ' report
read -rp 'Private fulfillment attribution: ' fulfilled_by
read -rp 'UTC fulfillment time: ' fulfilled_at
uv run --locked --offline python -m scripts.checkout_operator resume --execute \
  --lock-sha256 "$approved_lock" --cluster-uid "$approved_uid" \
  --checkpoint-dir "$checkpoint" --token-file "$token_file" \
  --report-file "$report" --fulfilled-by "$fulfilled_by" --fulfilled-at "$fulfilled_at"
```

Compare the registered length and SHA-256 with the exact bytes before
interpreting the result. The verifier checks the selected report contract,
Run, candidate registration, policy identity and full rule coverage. It finds
the checkout rule by ID and metric selector. A foreign candidate, malformed
report, missing registration or changed bytes leaves the attempt without an
SLO verdict. Preserve the failing check for the owner.

## 5. Record the observation and leave the deployment available

For the current checkout policy, a quality-supported p95 at or below 500 ms
meets the maximum; a p95 above 500 ms exceeds it. The policy runs in observe
mode. A completed Run can yield either observation. Valid but insufficient
measurement evidence gives an unresolved observation. Record the reported
quality and reason. This decision describes one Run of the local sample SUT.

Expect `Verified checkout decision: <verdict>.` The checkpoint's `decision`
records the rule, metric, quality, SLO status, measured p95 and maximum.
Keep the private checkpoint, original report, registration and deployment
through the [return visit](https://github.com/stanimirivanov/perfeng-environment/issues/408)
and explicit owner release. The sanitized first-pass record needs the
Environment revision, lock digest, cluster UID and immutable pins, Run ID and
timestamps, policy ID/version/digest, artifact ID/size/digest, quality, rule,
reported SLO value and verdict, decision and limitations. Record the checkpoint
location, accountable owner, backup and retention review date privately.
Publish only sanitized acceptance facts. Record missing live proof as a gap.
