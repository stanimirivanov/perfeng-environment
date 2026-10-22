# A successful nested preflight result is allowed; a failed parent checkpoint is not.
(.status.phase == "Failed") and
([.status.nodes[.metadata.name].outputs.parameters[]? |
  select(.name == "result" and has("value"))] | length == 0) and
([.status.nodes[] | select(.displayName == $step and .type == "Pod" and .phase == "Failed")] | length == 1) and
([.status.nodes[] | select(.displayName == $step) | .outputs.parameters[]? |
  select(.name == "result" and has("value"))] | length == 0) and
([.status.nodes[] | .outputs.parameters[]? | select(.name == "result") |
  .value | fromjson? | select(.kind == "PerfengMaintenanceCheckpoint")] | length == 0) and
(if $step == "verify-identities" then
  [.status.nodes[] | select(.displayName == "observe" and .phase != "Skipped")] | length == 0
elif $step == "observe" then
  [.status.nodes[] | select(.displayName == "preflight" and .type == "Steps" and .phase == "Succeeded") |
    .outputs.parameters[]? | select(.name == "result") | .value | fromjson? |
    select(.kind == "PerfengReleasePreflight" and .status == "identity-verified")] | length == 1
else false end)
