# CI-only two-phase helper: register before token cleanup, execute cases last.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -n ${fixture:-} && -f $fixture/owned && ${KUBECONFIG:-} == "$fixture/kubeconfig" ]] || exit 2
declare -F k a > /dev/null || exit 2

registry_credentials() {
  uv run --locked --offline python - "$fixture" <<'PY'
import base64, json, os, re, secrets, sys
from pathlib import Path
import yaml
directory = Path(sys.argv[1])
actor = os.environ.get("GITHUB_ACTOR", "")
token = (directory / "token").read_text(encoding="ascii")
if not actor or len(actor) > 128 or ":" in actor or not token or any(c.isspace() for c in actor + token):
    raise SystemExit("Registry fixture requires a valid Actions actor and private token file")
for label, password in (("approved", token), ("denied", "invalid-" + secrets.token_hex(32))):
    auth = base64.b64encode((actor + ":" + password).encode()).decode()
    config = {"auths": {"ghcr.io": dict(username=actor, password=password, auth=auth)}}
    secret = dict(apiVersion="v1", kind="Secret", type="kubernetes.io/dockerconfigjson", immutable=True,
        metadata=dict(name="perfeng-execution-registry", namespace="perf-generators", labels={
            "app.kubernetes.io/managed-by": "perfeng-environment", "perfeng.io/purpose": "execution-image-pull"}),
        data={".dockerconfigjson": base64.b64encode(json.dumps(config).encode()).decode()})
    (directory / f"registry-{label}.secret.json").write_text(json.dumps(secret))
template = yaml.safe_load(Path("gitops/release-workflows/prepare-execution-registry.yaml").read_text())
job = yaml.safe_load(template["spec"]["templates"][0]["resource"]["manifest"])
expected = {}
for container in job["spec"]["template"]["spec"]["containers"]:
    name = container["name"]
    reference = container["args"][1]
    assert container["args"][0] == "digest" and re.fullmatch(
        r"ghcr\.io/stanimirivanov/perfeng-" + re.escape(name) + r"@sha256:[0-9a-f]{64}", reference)
    expected[name] = reference.split("@")[1]
assert set(expected) == {"k6", "kube-burner", "analysis"}
(directory / "registry-expected.json").write_text(json.dumps(expected))
PY
}
registry_secret_snapshot() {
  k -n perf-generators get secret perfeng-execution-registry -o json | jq -S . > "$1"
}
registry_jobs() {
  k -n perf-generators get jobs -l perfeng.io/purpose=release-external-state -o json \
    | jq -r '.items[].metadata.name | select(startswith("perfeng-prepare-registry-v1-"))' | sort
}
registry_signals() {
  # Emit constants only; registry errors can contain URLs, identities and credentials.
  uv run --locked --offline python - "$1" <<'PY'
import re, sys
from pathlib import Path
message = Path(sys.argv[1]).read_text(errors="replace").lower()
patterns = {
    "authorization": r"unauthorized|denied|forbidden|authentication required|status code (401|403)",
    "manifest-missing": r"manifest_unknown|name_unknown|manifest unknown|status code 404|404 not found",
    "tls": r"x509|tls handshake|certificate signed by unknown authority",
    "network": r"no such host|connection refused|i/o timeout|context deadline exceeded|network is unreachable",
    "command": r"unknown command|unknown flag|exec format error|executable file not found",
}
signals = [name for name, pattern in patterns.items() if re.search(pattern, message)]
print("\n".join(signals or ["unclassified"]))
PY
}
registry_selected_image_prerequisite() {
  [[ ${mode:-} == preflight-parent ]] || return 2
  # Use exactly the auth payload destined for Kubernetes, never a stored login.
  if ! uv run --locked --offline python - "$fixture" <<'PY'
import base64, json, re, sys
from pathlib import Path
directory = Path(sys.argv[1])
contract = json.loads(Path("tests/integration/release-workflows/migration-image.json").read_text())
selected = json.loads(Path("deployments/local-control-plane.json").read_text())["controlPlane"]
if (contract["image"], contract["sourceRevision"]) != (selected["image"], selected["sourceRevision"]) or not re.fullmatch(
    r"ghcr.io/stanimirivanov/perfeng-control-plane@sha256:[0-9a-f]{64}", selected["image"]):
    raise SystemExit("[PREREQUISITE:selection] Selected image/source and migration contract disagree. Correct the reviewed immutable selection and contract together; do not substitute a mutable tag.")
secret = json.loads((directory / "registry-approved.secret.json").read_text())
config = base64.b64decode(secret["data"][".dockerconfigjson"], validate=True)
assert set(json.loads(config)) == {"auths"}
client = directory / "registry-client"
client.mkdir(mode=0o700)
(client / "config.json").write_bytes(config)
(directory / "selected-image.txt").write_text(selected["image"])
PY
  then
    echo '[PREREQUISITE:configuration] Cannot prepare selected-image credentials. Inspect the selection diagnostic and generated Docker-config construction; no cluster was created.' >&2
    return 1
  fi
  local image rc=0 signals
  image=$(cat "$fixture/selected-image.txt")
  echo "Prerequisite: authenticated pull of $image before cluster creation"
  if timeout --kill-after=5s 120s docker --config "$fixture/registry-client" pull --platform linux/amd64 "$image" \
    > "$fixture/selected-image.log" 2>&1; then
    if timeout --kill-after=5s 20s docker --config "$fixture/registry-client" image inspect "$image" \
      --format '{{.Os}}/{{.Architecture}}' > "$fixture/selected-image.platform" 2> "$fixture/selected-image.log"; then
      if [[ $(cat "$fixture/selected-image.platform") != linux/amd64 ]]; then rc=1; signals=platform; fi
    else rc=$?; signals=inspection; fi
  else
    rc=$?
    signals=$(registry_signals "$fixture/selected-image.log")
    if [[ $rc == 124 || $rc == 137 ]]; then signals=timeout; fi
  fi
  rm -f "$fixture/registry-client/config.json" "$fixture/selected-image.log" "$fixture/selected-image.platform"
  rmdir "$fixture/registry-client"
  uv run --locked --offline python - "$results/selected-image-prerequisite.json" "$image" "$rc" "${signals:-}" <<'PY'
import json, sys
from pathlib import Path
path, image, code, signals = sys.argv[1:]
Path(path).write_text(json.dumps({"schemaVersion":1, "image":image,
    "status":"passed" if code == "0" else "failed", "exitCode":int(code),
    "signals":signals.splitlines() if signals else []}) + "\n")
PY
  if [[ $rc == 0 ]]; then
    echo 'Prerequisite passed: selected image is readable with the job pull credentials and is linux/amd64; kubelet pull and migration still require their independent checks.'
    return 0
  fi
  echo "[PREREQUISITE:registry] Selected image pull/inspection failed (exit $rc); no cluster was created. See selected-image-prerequisite.json." >&2
  case "$signals" in
    *authorization*) echo 'Resolution: verify effective Packages: read and this exact package Manage Actions access for perfeng-environment. Read or Admin is sufficient; do not increase Admin further. If that grant is present, investigate job-token/Docker-config delivery. Rerun the full workflow after correction.' >&2 ;;
    *manifest-missing*) echo 'Resolution: verify the locked digest exists in the selected package. Restore the immutable artifact or review a replacement selection; do not switch to a mutable tag.' >&2 ;;
    *tls*) echo 'Resolution: inspect runner/registry certificate trust. Keep TLS verification enabled.' >&2 ;;
    *network*|timeout) echo 'Resolution: check runner DNS, connectivity and GHCR availability, then rerun. Do not extend the migration Job deadline.' >&2 ;;
    platform) echo 'Resolution: publish/select a reviewed linux/amd64 image matching the kind node platform.' >&2 ;;
    *) echo 'Resolution: inspect runner Docker availability and the selected immutable image. Raw vendor output was withheld; do not print credentials or substitute a personal token.' >&2 ;;
  esac
  return 1
}
registry_selected_image_kubelet() {
  [[ ${mode:-} == preflight-parent ]] || return 2
  # Test node credential delivery before controllers/database setup. The probe
  # gets no API token or database credentials; it cannot perform a migration.
  uv run --locked --offline python - "$fixture" <<'PY'
import json, sys
from pathlib import Path
directory = Path(sys.argv[1])
secret = json.loads((directory / "registry-approved.secret.json").read_text())
secret["metadata"] = {"name":"selected-image-prerequisite", "namespace":"perf-platform"}
(directory / "selected-image.secret.json").write_text(json.dumps(secret))
image = (directory / "selected-image.txt").read_text()
pod = {"apiVersion":"v1", "kind":"Pod", "metadata":{"name":"selected-image-prerequisite", "namespace":"perf-platform"},
    "spec":{"restartPolicy":"Never", "activeDeadlineSeconds":150, "automountServiceAccountToken":False,
        "imagePullSecrets":[{"name":"selected-image-prerequisite"}],
        "containers":[{"name":"pull", "image":image, "imagePullPolicy":"Always",
            "command":["/usr/local/bin/migrate"], "env":[{"name":"PERFENG_DATABASE_URL","value":""}],
            "resources":{"requests":{"cpu":"10m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"128Mi"}},
            "securityContext":{"runAsNonRoot":True,"runAsUser":65532,"allowPrivilegeEscalation":False,
                "readOnlyRootFilesystem":True,"capabilities":{"drop":["ALL"]},"seccompProfile":{"type":"RuntimeDefault"}}}]}}
(directory / "selected-image.pod.json").write_text(json.dumps(pod))
PY
  k create -f "$fixture/selected-image.secret.json" > /dev/null
  rm -f "$fixture/selected-image.secret.json"
  k create -f "$fixture/selected-image.pod.json" > /dev/null
  local rc=0 signals
  # A value-less wait accepts imageID="" in kubectl 1.35. Filter empty strings;
  # use > rather than != because its wait parser splits on a single '='.
  if timeout --kill-after=5s 100s kubectl --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --request-timeout=100s -n perf-platform wait pod/selected-image-prerequisite \
    --for=jsonpath='{.status.containerStatuses[?(@.imageID>"")].imageID}' --timeout=95s > "$fixture/selected-image-kubelet.log" 2>&1; then :; else rc=$?; fi
  k -n perf-platform get pod selected-image-prerequisite -o json > "$fixture/selected-image-kubelet.json"
  signals=$(registry_signals "$fixture/selected-image-kubelet.json")
  local report_rc=0
  if uv run --locked --offline python - "$fixture/selected-image-kubelet.json" "$results/selected-image-kubelet-prerequisite.json" "$rc" "$signals" <<'PY'
import json, sys
from pathlib import Path
path, output, code, signals = sys.argv[1:]
pod = json.loads(Path(path).read_text())
statuses = pod.get("status",{}).get("containerStatuses",[])
pulled = len(statuses) == 1 and statuses[0].get("name") == "pull" and bool(statuses[0].get("imageID"))
passed = code == "0" and pulled
Path(output).write_text(json.dumps({"schemaVersion":1, "status":"passed" if passed else "failed",
    "runnerPullPassed":True, "imagePulled":bool(pulled), "exitCode":int(code),
    "signals":[] if passed else signals.splitlines()}) + "\n")
if not passed: raise SystemExit(1)
PY
  then :; else report_rc=$?; fi
  rm -f "$fixture/selected-image-kubelet.log" "$fixture/selected-image-kubelet.json"
  if [[ $report_rc != 0 ]]; then
    echo '[PREREQUISITE:kubelet-pull] Runner pull passed with the same Docker auth JSON, but the kind node could not pull it. Inspect the Kubernetes imagePullSecret data/type/namespace/reference and node registry access. More package Admin access is not the next remedy. See selected-image-kubelet-prerequisite.json; controllers and database setup have not started.' >&2
    return 1
  fi
  echo 'Prerequisite passed: kind kubelet pulled the selected image with the generated pull credentials; migration success remains separate.'
}
registry_case() {
  registry_label=$1 registry_expected=$2
  echo "Registry case: $registry_label ($registry_expected)"
  registry_jobs > "$fixture/registry-jobs-before"
  registry_workflow=$(a submit "$source_dir/registry-workflow.yaml" -o json | jq -er .metadata.name)
  if timeout 330s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$registry_workflow"; then registry_rc=0; else registry_rc=$?; fi
  a get "$registry_workflow" -o json > "$results/registry-$registry_label.workflow.json"
  registry_jobs > "$fixture/registry-jobs-after"
  comm -13 "$fixture/registry-jobs-before" "$fixture/registry-jobs-after" > "$fixture/registry-new-job"
  [[ $(wc -l < "$fixture/registry-new-job") == 1 ]]
  registry_job=$(cat "$fixture/registry-new-job")
  if [[ $registry_rc == 0 ]]; then registry_condition=Complete; else registry_condition=Failed; fi
  k -n perf-generators wait "--for=condition=$registry_condition" "job/$registry_job" --timeout=30s
  k -n perf-generators get job "$registry_job" -o json > "$results/registry-$registry_label.job.json"
  k -n perf-generators get pods -l "batch.kubernetes.io/job-name=$registry_job" -o json > "$fixture/registry-pods.json"
  jq '[.items[].status.containerStatuses[]? | {name, exitCode: .state.terminated.exitCode}]' \
    "$fixture/registry-pods.json" > "$results/registry-$registry_label.exit-codes.json"
  for worker in k6 kube-burner analysis; do
    k -n perf-generators logs "job/$registry_job" -c "$worker" --tail=20 --limit-bytes=8192 > "$fixture/registry-$worker.log"
    # Raw registry URLs/errors may include credential context: never upload them.
    registry_signals "$fixture/registry-$worker.log" > "$results/registry-$registry_label-$worker.signals.txt"
  done
  if [[ $registry_expected == success ]]; then
    if [[ $registry_rc != 0 ]]; then
      for worker in k6 kube-burner analysis; do
        printf 'Registry %s: ' "$worker" >&2
        tr '\n' ' ' < "$results/registry-$registry_label-$worker.signals.txt" >&2
        printf '\n' >&2
      done
      echo 'Registry access failed: inspect registry exit-codes and signal artifacts; authorization requires package Manage Actions access -> repository Read grant.' >&2
      return 1
    fi
    jq -e '.status.phase == "Succeeded"' "$results/registry-$registry_label.workflow.json" > /dev/null
    jq -e '.status.succeeded == 1' "$results/registry-$registry_label.job.json" > /dev/null
    jq -e --arg job "$registry_job" 'any(.status.nodes[.metadata.name].outputs.parameters[];
      .name == "job-name" and .value == $job)' "$results/registry-$registry_label.workflow.json" > /dev/null
    jq -e '[.items[].status.containerStatuses[]?] | length == 3 and all(.[]; .state.terminated.exitCode == 0)' "$fixture/registry-pods.json" > /dev/null
    : > "$results/registry-$registry_label.digests.txt"
    for worker in k6 kube-burner analysis; do
      expected_digest=$(jq -er --arg name "$worker" '.[$name]' "$fixture/registry-expected.json")
      actual_digest=$(tail -n 1 "$fixture/registry-$worker.log")
      [[ $actual_digest == "$expected_digest" ]]
      printf '%s %s\n' "$worker" "$actual_digest" >> "$results/registry-$registry_label.digests.txt"
    done
  else
    [[ $registry_expected == refusal && $registry_rc != 0 && $registry_rc != 124 && $registry_rc != 137 ]]
    jq -e '.status.phase == "Failed" or .status.phase == "Error"' "$results/registry-$registry_label.workflow.json" > /dev/null
    jq -e '.status.failed == 1 and any(.status.conditions[]?; .type == "Failed" and .status == "True")' "$results/registry-$registry_label.job.json" > /dev/null
    jq -e 'any(.items[].status.containerStatuses[]?; .name == "kube-burner" and .state.terminated.exitCode == 1)' "$fixture/registry-pods.json" > /dev/null
    grep -qx authorization "$results/registry-$registry_label-kube-burner.signals.txt"
  fi
  registry_secret_snapshot "$fixture/registry-secret-after.json"
  cmp "$fixture/registry-secret-before.json" "$fixture/registry-secret-after.json"
}
registry_cases() {
  k create namespace perf-generators
  for grant in serviceaccount workflow-access generator-jobs; do
    k apply -f "gitops/release-workflows/rbac/external-state/$grant.yaml"
  done
  k create -f "$fixture/registry-approved.secret.json"
  registry_secret_snapshot "$fixture/registry-secret-before.json"
  registry_case first success
  registry_case repeat success
  cmp "$results/registry-first.digests.txt" "$results/registry-repeat.digests.txt"
  # Explicit administrator-only fixture changes; the release identity cannot rotate Secrets.
  k -n perf-generators delete secret perfeng-execution-registry --wait=true --timeout=30s
  k create -f "$fixture/registry-denied.secret.json"
  registry_secret_snapshot "$fixture/registry-secret-before.json"
  registry_case denied refusal
  k -n perf-generators delete secret perfeng-execution-registry --wait=true --timeout=30s
  k create -f "$fixture/registry-approved.secret.json"
  registry_secret_snapshot "$fixture/registry-secret-before.json"
  registry_case corrected success
  cmp "$results/registry-first.digests.txt" "$results/registry-corrected.digests.txt"
  registry_jobs > "$results/registry-retained-jobs.txt"
  [[ $(wc -l < "$results/registry-retained-jobs.txt") == 4 ]]
  for completed_case in first repeat denied corrected; do
    original_job=$(jq -r .metadata.name "$results/registry-$completed_case.job.json")
    original_uid=$(jq -r .metadata.uid "$results/registry-$completed_case.job.json")
    [[ $(k -n perf-generators get job "$original_job" -o jsonpath='{.metadata.uid}') == "$original_uid" ]]
  done
  registry_identity=system:serviceaccount:argo-workflows:release-external-state
  for permission in 'create jobs perf-generators yes' 'get jobs perf-generators yes' 'watch jobs perf-generators yes' \
    'delete jobs perf-generators no' 'create secrets perf-generators no' 'patch secrets perf-generators no' \
    'delete secrets perf-generators no' 'list secrets perf-generators no' 'patch deployments perf-platform no'; do
    read -r verb resource namespace expected_answer <<< "$permission"
    answer=$(k auth can-i "$verb" "$resource" --namespace "$namespace" --as "$registry_identity" || :)
    [[ $answer == "$expected_answer" ]] || { echo "Unexpected registry workflow authority: $permission" >&2; exit 1; }
  done
  exec_answer=$(k auth can-i create pods --subresource=exec --namespace perf-generators --as "$registry_identity" || :)
  [[ $exec_answer == no ]]
  echo 'Registry manifest-access parity passed; layer downloads, full parent and cutover remain separate.'
}
