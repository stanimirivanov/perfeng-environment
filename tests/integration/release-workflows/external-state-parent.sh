#!/usr/bin/env bash
# Complete finite preparation, using the same exact real-UID CI request/ref.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $fixture/parent-parameters.json ]] || exit 2
declare -F k a registry_credentials > /dev/null || exit 2
source "$source_dir/external-state-fixtures.sh"
database_setup
bucket_setup
k create namespace perf-generators
for grant in serviceaccount workflow-access cluster-identity platform-jobs generator-jobs; do
  k apply -f "gitops/release-workflows/rbac/external-state/$grant.yaml"
done
k create -f "$fixture/registry-approved.secret.json"
jq '."request-sha256" = ("sha256:" + ("0" * 64))' "$fixture/parent-parameters.json" > "$fixture/external-wrong-digest.json"

external_inventory() {
  k get jobs --all-namespaces -l perfeng.io/purpose=release-external-state -o json \
    | jq -r '.items[] | select(.metadata.namespace == "perf-platform" or .metadata.namespace == "perf-generators") |
      .metadata.namespace + "/" + .metadata.name' | sort
}
external_secrets() {
  k -n perf-platform get secrets perfeng-postgres-auth perfeng-control-plane-database perfeng-s3-auth bucket-ci-auth -o json \
    | jq -S '.items | sort_by(.metadata.name)' > "$fixture/external-platform-secrets.json"
  k -n perf-generators get secret perfeng-execution-registry -o json | jq -S . > "$fixture/external-registry-secret.json"
  k -n argo-workflows get secret release-git-read -o json | jq -S . > "$fixture/external-git-secret.json"
  cat "$fixture/external-platform-secrets.json" "$fixture/external-registry-secret.json" "$fixture/external-git-secret.json" > "$1"
}
external_case() {
  external_label=$1 external_input=$2 external_failure=$3 external_expected_jobs=$4
  echo "Complete external-state preparation: $external_label"
  external_inventory > "$fixture/external-jobs-before.txt"
  external_secrets "$fixture/external-secrets-before.json"
  external_workflow=$(a submit --from workflowtemplate/perfeng-release-external-state-v1 \
    --parameter-file "$external_input" -o json | jq -er .metadata.name)
  if timeout 930s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$external_workflow"; then external_rc=0; else external_rc=$?; fi
  a get "$external_workflow" -o json > "$results/external-parent-$external_label.workflow.json"
  external_inventory > "$fixture/external-jobs-after.txt"
  comm -13 "$fixture/external-jobs-before.txt" "$fixture/external-jobs-after.txt" > "$results/external-parent-$external_label.jobs.txt"
  [[ $(wc -l < "$results/external-parent-$external_label.jobs.txt") == "$external_expected_jobs" ]]
  while IFS=/ read -r external_namespace external_job; do
    [[ -n $external_job ]] || continue
    case $external_job in
      perfeng-prepare-bucket-v1-*) external_task=bucket external_worker=aws-cli ;;
      perfeng-prepare-db-v1-*) external_task=database external_worker=psql ;;
      perfeng-prepare-registry-v1-*) external_task=registry external_worker='' ;;
      *) echo 'Unexpected external-state Job identity' >&2; exit 1 ;;
    esac
    k -n "$external_namespace" get job "$external_job" -o json > "$results/external-parent-$external_label-$external_task.job.json"
    k -n "$external_namespace" get pods -l "batch.kubernetes.io/job-name=$external_job" -o json > "$fixture/external-job-pods.json"
    jq -e '.items | length == 1 and all(.[]; .spec.automountServiceAccountToken == false)' "$fixture/external-job-pods.json" > /dev/null
    jq '[.items[].status.containerStatuses[]? | {name, exitCode: .state.terminated.exitCode}]' "$fixture/external-job-pods.json" \
      > "$results/external-parent-$external_label-$external_task.exits.json"
    if [[ $external_task == registry ]]; then
      for worker in k6 kube-burner analysis; do
        k -n "$external_namespace" logs "job/$external_job" -c "$worker" --tail=20 --limit-bytes=8192 > "$fixture/external-registry-$worker.log"
        registry_signals "$fixture/external-registry-$worker.log" > "$results/external-parent-$external_label-$worker.signals.txt"
        expected_digest=$(jq -er --arg worker "$worker" '.[$worker]' "$fixture/registry-expected.json")
        actual_digest=$(tail -n 1 "$fixture/external-registry-$worker.log")
        if [[ $actual_digest != "$expected_digest" ]]; then
          echo "Registry $worker did not return the pinned digest; inspect safe signal/exit artifacts and package repository Read grant." >&2
          exit 1
        fi
        printf '%s %s\n' "$worker" "$actual_digest" >> "$results/external-parent-$external_label-registry.digests.txt"
      done
    fi
    if [[ $external_task == "$external_failure" ]]; then
      jq -e '.status.failed == 1 and any(.status.conditions[]?; .type == "Failed" and .status == "True")' \
        "$results/external-parent-$external_label-$external_task.job.json" > /dev/null
      jq -e --arg worker "$external_worker" 'any(.[]; .name == $worker and .exitCode == 1)' \
        "$results/external-parent-$external_label-$external_task.exits.json" > /dev/null
      k -n "$external_namespace" logs "job/$external_job" -c "$external_worker" --tail=30 --limit-bytes=8192 > "$fixture/external-worker.log"
      if [[ $external_task == bucket ]]; then external_diagnostic='Bucket lookup failed for a reason other than absence';
      else external_diagnostic='Existing database role has unsafe attributes'; fi
      grep -Fq "$external_diagnostic" "$fixture/external-worker.log"
      if [[ $external_task == bucket ]]; then
        # Published v1 hides /tmp/head-error. Bracket the bad-client test, but
        # do not claim these probes establish the individual Job's HTTP cause.
        if s3 denied head-bucket --bucket perfeng-artifacts > "$fixture/external-denied-after.out" 2> "$fixture/external-denied-after.error"; then exit 1; fi
        grep -Eq '\(403\)|Forbidden' "$fixture/external-denied-after.error"
      fi
      printf '%s\n' "$external_diagnostic" > "$results/external-parent-$external_label.refusal.txt"
    else
      jq -e '.status.succeeded == 1' "$results/external-parent-$external_label-$external_task.job.json" > /dev/null
      jq -e 'length > 0 and all(.[]; .exitCode == 0)' "$results/external-parent-$external_label-$external_task.exits.json" > /dev/null
    fi
  done < "$results/external-parent-$external_label.jobs.txt"

  if [[ $external_failure == success ]]; then
    [[ $external_rc == 0 ]]
  else
    [[ $external_rc != 0 && $external_rc != 124 && $external_rc != 137 ]]
  fi
  uv run --locked --offline python "$source_dir/external_parent_evidence.py" \
    "$results" "$external_label" "$external_failure" "$fixture/parent-parameters.json" "$cluster_uid"
  if [[ $external_failure == preflight ]]; then
    k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$external_workflow" -o json \
      | jq -e '[.items[].status.containerStatuses[]? | select(.name == "main" and .state.terminated.exitCode == 2)] | length == 1' > /dev/null
    k -n argo-workflows logs -l "workflows.argoproj.io/workflow=$external_workflow" -c main --tail=20 --limit-bytes=8192 > "$fixture/external-preflight.log"
    grep -Fx 'Preflight refused: request bytes differ from the submitted digest' "$fixture/external-preflight.log" > "$results/external-parent-$external_label.refusal.txt"
  fi
  external_secrets "$fixture/external-secrets-after.json"
  cmp "$fixture/external-secrets-before.json" "$fixture/external-secrets-after.json"
}

