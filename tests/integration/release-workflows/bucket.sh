# CI-only S3 parity; sourced in the existing owned cluster, never an operator CLI.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -n ${fixture:-} && -f $fixture/owned && ${KUBECONFIG:-} == "$fixture/kubeconfig" ]] || exit 2
declare -F k a > /dev/null || exit 2
source "$source_dir/external-state-fixtures.sh"
bucket_setup

bucket_case() {
  bucket_label=$1 bucket_expected=$2
  echo "Bucket case: $bucket_label ($bucket_expected)"
  bucket_jobs > "$fixture/bucket-jobs-before"
  bucket_workflow=$(a submit "$source_dir/bucket-workflow.yaml" -o json | jq -er .metadata.name)
  if timeout 270s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$bucket_workflow"; then bucket_rc=0; else bucket_rc=$?; fi
  a get "$bucket_workflow" -o json > "$results/bucket-$bucket_label.workflow.json"
  bucket_jobs > "$fixture/bucket-jobs-after"
  comm -13 "$fixture/bucket-jobs-before" "$fixture/bucket-jobs-after" > "$fixture/bucket-new-job"
  [[ $(wc -l < "$fixture/bucket-new-job") == 1 ]]
  bucket_job=$(cat "$fixture/bucket-new-job")
  if [[ $bucket_expected == success ]]; then bucket_condition=Complete; else bucket_condition=Failed; fi
  k -n perf-platform wait "--for=condition=$bucket_condition" "job/$bucket_job" --timeout=30s
  k -n perf-platform get job "$bucket_job" -o json > "$results/bucket-$bucket_label.job.json"
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=$bucket_job" -o json > "$fixture/bucket-pods.json"
  if [[ $bucket_expected == success ]]; then
    [[ $bucket_rc == 0 ]]
    jq -e '.status.phase == "Succeeded"' "$results/bucket-$bucket_label.workflow.json" > /dev/null
    jq -e '.status.succeeded == 1' "$results/bucket-$bucket_label.job.json" > /dev/null
    jq -e --arg job "$bucket_job" 'any(.status.nodes[.metadata.name].outputs.parameters[];
      .name == "job-name" and .value == $job)' "$results/bucket-$bucket_label.workflow.json" > /dev/null
    jq -e 'any(.items[].status.containerStatuses[]?; .name == "aws-cli" and .state.terminated.exitCode == 0)' "$fixture/bucket-pods.json" > /dev/null
  else
    [[ $bucket_expected == refusal && $bucket_rc != 0 && $bucket_rc != 124 && $bucket_rc != 137 ]]
    jq -e '.status.phase == "Failed" or .status.phase == "Error"' "$results/bucket-$bucket_label.workflow.json" > /dev/null
    jq -e '.status.failed == 1 and any(.status.conditions[]?; .type == "Failed" and .status == "True")' "$results/bucket-$bucket_label.job.json" > /dev/null
    jq -e 'any(.items[].status.containerStatuses[]?; .name == "aws-cli" and .state.terminated.exitCode == 1)' "$fixture/bucket-pods.json" > /dev/null
    k -n perf-platform logs "job/$bucket_job" -c aws-cli --tail=30 --limit-bytes=8192 \
      | grep -F 'Bucket lookup failed for a reason other than absence' > "$results/bucket-$bucket_label.refusal.txt"
  fi
  bucket_secrets "$fixture/bucket-secrets-after.json"
  cmp "$fixture/bucket-secrets-before.json" "$fixture/bucket-secrets-after.json"
}

bucket_secrets "$fixture/bucket-secrets-before.json"
bucket_absence
if s3 denied head-bucket --bucket perfeng-artifacts > "$fixture/bucket-denied.out" 2> "$fixture/bucket-denied.error"; then
  echo 'Denied fixture credentials unexpectedly accessed S3' >&2; exit 1
