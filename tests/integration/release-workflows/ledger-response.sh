#!/usr/bin/env bash
# Only the existing owned boundary job may run this native transport fault.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == boundaries ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -z ${GITHUB_TOKEN:-} ]] || exit 2
declare -F k a ledger_case observations > /dev/null || exit 2

uv run --locked --offline python - "$fixture" "$source_dir" <<'PY'
import json, sys, yaml
from pathlib import Path
directory, source = map(Path, sys.argv[1:])
sys.path.insert(0, str(source))
from ledger_response_proxy import fault_workflow
native = yaml.safe_load(Path("gitops/release-workflows/adoption-records.yaml").read_text())
workflow = fault_workflow(native, yaml.safe_load((source / "ledger-workflow.yaml").read_text()), json.loads((directory / "ledger-parameters.json").read_text()))
workflow["spec"]["templates"][0]["resource"]["manifest"] = json.dumps(yaml.safe_load(workflow["spec"]["templates"][0]["resource"]["manifest"]))
(directory / "ledger-response.workflow.json").write_text(json.dumps(workflow))
PY
argo lint --offline --kinds=workflows "$fixture/ledger-response.workflow.json"
response_workflow=$(a submit "$fixture/ledger-response.workflow.json" \
  --parameter-file "$fixture/ledger-parameters.json" -o json | jq -er .metadata.name)
a get "$response_workflow" -o json > "$fixture/ledger-response-submitted.json"
uv run --locked --offline python - "$fixture" "$source_dir" <<'PY'
import json, sys
from pathlib import Path
directory, source = map(Path, sys.argv[1:])
sys.path.insert(0, str(source))
from ledger_response_proxy import ADDRESS, certificate
certificate(directory, "ledger-response-proxy.argo-workflows.svc")
workflow = json.loads((directory / "ledger-response-submitted.json").read_text())
parameters = json.loads((directory / "ledger-parameters.json").read_text())
manifest = workflow["spec"]["templates"][0]["resource"]["manifest"]
for key, value in parameters.items():
    manifest = manifest.replace("{{inputs.parameters." + key + "}}", value)
manifest = manifest.replace("{{workflow.uid}}", workflow["metadata"]["uid"]).replace("{{workflow.parameters.request-revision}}", parameters["request-revision"])
assert "{{" not in manifest
(directory / "ledger-response-expected.json").write_text(manifest)
config = dict(apiVersion="v1", kind="Config", clusters=[dict(name="fault", cluster={"server": ADDRESS, "certificate-authority": "/fault/ca.crt"})],
    users=[dict(name="caller", user={"tokenFile": "/var/run/secrets/kubernetes.io/serviceaccount/token"})],
    contexts=[dict(name="fault", context=dict(cluster="fault", user="caller", namespace="argo-workflows"))], **{"current-context":"fault"})
(directory / "ledger-response-client.json").write_text(json.dumps(config))
PY
k -n argo-workflows create configmap ledger-response-proxy \
  --from-file="proxy.py=$source_dir/ledger_response_proxy.py" \
  --from-file="expected.json=$fixture/ledger-response-expected.json" \
  --from-file="client.json=$fixture/ledger-response-client.json" --from-file="ca.crt=$fixture/ledger-response.crt"
k -n argo-workflows create secret tls ledger-response-proxy-tls \
  --cert="$fixture/ledger-response.crt" --key="$fixture/ledger-response.key"
k create -f "$source_dir/ledger-response-proxy.yaml"
k -n argo-workflows wait pod/ledger-response-proxy --for=condition=Ready --timeout=90s
a resume "$response_workflow"
if timeout 120s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
  --namespace argo-workflows wait "$response_workflow"; then
  echo 'Lost-response native create unexpectedly succeeded' >&2; exit 1
else
  response_rc=$?
  [[ $response_rc != 124 && $response_rc != 137 ]]
fi
a get "$response_workflow" -o json > "$results/ledger-response-loss.workflow.json"
k -n argo-workflows logs ledger-response-proxy -c proxy --limit-bytes=16384 > "$fixture/ledger-response-events.jsonl"
k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$response_workflow" -o json \
  | jq '.items|map({uid:.metadata.uid,account:.spec.serviceAccountName,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])})' \
  > "$results/ledger-response-loss.workers.json"
ledger_case response-observed observe success
observations "$results/ledger-response-observed.workflow.json" > "$fixture/ledger-response-observed.json"
uv run --locked --offline python - "$fixture" "$results" "$source_dir" <<'PY'
import json, sys
from pathlib import Path
directory, results, source = map(Path, sys.argv[1:])
sys.path.insert(0, str(source))
from ledger_response_proxy import verify
def load(path): return json.loads(path.read_text())
workflow = load(results / "ledger-response-loss.workflow.json")
workers = load(results / "ledger-response-loss.workers.json")
assert len(workers) == 1 and workers[0]["uid"] and workers[0]["account"] == "release-adoption", "Writer Pod missing or unexpected identity; inspect ledger-response-loss.workflow.json status.message and worker projection"
assert workers[0]["nodeID"] == workflow["metadata"]["name"] and workers[0]["exitCode"] not in (None, 0), "Writer did not fail with the expected node identity; inspect worker projection"
events = [json.loads(line) for line in (directory / "ledger-response-events.jsonl").read_text().splitlines()]
report = verify(workflow, events, load(directory / "ledger-response-observed.json"), load(directory / "ledger-response-expected.json"))
(results / "ledger-response-loss.outcome.json").write_text(json.dumps(report, sort_keys=True))
PY
# Retry through the unchanged direct native template, never through the proxy.
ledger_case response-retry receipt-create conflict
ledger_case response-reread observe success
observations "$results/ledger-response-reread.workflow.json" | jq -S . > "$fixture/ledger-response-reread.json"
jq -S . "$fixture/ledger-response-observed.json" > "$fixture/ledger-response-before.json"
cmp "$fixture/ledger-response-before.json" "$fixture/ledger-response-reread.json"
recovery_check partial "$fixture/ledger-response-observed.json"