role_snapshot "$fixture/external-empty-roles.json"
jq -e 'length == 0' "$fixture/external-empty-roles.json" > /dev/null
bucket_absence
if s3 denied head-bucket --bucket perfeng-artifacts > "$fixture/external-denied.out" 2> "$fixture/external-denied.error"; then exit 1; fi
grep -Eq '\(403\)|Forbidden' "$fixture/external-denied.error"
external_case wrong-digest "$fixture/external-wrong-digest.json" preflight 0
external_case denied-bucket "$fixture/parent-parameters.json" bucket 1
bucket_absence
role_snapshot "$fixture/external-still-empty-roles.json"
cmp "$fixture/external-empty-roles.json" "$fixture/external-still-empty-roles.json"
# Explicit fixture administration replaces only its deliberately invalid client.
denied_uid=$(k -n perf-platform get secret perfeng-s3-auth -o jsonpath='{.metadata.uid}')
k -n perf-platform delete secret perfeng-s3-auth --wait=true --timeout=30s
k create -f "$fixture/bucket-client-approved.json"
[[ $(k -n perf-platform get secret perfeng-s3-auth -o jsonpath='{.metadata.uid}') != "$denied_uid" ]]
external_secrets "$fixture/external-approved-secrets.json"
external_case first "$fixture/parent-parameters.json" success 3
role_snapshot "$fixture/external-first-roles.json"
jq -e 'length == 2 and all(.[]; .login and .connect and
  (.superuser or .createDB or .createRole or .replication or .bypassRLS | not)) and
  (map({key:.name,value:.create}) | from_entries) == {perfeng_migration:true,perfeng_runtime:false}' "$fixture/external-first-roles.json" > /dev/null