fi
grep -Eq '\(403\)|Forbidden' "$fixture/bucket-denied.error"
printf '%s\n' 'Valid profile: bucket absent (404); denied profile: forbidden (403).' > "$results/bucket-preconditions.txt"
bucket_case denied refusal
bucket_absence
denied_uid=$(k -n perf-platform get secret perfeng-s3-auth -o jsonpath='{.metadata.uid}')
# Only fixture administration corrects this deliberately invalid immutable Secret.
k -n perf-platform delete secret perfeng-s3-auth --wait=true --timeout=30s
k create -f "$fixture/bucket-client-approved.json"
approved_uid=$(k -n perf-platform get secret perfeng-s3-auth -o jsonpath='{.metadata.uid}')
[[ $denied_uid != "$approved_uid" ]]
bucket_secrets "$fixture/bucket-secrets-before.json"
bucket_case first success
s3 default head-bucket --bucket perfeng-artifacts
s3 default list-buckets | jq -S '[.Buckets[] | select(.Name == "perfeng-artifacts")]' > "$results/bucket-first.json"
jq -e 'length == 1 and (.[0].CreationDate | type == "string")' "$results/bucket-first.json"
printf '%s\n' 'perfeng-bucket-parity-sentinel-v1' > "$fixture/bucket-sentinel.bin"
observer /bin/sh -ec 'cat > /tmp/bucket-sentinel.bin' < "$fixture/bucket-sentinel.bin"
if s3 default put-object --bucket perfeng-artifacts --key ci/parity-sentinel.bin \
  --body /tmp/bucket-sentinel.bin --if-none-match '*' > "$results/bucket-sentinel-created.json"; then
  :
else
  bucket_put_rc=$?
  bucket_allocation_diagnostics
  exit "$bucket_put_rc"
fi
s3 default head-object --bucket perfeng-artifacts --key ci/parity-sentinel.bin > "$results/bucket-object-before.json"
bucket_case repeat success
s3 default list-buckets | jq -S '[.Buckets[] | select(.Name == "perfeng-artifacts")]' > "$results/bucket-repeat.json"
cmp "$results/bucket-first.json" "$results/bucket-repeat.json"
s3 default head-object --bucket perfeng-artifacts --key ci/parity-sentinel.bin > "$results/bucket-object-after.json"
cmp "$results/bucket-object-before.json" "$results/bucket-object-after.json"
s3 default get-object --bucket perfeng-artifacts --key ci/parity-sentinel.bin /tmp/bucket-retained.bin > "$results/bucket-download.json"
observer cat /tmp/bucket-retained.bin > "$results/bucket-retained.bin"
cmp "$fixture/bucket-sentinel.bin" "$results/bucket-retained.bin"
bucket_jobs > "$results/bucket-retained-jobs.txt"
[[ $(wc -l < "$results/bucket-retained-jobs.txt") == 3 ]]
for finished_case in denied first repeat; do
  original_job=$(jq -r .metadata.name "$results/bucket-$finished_case.job.json")
  original_uid=$(jq -r .metadata.uid "$results/bucket-$finished_case.job.json")
  [[ $(k -n perf-platform get job "$original_job" -o jsonpath='{.metadata.uid}') == "$original_uid" ]]
done
bucket_identity=system:serviceaccount:argo-workflows:release-external-state
for permission in 'create jobs perf-platform yes' 'get jobs perf-platform yes' 'watch jobs perf-platform yes' \
  'delete jobs perf-platform no' 'create secrets perf-platform no' 'patch secrets perf-platform no' \
  'delete secrets perf-platform no' 'list secrets perf-platform no' 'patch deployments perf-platform no'; do
  read -r verb resource namespace expected_answer <<< "$permission"
  answer=$(k auth can-i "$verb" "$resource" --namespace "$namespace" --as "$bucket_identity" || :)
  [[ $answer == "$expected_answer" ]] || { echo "Unexpected bucket workflow authority: $permission" >&2; exit 1; }
done
exec_answer=$(k auth can-i create pods --subresource=exec --namespace perf-platform --as "$bucket_identity" || :)
[[ $exec_answer == no ]] || { echo 'Bucket Workflow unexpectedly has Pod exec authority' >&2; exit 1; }
echo 'Bucket child parity passed; production topology, full parent and cutover remain separate.'
