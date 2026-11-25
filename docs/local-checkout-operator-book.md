# Perfeng operator book

## TL;DR

- Chapter one follows a real checkout Run on one reviewed local deployment.
- Verify the retained Analysis Report's bytes and policy binding before making
  an SLO decision.
- Keep the deployment and original Run available for the return visit.

## Chapter one: Decide whether checkout met its SLO

### 1. Select the deployment

Use a clean Perfeng Environment checkout with reviewed deployment inputs and
the adjacent `k8s-infrastructure`, `perfeng-k8s` and `perfeng-control-plane`
repositories containing the locked commits. Run commands from the Environment
root. Use Docker with Linux containers and cgroup v2, kind v0.31.0, kubectl,
Helm, Task 3, Python 3.12 and uv. The evidence commands use Bash, curl, jq,
sha256sum and timeout. Install the locked Python dependencies in the reviewed
checkout before the offline checks. This setup can fetch dependencies.

```sh
uv sync --locked
```

Confirm the following access with the deployment owner before proceeding.

- The intended Docker host, the dedicated kubeconfig for `kind-perfeng-local`
  and authorization for any deployment preparation. First-time provisioning
  also requires private repository Contents and GHCR package-read credentials.
  Activation requires approved Argo CD access to inspect and sync the child.
- API access as the smoke principal `local-operator` through the approved
  route. Obtain an approved operator token for that principal. Run and artifact
  metadata reads are principal-scoped.
- Approval and a way to retrieve the exact report object for the new Run.
  Arrange how the Environment owner will authorize or broker that GET once
  the Run ID is known. The pinned Run API provides the Artifact Reference.
  The body requires a separately approved storage read. The AWS CLI v2 example
  below uses an approved S3 endpoint and a read-only profile scoped to the
  exact key.

Start a dedicated Bash session. Run each block in order and stop on errors.
A failed command exits this shell. Use the printed evidence directory when
resuming. Record the Environment revision and
[Deployment Lock](../deployments/local-control-plane.json) SHA-256. Keep the
evidence directory private and outside Git. The validators read exact sibling
Git objects independently of their checked-out branches.

```sh
set +x
set -euo pipefail
umask 077
evidence_dir=$(mktemp -d /tmp/perfeng-checkout.XXXXXXXX)
printf 'Private evidence directory: %s\n' "$evidence_dir"
lock=deployments/local-control-plane.json
environment_revision=$(git rev-parse HEAD)
lock_sha256=$(sha256sum < "$lock" | cut -d ' ' -f 1)
uv run --locked --offline python scripts/infrastructure_release.py validate
uv run --locked --offline python scripts/control_plane_release.py validate
uv run --locked --offline python scripts/gitops.py validate
printf 'Environment revision %s, Deployment Lock SHA-256 %s\n' \
  "$environment_revision" "$lock_sha256"
jq '{controlPlane, deployment}' "$lock"
jq . deployments/local-infrastructure.json
```

Require all three validations to succeed. Read the cluster UID and checkout
policy from the exact runtime bundle selected by the lock.

```sh
revision=$(jq -er '.deployment.revision' "$lock")
bundle=$(jq -er '.deployment.runtimeBundle.path' "$lock")
git -C ../perfeng-k8s show "$revision:$bundle/registry.json" > "$evidence_dir/registry.json"
reviewed_uid=$(jq -er '[.entries[].benchmark.clusterUid | values] | unique
  | if length == 1 then .[0] else error("missing or ambiguous reviewed cluster UID") end' \
  "$evidence_dir/registry.json")
checkout=$(jq -ce '[.entries[] | select(.testId == "checkout-api" and .profile == "smoke")]
  | if length == 1 then .[0] else error("missing or ambiguous checkout binding") end' \
  "$evidence_dir/registry.json")
contracts_version=$(jq -er '.contractsVersion' <<< "$checkout")
policy_path=$(jq -er '.policy' <<< "$checkout")
git -C ../perfeng-k8s show "$revision:$bundle/$policy_path" > "$evidence_dir/policy.json"
policy_hash=$(sha256sum < "$evidence_dir/policy.json" | cut -d ' ' -f 1)
policy_binding=$(jq -ce --arg digest "$policy_hash" \
  '{id: .metadata.name, version: .metadata.version, sha256: $digest}' \
  "$evidence_dir/policy.json")
printf 'Reviewed checkout policy %s\n' "$policy_binding"
```