jq -S 'map(del(.password))' "$fixture/external-first-roles.json" > "$results/external-parent-roles.json"
s3 default list-buckets | jq -S '[.Buckets[] | select(.Name == "perfeng-artifacts")]' > "$results/external-parent-bucket-before.json"
jq -e 'length == 1 and (.[0].CreationDate | type == "string")' "$results/external-parent-bucket-before.json" > /dev/null
printf '%s\n' 'perfeng-external-parent-sentinel-v1' > "$fixture/external-sentinel.bin"
observer /bin/sh -ec 'cat > /tmp/external-sentinel.bin' < "$fixture/external-sentinel.bin"
if ! s3 default put-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin --body /tmp/external-sentinel.bin \
  --if-none-match '*' > "$results/external-parent-sentinel.json"; then bucket_allocation_diagnostics; exit 1; fi
s3 default head-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin > "$results/external-parent-object-before.json"
external_case repeat "$fixture/parent-parameters.json" success 3
role_snapshot "$fixture/external-repeat-roles.json"
cmp "$fixture/external-first-roles.json" "$fixture/external-repeat-roles.json"
printf '%s\n' 'ALTER ROLE perfeng_runtime SUPERUSER;' | sql
role_snapshot "$fixture/external-unsafe-before.json"
external_case unsafe-role "$fixture/parent-parameters.json" database 2
role_snapshot "$fixture/external-unsafe-after.json"
cmp "$fixture/external-unsafe-before.json" "$fixture/external-unsafe-after.json"
printf '%s\n' 'ALTER ROLE perfeng_runtime NOSUPERUSER;' | sql
external_case corrected "$fixture/parent-parameters.json" success 3
role_snapshot "$fixture/external-final-roles.json"
cmp "$fixture/external-first-roles.json" "$fixture/external-final-roles.json"
external_secrets "$fixture/external-final-secrets.json"
cmp "$fixture/external-approved-secrets.json" "$fixture/external-final-secrets.json"
s3 default list-buckets | jq -S '[.Buckets[] | select(.Name == "perfeng-artifacts")]' > "$results/external-parent-bucket-after.json"
cmp "$results/external-parent-bucket-before.json" "$results/external-parent-bucket-after.json"
s3 default head-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin > "$results/external-parent-object-after.json"
cmp "$results/external-parent-object-before.json" "$results/external-parent-object-after.json"
s3 default get-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin /tmp/external-retained.bin > "$results/external-parent-download.json"
observer cat /tmp/external-retained.bin > "$results/external-parent-retained.bin"
cmp "$fixture/external-sentinel.bin" "$results/external-parent-retained.bin"
for label in first repeat corrected; do
  cmp "$results/external-parent-first-registry.digests.txt" "$results/external-parent-$label-registry.digests.txt"
done
for label in wrong-digest denied-bucket first repeat unsafe-role corrected; do
  name=$(jq -er .metadata.name "$results/external-parent-$label.workflow.json")
  uid=$(jq -er .metadata.uid "$results/external-parent-$label.workflow.json")
  [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
done
jq -s -e 'length == 6 and (map(.metadata.uid) | unique | length) == 6' "$results"/external-parent-*.workflow.json > /dev/null
for job_file in "$results"/external-parent-*.job.json; do
  namespace=$(jq -er .metadata.namespace "$job_file") name=$(jq -er .metadata.name "$job_file") uid=$(jq -er .metadata.uid "$job_file")
  [[ $(k -n "$namespace" get job "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
done
identity=system:serviceaccount:argo-workflows:release-external-state
for permission in 'create jobs perf-platform yes' 'create jobs perf-generators yes' 'delete jobs perf-platform no' \
  'delete jobs perf-generators no' 'create secrets perf-platform no' 'patch secrets perf-platform no' \
  'list secrets perf-generators no' 'patch deployments perf-platform no' 'patch applications.argoproj.io argocd no'; do
  read -r verb resource namespace expected <<< "$permission"
  [[ $(k auth can-i "$verb" "$resource" -n "$namespace" --as "$identity" || :) == "$expected" ]]
done
[[ $(k auth can-i create pods --subresource=exec -n perf-platform --as "$identity" || :) == no ]]
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
echo 'Complete external-state parent passed; deployment, migration, recovery and retirement remain separate.'
