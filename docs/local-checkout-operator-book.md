# Perfeng operator book

## TL;DR

- Chapter one follows a real checkout Run on one reviewed local deployment.
- Verify the retained Analysis Report's bytes and policy binding before making
  an SLO decision.
- Keep the deployment and original Run available for the return visit.

## Chapter one: Decide whether checkout met its SLO

### 1. Select the deployment

Work from a clean, reviewed Perfeng Environment checkout with sibling
`perfeng-k8s` and `perfeng-control-plane` repositories containing the pinned
commits. Use the [local Kubernetes prerequisites](local-kubernetes.md) and the
[M04 runtime prerequisites](kubernetes-runtime-deployment.md#selection-and-prerequisites)
for host tools, private image access and the dedicated kind context. The report
steps below use Bash, curl, jq, sha256sum and timeout. Arrange scoped operator API access
and approved exact-object read access for the same deployment before submitting.
The Run and artifact API reads are scoped to the authenticated principal. Have
the deployment owner confirm access as the smoke operator principal, or through
a reviewed equivalent authorized route. The AWS CLI example requires an
approved S3 endpoint and read profile.

Validate the [Deployment Lock](../deployments/local-control-plane.json) offline.
Record the Environment revision, the locked control-plane image and source
revision, the deployment commit and chart, and the runtime bundle and package.

```sh
uv run --locked --offline python scripts/control_plane_release.py validate
uv run --locked --offline python scripts/gitops.py validate
```

Both checks must succeed. The release validator reads the exact sibling Git
objects, so a sibling checkout at a different branch does not change the
selection.

Confirm Docker is on the intended host and that the named cluster exists. With
the dedicated kubeconfig, read the live cluster identity.

```sh
kind get clusters
kubectl --kubeconfig .local/perfeng-local.kubeconfig --context kind-perfeng-local \
  --request-timeout=10s get namespace kube-system -o jsonpath='{.metadata.uid}'
```

Compare the UID with the **reviewed descriptor** referenced in the runtime
prerequisites. If the cluster or dedicated kubeconfig is absent, follow the
[staged foundation bootstrap](local-gitops.md#fresh-cluster-staged-bootstrap)
only for an explicitly selected new local substrate. A new cluster has a new
UID. Before preparing the runtime or submitting a Run, obtain the new
qualification and reviewed bundle promotion required by the
[runtime guide](kubernetes-runtime-deployment.md#fresh-cluster-recovery).
On a mismatch, stop and preserve both the deployment and the evidence. A copied
kubeconfig, edited UID or recreated cluster cannot recover the original selection.

From the Environment checkout, start a dedicated Bash session for the
following steps. Keep a private evidence directory outside Git, and read the
checkout policy bytes from the reviewed commit.

```sh
set +x
set -euo pipefail
umask 077
evidence_dir=$(mktemp -d /tmp/perfeng-checkout.XXXXXXXX)
lock=deployments/local-control-plane.json
revision=$(jq -er '.deployment.revision' "$lock")
bundle=$(jq -er '.deployment.runtimeBundle.path' "$lock")
policy_path=$(git -C ../perfeng-k8s show "$revision:$bundle/registry.json" |
  jq -er '[.entries[] | select(.testId == "checkout-api" and .profile == "smoke")]
          | if length == 1 then .[0].policy else error("ambiguous checkout binding") end')
git -C ../perfeng-k8s show "$revision:$bundle/$policy_path" > "$evidence_dir/policy.json"
policy_hash=$(sha256sum < "$evidence_dir/policy.json" | cut -d ' ' -f 1)
policy_binding=$(jq -ce --arg digest "$policy_hash" \
  '{id: .metadata.name, version: .metadata.version, sha256: $digest}' \
  "$evidence_dir/policy.json")
```

### 2. Bring the selected deployment to readiness

If the selected deployment is already reconciled, start with the health checks
below. For a first install or reviewed upgrade, follow the existing authority order.

1. Confirm the [staged foundation](local-gitops.md#fresh-cluster-staged-bootstrap)
   has healthy PostgreSQL, SeaweedFS, runtime prerequisites and verified
   resource metrics.
2. Use [runtime preparation](kubernetes-runtime-deployment.md#prepare-before-activation)
   for pull credentials, bounded maintenance and migration, and immutable
   runtime inputs. Existing owned credentials and resources are reused.
3. Ensure the exact checkout policy is published. Use the
   [policy publication procedure](policy-publication.md#checkout-smoke-policy)
   with the bytes from the **selected** bundle commit. The worked example there
   uses the older 1.0.0 bundle. For a missing object, preview publication from a
   clean export of the selected bundle, compare the policy digest from step 1,
   then publish using the digest guard. For an existing policy, verify the exact
   retained object instead of publishing again.
4. Follow [manual activation](kubernetes-runtime-deployment.md#activate-after-merge)
   from reviewed `main`. Reconcile the root, review both child source pins and
   the ConfigMaps, and manually sync the control-plane Application with Prune,
   Force and Replace off. Continue with the health checks below after sync.
   The smoke invocation in step 3 is the single checkout submission. The
   separate Kubernetes benchmark qualification belongs to M04.

Use read-only health checks before submitting.

```sh
uv run --locked --offline python scripts/gitops.py health
uv run --locked --offline python scripts/execution_registry.py health --execute
uv run --locked --offline python scripts/gitops.py health --execute
uv run --locked --offline python scripts/control_plane_health.py --execute
```

Require healthy, synced GitOps Applications, the ready locked control-plane
workload and responsive operational endpoints. Stop on a failed prerequisite
and use the [local troubleshooting runbook](troubleshooting.md). Health checks
do not repair the deployment.

### 3. Submit one real checkout Run

Preview and then explicitly execute the
[authenticated checkout smoke](local-control-plane.md#authenticated-k6-to-analysis-smoke-run).
The launcher binds the checkout request to the locked runtime and policy
bytes and supplies the operator credential to its restricted client by
Secret reference.

```sh
uv run --locked --offline python scripts/control_plane_smoke.py
uv run --locked --offline python scripts/control_plane_smoke.py --execute
```

Record the Run ID from the completion line and the time in UTC. Completion means
the client saw `COMPLETED` and the five expected artifact registrations. The
printed artifact count covers registrations. Step 4 verifies the actual
Analysis Report. A tool exit code is execution evidence, separate from the
report's SLO verdict.

If the command fails, keep any Run ID and the retained client or Jobs. Use
[smoke diagnostics](troubleshooting.md#the-end-to-end-smoke-run-fails) and operator
reads to establish whether the original request was accepted. A new invocation
creates a new Run. Avoid resubmitting while the first outcome is unknown.

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

In the dedicated Bash session, set `run_id` to the recorded ID. Supply the
approved read token for the smoke operator principal through the hidden prompt.
If your deployment provides a different approved API access path, use it to
obtain the same two responses.

```sh
[[ "$run_id" =~ ^perf-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$ ]]
read -rsp 'Approved operator read token: ' operator_token
printf '\n'
for resource in run artifacts; do
  path="/v1/runs/$run_id"
  if [[ "$resource" == artifacts ]]; then path="$path/artifacts"; fi
  status=$(printf 'Authorization: Bearer %s\n' "$operator_token" |
    curl -q --noproxy 127.0.0.1 --silent --show-error --fail --max-time 15 --max-filesize 1048576 \
      --header @- --output "$evidence_dir/$resource.json" --write-out '%{http_code}' \
      "http://127.0.0.1:18080$path")
  [[ "$status" == 200 ]]
done
unset operator_token
```

Stop the loopback port-forward once the two reads finish or if either fails.
Continue in the same Bash session after successful reads. Console Run Detail
can help locate the Run if available. For HTTP 403 or 404, confirm the
Run ID and authorized principal with the deployment owner before concluding
the Run is missing.

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
       and .uri == ("s3://perfeng-artifacts/runs/" + $id + "/analysis-result.json")
       and (.sha256 | test("^[0-9a-f]{64}$"))
       and (.sizeBytes | type == "number" and . == floor and . > 0 and . <= 1048576))' \
  "$evidence_dir/artifacts.json" > "$evidence_dir/report-reference.json"
```

Use the registered URI with the deployment's approved exact-object GET. For
an approved S3 endpoint and read profile, the following reads a bounded range
for that object. Set `approved_endpoint` and `approved_profile` using your
operator access instructions.

```sh
claim="$evidence_dir/report-reference.json"
uri=$(jq -er '.uri' "$claim")
size=$(jq -er '.sizeBytes' "$claim")
digest=$(jq -er '.sha256' "$claim")
aws --profile "$approved_profile" --endpoint-url "$approved_endpoint" \
  --cli-connect-timeout 5 --cli-read-timeout 15 s3api get-object \
  --bucket perfeng-artifacts --key "${uri#s3://perfeng-artifacts/}" \
  --range "bytes=0-$size" "$evidence_dir/report.json" > /dev/null
[[ $(wc -c < "$evidence_dir/report.json") -eq "$size" ]]
[[ $(sha256sum < "$evidence_dir/report.json" | cut -d ' ' -f 1) == "$digest" ]]
```

The inclusive range requests one byte more than the registration, so an
oversized object causes the length check to fail. If
the deployment has no approved exact-object read for this operator, stop and
record the deployment selection, Run ID and the missing capability for the
Environment owner. Do the same for denied or missing objects, unexpected
registration or mismatched bytes. Preserve the deployment and evidence. Use
only the approved access path and leave runtime and bootstrap storage
credentials in their owning workloads.

### 5. Decide and record

After verifying the exact bytes, compare the report Run ID and test ID with the
Run, and compare its policy ID, version, digest and mode with the reviewed
policy. Extract only the matching evaluation's quality and SLO fields.

```sh
jq -e --arg id "$run_id" --argjson policy "$policy_binding" \
  --slurpfile source "$evidence_dir/policy.json" '
  select(.kind == "AnalysisResult" and .schemaVersion == 1
    and .runId == $id and .testId == "checkout-api"
    and .policy == ($policy + {mode: $source[0].spec.mode}))
  | .evaluations
  | select(length == 1)
  | .[0] as $evaluation
  | $source[0].spec.rules[0] as $rule
  | select($evaluation.ruleId == $rule.id and $evaluation.metric == $rule.metric)
  | {quality: $evaluation.quality.status, slo: $evaluation.slo.status,
     value: $evaluation.slo.value, limit: $rule.slo.max}
  ' "$evidence_dir/report.json"
```

The current reviewed checkout-smoke binding selects policy `checkout-api`
version `1.0.0` in observe mode. It checks `api.http.duration` p95 in milliseconds
against a maximum of 500 ms. Use the report's verdict only when quality is
`PASS` and the measured value and verdict agree with the reviewed threshold.
A passing SLO means checkout met the limit for this Run. A failing SLO means
checkout exceeded it and warrants investigation. Missing data, non-passing
quality, an inconclusive or unevaluated SLO, or a value that disagrees with its
verdict leaves the decision unresolved. Record the gap and keep the evidence.
This policy has no baseline regression rule.

Record the first pass in a sanitized form suitable for an authorized reviewer.

| Field | Record |
|:--|:--|
| Reviewed selection | Environment revision, exact deployment pins and lock identity |
| Deployment identity | Dedicated context and cluster UID, kept private where required |
| Execution | UTC time, Run ID, lifecycle state and optional tool exit code |
| Reviewed policy | ID, version, SHA-256, rule and threshold |
| Verified report | Registered ID, SHA-256, byte count and confirmed Run/policy linkage |
| Decision | Quality, SLO outcome, p95 when available, conclude met/exceeded or record the gap |
| Return visit | Where the operator can find the same deployment and private record, plus unresolved limitations |

Leave the deployment, persistent storage, Run and report in place. Keep
machine-specific receipts, access details and report bodies outside Git and
public issue output.