Compare the Docker context with the deployment owner's selection before
continuing. Check the named cluster and its UID before any deployment change
or Run submission.

```sh
docker context show
```

```sh
timeout 15s kind get clusters | grep -Fx perfeng-local
test -r .local/perfeng-local.kubeconfig
live_uid=$(kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local \
  --request-timeout=10s get namespace kube-system -o jsonpath='{.metadata.uid}')
[[ "$live_uid" == "$reviewed_uid" ]] || {
  printf 'Cluster UID differs from the reviewed selection. Stop here.\n' >&2
  exit 1
}
```

Expect the named cluster and an exact UID match. If Docker is on a different
host, the cluster or kubeconfig is missing, or the UID differs, stop and
preserve the state. Ask the Environment owner for access to the original host
and its dedicated kubeconfig or a replacement selected through
[reviewed qualification and promotion](kubernetes-runtime-deployment.md#fresh-cluster-recovery).
Restart this chapter from the newly reviewed pins after any promotion.

### 2. Bring the selected deployment to readiness

If the selected deployment is already active, go to [Confirm readiness](#confirm-readiness).
For an authorized first install on the verified substrate or a reviewed
upgrade, use this order.

#### Establish the foundation

If the verified substrate already has Argo CD, PostgreSQL, SeaweedFS and the
artifact bucket, run the metrics health check below. If the foundation is
missing, preview the bootstrap commands, then run the Task with the approved
private-source Contents and GHCR package-read tokens. The Task revalidates the
local substrate, creates or verifies the namespaces and external credentials,
reconciles the root and stateful Applications, then initializes and checks S3.
It stops before policy publication and control-plane preparation.

```sh
task --dry bootstrap
```

Execute only if foundation setup is needed and the workspace still selects
the verified cluster. Enter the approved tokens at the hidden prompts.
Existing GitOps and pull credentials are verified and reused by bootstrap.

```sh
(
  read -rsp 'GHCR package-read token: ' PERFENG_REPOSITORIES_READ_TOKEN
  printf '\n'
  read -rsp 'Private repository Contents-read token: ' PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN
  printf '\n'
  export PERFENG_REPOSITORIES_READ_TOKEN PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN
  task bootstrap
)
```

Check the selected metrics ownership, verified TLS and fresh node usage before
preparing the runtime.

```sh
task kubernetes:metrics-health
```

If setup or metrics health fails, preserve the selected substrate and the
failed phase. For missing or stale metrics TLS, have the Environment owner
restore the selected serving credentials and reviewed trust binding. Then
repeat the failed check. [Metrics recovery](local-resource-metrics.md#failure-and-recovery)

#### Prepare the runtime

For an upgrade, stop new submissions, drain active Runs and arrange the
database backup and downtime. Preparation pauses the root and control-plane
Applications, quiesces the API and worker, runs the pinned migration and
prepares immutable runtime inputs. If preparation stops partway, record the
failed phase, leave the Applications paused and resolve that prerequisite
before retrying. [Runtime preparation reference](kubernetes-runtime-deployment.md#prepare-before-activation)

The first two commands are offline previews. Execute preparation after the
foundation and maintenance prerequisites pass.

```sh
uv run --locked --offline python scripts/control_plane_maintenance.py pause
uv run --locked --offline python scripts/execution_registry.py deploy
```

If a pull Secret is missing, supply the approved package-read token in a
short-lived subshell for the Task. Leave the prompt empty only when the
existing pull credentials are in place.

```sh
(
  read -rsp 'GHCR package-read token (empty if already provisioned): ' package_token
  printf '\n'
  if [[ -n "$package_token" ]]; then
    export PERFENG_REPOSITORIES_READ_TOKEN="$package_token"
  fi
  task kubernetes:prepare
)
```

#### Verify and publish policies

Verify the checkout and pod-startup policies against the reviewed bytes
before activating the Control Plane. Export the selected bundle into the
private evidence directory. Run the block once per policy, entering
`checkout-api` and `pod-startup-k8s` in turn. Use the preview to confirm the
reviewed digest and the `policies/<id>/<version>/performance-policy.json`
destination for each test.

```sh
git -C ../perfeng-k8s archive "$revision" "$bundle" | tar -x -C "$evidence_dir"
read -rp 'Reviewed test ID (checkout-api or pod-startup-k8s): ' test_id
[[ "$test_id" == checkout-api || "$test_id" == pod-startup-k8s ]]
publication_path=$(jq -er --arg id "$test_id" \
  '[.entries[] | select(.testId == $id)]
   | if length == 1 then .[0].policy else error("missing or ambiguous policy") end' \
  "$evidence_dir/registry.json")
publication_file="$evidence_dir/$bundle/$publication_path"
publication_hash=$(sha256sum < "$publication_file" | cut -d ' ' -f 1)
publication_id=$(jq -er '.metadata.name' "$publication_file")
publication_version=$(jq -er '.metadata.version' "$publication_file")
policy_publication=(
  "$evidence_dir/$bundle" --policy-id "$publication_id" --version "$publication_version"
  --receipt "$evidence_dir/$publication_id-policy-publication.json"
)
printf 'Reviewed publication SHA-256 %s\n' "$publication_hash"
uv run --locked --offline python scripts/policy_publish.py "${policy_publication[@]}"
```

For an existing key, arrange an approved read of that policy with the owner,
save its original bytes as `published-policy.json` in the private evidence
directory, and require this check to pass.

```sh
[[ $(sha256sum < "$evidence_dir/published-policy.json" | cut -d ' ' -f 1) == "$publication_hash" ]]
```

For a confirmed missing key, execute publication after the preview matches
the reviewed hash. It conditionally creates the key, verifies stored bytes and
writes a private receipt. Stop on unknown object state, digest mismatch or a
failed publication.

```sh
uv run --locked --offline python scripts/policy_publish.py "${policy_publication[@]}" \
  --expected-sha256 "$publication_hash" --execute
```

#### Activate the selected runtime

Once the reviewed deployment inputs are on remote `main`, resume the root
and wait for the benchmark-authority Application to become Synced.

```sh
uv run --locked --offline python scripts/gitops.py bootstrap
uv run --locked --offline python scripts/gitops.py bootstrap --execute
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local \
  --request-timeout=30s -n argocd wait application/perfeng-local-benchmark-authority \
  --for=jsonpath='{.status.sync.status}'=Synced --timeout=180s
```

Open the authorized Argo CD UI for this deployment, refresh
`perfeng-local-control-plane` and inspect its complete diff. Require both
child source pins, the Control Plane image and the two immutable ConfigMaps
to match the lock. Stop on unexpected Secret or persistent-resource changes.
Manually sync the complete child Application with Prune, Force and Replace
off and automatic sync disabled. Argo CD owns workload reconciliation.
If the authority wait or child sync fails, preserve the prepared state and
inspect the failure before changing anything else.

#### Confirm readiness

Run health checks after sync or on the already active deployment. Require
Synced and Healthy GitOps Applications, a ready Control Plane at the locked
image digest, working operational endpoints and healthy execution pull access.
GitOps health can create its bounded bucket-access probe Job and request a
normal Application refresh. Stop on a failed health check and record the
unready Application, endpoint or credential check. [Troubleshooting](troubleshooting.md)

```sh
uv run --locked --offline python scripts/gitops.py health
uv run --locked --offline python scripts/execution_registry.py health --execute
uv run --locked --offline python scripts/gitops.py health --execute
uv run --locked --offline python scripts/control_plane_health.py --execute
```

### 3. Submit one real checkout Run

Preview the authenticated checkout smoke. It submits `checkout-api` with
profile `smoke` against the local sample SUT. The pinned k6 workload uses a
120-second measurement phase. The launcher binds the exact runtime and policy
bytes and supplies the operator credential to its restricted client by Secret
reference. [Smoke command reference](local-control-plane.md#authenticated-k6-to-analysis-smoke-run)

```sh
uv run --locked --offline python scripts/control_plane_smoke.py
```

Expect the preview to say it would submit one locked `checkout-api` Run.
After readiness and access checks pass, record the start time and execute once.

```sh
date -u +'%Y-%m-%dT%H:%M:%SZ' > "$evidence_dir/submission-started-at"
uv run --locked --offline python scripts/control_plane_smoke.py --execute |
  tee "$evidence_dir/submission.log"
```

Expect `Completed Run <run-id> with 5 verified artifacts.` and a retained
client Pod name. The client polls the Run for up to 1,100 seconds, then
checks the five registered artifacts. Record the Run ID from that line.
`COMPLETED` is the lifecycle state. The SLO decision comes from step 5.

If the command fails, keep any Run ID and the retained client or Jobs. Use
the [retained smoke client diagnostics](troubleshooting.md#the-end-to-end-smoke-run-fails)
and, if the Run ID is available, the Run read below to determine the terminal
state. If the client never reported an ID, give the owner the Pod name and
UTC submission time to investigate the original request. Stop the decision
path on a failed, timed-out or unconfirmed Run. Each smoke invocation can
create a separate Run.

### 4. Verify the exact Analysis Report

Keep the evidence below in a private directory outside Git. Use the selected
deployment's approved operator access to save the exact JSON bodies for
`GET /v1/runs/<run-id>` and `GET /v1/runs/<run-id>/artifacts` as `run.json` and
`artifacts.json`. Both must return HTTP 200. For example, where cluster access
permits a loopback API port-forward, run this in a separate terminal. Give it
up to 30 seconds to find a running Pod. The forward stops after five minutes.

```sh
timeout --foreground 5m kubectl --kubeconfig .local/perfeng-local.kubeconfig \
  --context kind-perfeng-local --namespace perf-platform port-forward \
  --pod-running-timeout=30s --address 127.0.0.1 service/control-plane 18080:8080
```

Wait for this running command to print `Forwarding from 127.0.0.1:18080 -> 8080`
before sending a credential. Stop if the command exits or reports a bind
failure. Leave the forward running while fetching both responses.

In the dedicated Bash session, enter the recorded Run ID and the approved
`local-operator` token. The token is read in a short-lived subshell and passed
to curl through stdin. If your deployment uses an equivalent approved API
route, save the same two responses through that route.

```sh
read -rp 'Recorded checkout Run ID: ' run_id
[[ "$run_id" =~ ^perf-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$ ]]
(
  read -rsp 'Approved local-operator token: ' operator_token
  printf '\n'
  for resource in run artifacts; do
    path="/v1/runs/$run_id"
    if [[ "$resource" == artifacts ]]; then path="$path/artifacts"; fi
    status=$(printf 'Authorization: Bearer %s\n' "$operator_token" |
      curl -q --noproxy 127.0.0.1 --silent --show-error --fail \
        --max-time 15 --max-filesize 1048576 --header @- \
        --output "$evidence_dir/$resource.json" --write-out '%{http_code}' \
        "http://127.0.0.1:18080$path")
    [[ "$status" == 200 ]]
  done
)
```

Stop the loopback port-forward once the two reads finish or if either fails.
Continue in the same Bash session after successful reads. Console Run Detail
can help inspect the Run and its Artifact References when available. For HTTP
401, 403 or 404, stop and ask the owner to confirm the Run ID and principal
access. Keep the deployment and record the status code in the private record.

Compare the Run with the policy binding recorded in step 1 and select the
report registration.

```sh
jq -e --arg id "$run_id" --argjson policy "$policy_binding" \
  '.id == $id and .state == "COMPLETED" and .request.testSuite == "checkout-api"
   and .request.profile == "smoke" and .request.policy == $policy' \
  "$evidence_dir/run.json" > /dev/null
jq -e --arg id "$run_id" \
  '[.artifacts[] | select(.format == "analysis-result/v1")]
   | if length == 1 then .[0] else error("report registration missing or ambiguous") end
   | select(.runId == $id and .kind == "normalized" and .mediaType == "application/json"
       and (.id | test("^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$"))
       and .uri == ("s3://perfeng-artifacts/runs/" + $id + "/analysis-result.json")
       and (.sha256 | test("^[0-9a-f]{64}$"))
       and (.sizeBytes | type == "number" and . == floor and . > 0 and . <= 1048576))' \
  "$evidence_dir/artifacts.json" > "$evidence_dir/report-reference.json"
```

Expect the completed checkout Run with the selected policy binding, and one
report reference for its canonical bucket and key. Stop on a missing, foreign
or ambiguous registration. Give the validated Run ID and object key to the
owner if approval is granted per Run.

Use the approved route to save the original bytes as `report.json`. The AWS CLI
example reads a bounded range with the approved exact-key profile. Enter the
endpoint and profile name supplied by the owner. For a brokered read, save its
original bytes under the same filename and run the size and digest checks.

```sh
claim="$evidence_dir/report-reference.json"
uri=$(jq -er '.uri' "$claim")
size=$(jq -er '.sizeBytes' "$claim")
digest=$(jq -er '.sha256' "$claim")
```

```sh
read -rp 'Approved S3 endpoint: ' approved_endpoint
read -rp 'Approved read-only AWS profile: ' approved_profile
[[ -n "$approved_endpoint" && -n "$approved_profile" ]]
AWS_MAX_ATTEMPTS=1 timeout --foreground 30s aws \
  --no-cli-pager --profile "$approved_profile" --endpoint-url "$approved_endpoint" \
  --cli-connect-timeout 5 --cli-read-timeout 15 s3api get-object \
  --bucket perfeng-artifacts --key "${uri#s3://perfeng-artifacts/}" \
  --range "bytes=0-$size" "$evidence_dir/report.json" > /dev/null
```

```sh
[[ $(wc -c < "$evidence_dir/report.json") -eq "$size" ]]
[[ $(sha256sum < "$evidence_dir/report.json" | cut -d ' ' -f 1) == "$digest" ]]
```

The inclusive range requests up to one byte more than the registration to
detect extra bytes. Both checks must succeed. On a missing or denied object,
failed registration check, or byte mismatch, stop and record the Run ID,
selection and exact failing check for the Environment owner. Preserve the
deployment and the original evidence. Keep storage bootstrap and runtime
credentials in their owning workloads.

### 5. Decide and record

Check the verified report against the Run ID, test ID, selected contracts
version and reviewed policy. Use the matching rule's quality, verdict and
measured value. The command also checks the verdict against the inclusive
maximum before computing the decision.

```sh
jq -es --arg id "$run_id" --arg contracts "$contracts_version" \
  --argjson policy "$policy_binding" --slurpfile source "$evidence_dir/policy.json" '
  select(length == 1) | .[0]
  | select(.kind == "AnalysisResult" and .schemaVersion == 1
    and .runId == $id and .testId == "checkout-api" and .contractsVersion == $contracts
    and .blocking == false and .policy == ($policy + {mode: $source[0].spec.mode}))
  | .evaluations
  | select(length == 1)
  | .[0] as $evaluation
  | $source[0].spec.rules[0] as $rule
  | select($evaluation.ruleId == $rule.id and $evaluation.metric == $rule.metric)
  | {quality: $evaluation.quality.status, slo: $evaluation.slo.status,
     p95Ms: $evaluation.slo.value, maxMs: $rule.slo.max}
  | . + {decision:
      if .quality != "PASS" or (.p95Ms | type) != "number" then "UNRESOLVED"
      elif .slo == "PASS" and .p95Ms <= .maxMs then "MET"
      elif .slo == "FAIL" and .p95Ms > .maxMs then "EXCEEDED"
      else "UNRESOLVED" end}
  ' "$evidence_dir/report.json"
```

The current checkout smoke uses policy `checkout-api` version `1.0.0` in
observe mode. The rule checks `api.http.duration` p95 in milliseconds against a
maximum of 500 ms. A Run can complete in observe mode with either SLO verdict.
`MET` means checkout met the maximum for this Run. `EXCEEDED` means checkout
exceeded it and warrants investigation. Missing data, unsuitable quality or
an undecidable verdict yields `UNRESOLVED`. A failed identity or rule check
also leaves the decision unresolved. Record the failing check and preserve
the evidence. The policy has no baseline regression rule. The local sample
SUT result applies to this Run on the selected deployment.

### 6. Leave a return checkpoint

Record the first pass in a sanitized form suitable for an authorized
reviewer. Store the private record durably outside Git so it survives logout
or cleanup of the temporary evidence directory.

| Field | Record |
|:--|:--|
| Reviewed selection | Environment revision, Deployment Lock SHA-256, infrastructure revision, Control Plane source and image digest, chart and deployment commit, bundle and package pins |
| Deployment identity | Dedicated context and live cluster UID |
| Execution | UTC submission time, first Run ID, terminal state and optional tool exit code |
| Reviewed policy | ID, version, SHA-256, mode, rule and threshold |
| Verified report | Artifact ID, verified SHA-256 and byte count, Run and policy linkage |
| Decision | Evidence quality, reported SLO status, p95 when available and resulting decision or gap |
| Return visit | Private record and deployment locations, approved principal and artifact read route, outstanding owner follow-ups |

Keep the cluster, GitOps deployment, persistent storage, Run and report
available. End temporary port-forwards and close the shell after saving the
record. Keep credentials, kubeconfigs, generated receipts and report bodies
private. On return, use the recorded selection and the same approved operator
identity, check the cluster UID and readiness again, and keep the first Run ID
and verified digest for the retention check. [Return visit](https://github.com/stanimirivanov/perfeng-environment/issues/408)
