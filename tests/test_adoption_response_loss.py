"""Complete-parent transport fault evidence must bind the committed API response."""

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from test_adoption_parent_integration import FIXTURE, READINESS, ROOT, evidence
from test_adoption_parent_integration import MODULE as ADOPTION
from test_ledger_response_proxy import PROXY
from test_release_workflows_integration import BASH

SPEC = importlib.util.spec_from_file_location("adoption_response", FIXTURE / "adoption_response.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(
    sys.modules,
    {
        "adoption_parent_evidence": ADOPTION,
        "readiness_parent_evidence": READINESS,
        "ledger_response_proxy": PROXY,
    },
):
    SPEC.loader.exec_module(MODULE)


def sources():
    return tuple(
        yaml.safe_load((ROOT / "gitops/release-workflows" / name).read_text())
        for name in ("release-adopt-existing-v2.yaml", "adoption-records.yaml")
    )


def failure():
    args = list(evidence(True))[:6]
    workflow, _, _, _, _, api = args
    workflow["spec"]["workflowTemplateRef"]["name"] = MODULE.NAME
    workflow["status"]["phase"] = "Error"
    workflow["status"]["storedWorkflowTemplateSpec"] = MODULE.template(*sources())["spec"]
    nodes = workflow["status"]["nodes"]
    nodes["adoption"].update(phase="Error", outputs={})
    nodes["create-receipt"].update(phase="Error", outputs={}, templateName="fault-create-receipt")
    nodes["barrier"] = {
        "id": "barrier",
        "displayName": MODULE.BARRIER,
        "type": "Suspend",
        "phase": "Succeeded",
    }
    for name in (
        "stored-receipt",
        "root-after",
        "pointer-plan",
        "create-pointer",
        "stored-pointer",
        "final-receipt",
        "final-root",
        "finish",
    ):
        del nodes[name]
    api["pointer"] = None
    expected = copy.deepcopy(api["receipt"])
    for key in ("uid", "resourceVersion"):
        del expected["metadata"][key]
    workers = [
        {
            "nodeID": "create-receipt",
            "account": "release-adoption",
            "uid": "writer-uid",
            "exitCode": 1,
        }
    ]
    events = [
        {
            "event": "receipt-committed-response-dropped",
            "upstreamStatus": 201,
            "uid": api["receipt"]["metadata"]["uid"],
            "contentDigest": PROXY.digest(expected),
            "successfulResponsesForwarded": 0,
        }
    ]
    return args + [workers, events, expected]


def pointer_failure():
    args = list(evidence(True))[:6]
    workflow, _, _, _, _, api = args
    workflow["spec"]["workflowTemplateRef"]["name"] = MODULE.NAME
    workflow["status"]["phase"] = "Error"
    workflow["status"]["storedWorkflowTemplateSpec"] = MODULE.template(*sources(), "pointer")[
        "spec"
    ]
    nodes = workflow["status"]["nodes"]
    nodes["adoption"].update(phase="Error", outputs={})
    nodes["create-pointer"].update(phase="Error", outputs={}, templateName="fault-create-pointer")
    nodes["barrier"] = {
        "id": "barrier",
        "displayName": MODULE.POINTER_BARRIER,
        "type": "Suspend",
        "phase": "Succeeded",
    }
    for name in ("stored-pointer", "final-receipt", "final-root", "finish"):
        del nodes[name]
    expected = copy.deepcopy(api["pointer"])
    for key in ("uid", "resourceVersion"):
        del expected["metadata"][key]
    workers = [
        {
            "nodeID": "create-pointer",
            "account": "release-adoption",
            "uid": "writer-uid",
            "exitCode": 1,
        }
    ]
    events = [
        {
            "event": "pointer-committed-response-dropped",
            "upstreamStatus": 201,
            "uid": api["pointer"]["metadata"]["uid"],
            "contentDigest": PROXY.digest(expected),
            "successfulResponsesForwarded": 0,
        }
    ]
    return args + [workers, events, expected]


class AdoptionResponseLossTests(unittest.TestCase):
    def test_pointer_fault_requires_committed_data_and_no_downstream_result(self):
        args = pointer_failure()
        self.assertEqual(MODULE.expected_record(args[0], args[3], "pointer"), args[-1])
        report = MODULE.verify(*args, operation="pointer")
        self.assertEqual(report["pointerUID"], "pointer-uid")
        self.assertEqual(report["receiptUID"], "receipt-uid")
        for mutation in (
            "pointer-data",
            "receipt-uid",
            "missing-pointer",
            "origin",
            "plan",
            "event",
            "timeout",
            "downstream",
            "owner",
            "immutable",
            "mixed-data",
        ):
            with self.subTest(mutation=mutation):
                values = copy.deepcopy(args)
                workflow, _, _, _, _, api, workers, events, _ = values
                nodes = workflow["status"]["nodes"]
                if mutation == "pointer-data":
                    api["pointer"]["data"]["requestDigest"] = "foreign"
                elif mutation == "receipt-uid":
                    api["receipt"]["metadata"]["uid"] = "foreign"
                elif mutation == "missing-pointer":
                    api["pointer"] = None
                elif mutation == "origin":
                    api["receipt"]["metadata"]["annotations"]["perfeng.io/first-workflow-uid"] = (
                        "foreign"
                    )
                elif mutation == "plan":
                    nodes["pointer-plan"]["outputs"]["parameters"][0]["value"] = "foreign"
                elif mutation == "event":
                    events[0]["uid"] = "receipt-uid"
                elif mutation == "timeout":
                    workers[0]["exitCode"] = 137
                elif mutation == "downstream":
                    nodes["finish"] = {"displayName": "finish", "type": "Pod", "phase": "Succeeded"}
                elif mutation == "owner":
                    api["pointer"]["metadata"]["ownerReferences"] = [{"uid": "workflow"}]
                elif mutation == "immutable":
                    api["pointer"]["immutable"] = True
                else:
                    api["pointer"]["binaryData"] = {"unapproved": "eA=="}
                with self.assertRaises((AssertionError, KeyError, ValueError)):
                    MODULE.verify(*values, operation="pointer")

    def test_pointer_generator_changes_only_pointer_transport_and_barrier(self):
        parent, native = sources()
        generated = MODULE.template(parent, native, "pointer")
        spec = generated["spec"]
        steps = spec["templates"][0]["steps"]
        index = next(
            i for i, group in enumerate(steps) if group[0]["name"] == MODULE.POINTER_BARRIER
        )
        self.assertEqual(steps[index - 1][0]["name"], "pointer-plan")
        self.assertEqual(steps[index + 1][0]["name"], "create-pointer")
        del steps[index]
        create = steps[index][0]
        self.assertEqual(create.pop("template"), "fault-create-pointer")
        create["templateRef"] = {"name": native["metadata"]["name"], "template": "create-pointer"}
        barrier, resource = spec["templates"][1:]
        self.assertEqual(barrier, {"name": MODULE.POINTER_BARRIER, "suspend": {}})
        spec["templates"] = spec["templates"][:1]
        self.assertEqual(spec, parent["spec"])
        original = next(t for t in native["spec"]["templates"] if t["name"] == "create-pointer")
        resource["name"] = "create-pointer"
        resource.pop("podSpecPatch")
        resource["resource"].pop("flags")
        self.assertEqual(
            json.loads(resource["resource"]["manifest"]),
            yaml.safe_load(original["resource"]["manifest"]),
        )
        resource["resource"]["manifest"] = original["resource"]["manifest"]
        self.assertEqual(resource, original)

    def test_pointer_retry_skips_both_creates_and_keeps_full_original_records(self):
        fault = pointer_failure()
        retry = list(evidence(False))
        original = fault[5]
        w, _, _, _, _, api, _ = retry
        w["metadata"]["uid"] = "00000000-0000-0000-0000-000000000003"
        api["receipt"] = copy.deepcopy(original["receipt"])
        api["pointer"] = copy.deepcopy(original["pointer"])
        for name in ("existing-receipt", "stored-receipt", "final-receipt"):
            w["status"]["nodes"][name]["outputs"]["parameters"][0]["value"] = json.dumps(
                api["receipt"]
            )
        result = ADOPTION.success(*retry)
        self.assertEqual(result["receiptUID"], original["receipt"]["metadata"]["uid"])
        self.assertEqual(result["pointerUID"], original["pointer"]["metadata"]["uid"])
        for name in ("create-receipt", "create-pointer"):
            bad = copy.deepcopy(retry)
            bad[0]["status"]["nodes"][name].update(type="Pod", phase="Succeeded")
            with self.assertRaises(AssertionError):
                ADOPTION.success(*bad)

    @unittest.skipUnless(BASH, "Bash required for the runner credential boundary")
    def test_runner_token_is_hidden_only_in_fixture_and_cleanup_survives_every_exit(self):
        assert BASH is not None
        parent = (FIXTURE / "adoption-parent.sh").read_text()
        invocation = parent.split(
            "elif [[ $scenario == response-loss || $scenario == pointer-response-loss ]]; then\n", 1
        )[1].split("\nelse", 1)[0]
        definition = re.search(r"^adoption_response_case\(\) \{\n.*?^\}", parent, re.M | re.S)
        definitions = definition.group(0) if definition else ""
        guards = (FIXTURE / "adoption-response.sh").read_text().split("\nPYTHONPATH=", 1)[0]
        for case, code in (
            ("success", 0),
            ("pointer-success", 0),
            ("pointer-exit", 23),
            ("command-failure", 1),
            ("explicit-exit", 23),
            ("wrong-mode", 2),
            ("wrong-scenario", 2),
            ("unowned", 2),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                if case != "unowned":
                    (directory / "owned").touch()
                helper = (
                    guards
                    + """
[[ -z ${GITHUB_TOKEN:-} && -z ${GH_TOKEN:-} ]]
"$BASH" -c '[[ -z ${GITHUB_TOKEN:-} && -z ${GH_TOKEN:-} ]]'
echo started > "$fixture/started"
if [[ $test_case == command-failure ]]; then false; fi
if [[ $test_case == explicit-exit || $test_case == pointer-exit ]]; then exit 23; fi
adoption_labels+=("$scenario" "$response_recovery")
adoption_baseline=$response_recovery
adoption_origin=$scenario
echo completed > "$fixture/completed"
"""
                )
                (directory / "adoption-response.sh").write_text(
                    helper, encoding="utf-8", newline="\n"
                )
                script = (
                    """set -euo pipefail
fixture=$1
source_dir=$fixture
KUBECONFIG=$fixture/kubeconfig
mode=preflight-parent
scenario=response-loss
[[ $test_case != pointer-* ]] || scenario=pointer-response-loss
[[ $test_case != wrong-mode ]] || mode=boundaries
[[ $test_case != wrong-scenario ]] || scenario=initial
adoption_labels=()
adoption_baseline=first
adoption_origin=first
k() { :; }
a() { :; }
adoption_case() { :; }
adoption_records() { :; }
trap 'if [[ ${GITHUB_TOKEN:-} == runner-sentinel && ${GH_TOKEN:-} == alias-sentinel ]]; then echo restored >> "$fixture/cleanup"; else echo lost >> "$fixture/cleanup"; fi' EXIT
"""
                    + definitions
                    + "\n"
                    + invocation
                    + """
expected_recovery=recovered
[[ $scenario != pointer-response-loss ]] || expected_recovery=pointer-recovered
[[ ${adoption_labels[*]} == "$scenario $expected_recovery" ]]
[[ $adoption_baseline == "$expected_recovery" && $adoption_origin == "$scenario" ]]
"$BASH" -c '[[ $GITHUB_TOKEN == runner-sentinel && $GH_TOKEN == alias-sentinel ]]'
echo returned > "$fixture/returned"
"""
                )
                result = subprocess.run(
                    [BASH, "-c", script, "credential-boundary", directory.as_posix()],
                    env={
                        **os.environ,
                        "GITHUB_ACTIONS": "true",
                        "RUNNER_ENVIRONMENT": "github-hosted",
                        "RUNNER_OS": "Linux",
                        "GITHUB_TOKEN": "runner-sentinel",
                        "GH_TOKEN": "alias-sentinel",
                        "test_case": case,
                    },
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual((directory / "cleanup").read_text().splitlines(), ["restored"])
                self.assertEqual(
                    (directory / "started").exists(),
                    case
                    in {
                        "success",
                        "pointer-success",
                        "pointer-exit",
                        "command-failure",
                        "explicit-exit",
                    },
                )
                self.assertEqual(
                    (directory / "returned").exists(), case in {"success", "pointer-success"}
                )
                self.assertEqual(
                    (directory / "completed").exists(), case in {"success", "pointer-success"}
                )
                self.assertNotIn("sentinel", result.stdout + result.stderr)

    def test_generated_parent_reverses_to_exact_published_composition(self):
        parent, native = sources()
        original = copy.deepcopy(parent)
        generated = MODULE.template(parent, native)
        self.assertEqual(parent, original)
        spec = copy.deepcopy(generated["spec"])
        barrier, resource = spec["templates"][1:]
        self.assertEqual(barrier, {"name": MODULE.BARRIER, "suspend": {}})
        self.assertEqual(resource["activeDeadlineSeconds"], 60)
        self.assertFalse(resource["resource"]["setOwnerReference"])
        manifest = yaml.safe_load(resource["resource"]["manifest"])
        canonical = next(t for t in native["spec"]["templates"] if t["name"] == "create-receipt")
        self.assertEqual(manifest, yaml.safe_load(canonical["resource"]["manifest"]))
        restored_resource = copy.deepcopy(resource)
        restored_resource["name"] = "create-receipt"
        del restored_resource["podSpecPatch"]
        del restored_resource["resource"]["flags"]
        restored_resource["resource"]["manifest"] = canonical["resource"]["manifest"]
        self.assertEqual(restored_resource, canonical)
        spec["templates"] = spec["templates"][:1]
        steps = spec["templates"][0]["steps"]
        index = next(i for i, group in enumerate(steps) if group[0]["name"] == MODULE.BARRIER)
        del steps[index]
        create = steps[index][0]
        self.assertEqual(create.pop("template"), "fault-create-receipt")
        create["templateRef"] = {"name": native["metadata"]["name"], "template": "create-receipt"}
        self.assertEqual(spec, parent["spec"])

    def test_committed_response_matches_actual_plan_and_failed_worker(self):
        args = failure()
        self.assertEqual(MODULE.expected_record(args[0], args[3]), args[-1])
        result = MODULE.verify(*args)
        self.assertEqual(result["receiptUID"], "receipt-uid")
        self.assertEqual(result["workflowUID"], args[0]["metadata"]["uid"])

    def test_controller_serialization_defaults_preserve_strict_composition_check(self):
        args = failure()
        templates = args[0]["status"]["storedWorkflowTemplateSpec"]["templates"]
        # Observed in real Argo v4.1.5 storedWorkflowTemplateSpec evidence: Go
        # emits these empty struct fields and omits the false resource default.
        for template in templates:
            for key in ("inputs", "outputs", "metadata"):
                template.setdefault(key, {})
            for group in template.get("steps", []):
                for step in group:
                    step.setdefault("arguments", {})
            template.get("resource", {}).pop("setOwnerReference", None)
        MODULE.verify(*args)
        templates[-1]["resource"]["setOwnerReference"] = True
        with self.assertRaises(AssertionError):
            MODULE.verify(*args)

    def test_transport_failure_alone_cannot_qualify(self):
        for change in (
            "no-event",
            "denied",
            "uid",
            "digest",
            "forwarded",
            "extra-event",
            "pointer",
            "bytes",
            "origin",
            "worker",
            "exit",
            "timeout",
            "account",
            "result",
            "downstream",
            "readiness",
            "barrier",
            "composition",
            "parent",
            "plan",
        ):
            with self.subTest(change=change):
                args = failure()
                workflow, _, _, _, _, api, workers, events, expected = args
                nodes = workflow["status"]["nodes"]
                if change == "no-event":
                    events.clear()
                elif change == "denied":
                    events[0]["upstreamStatus"] = 403
                elif change == "uid":
                    events[0]["uid"] = "foreign"
                elif change == "digest":
                    events[0]["contentDigest"] = "foreign"
                elif change == "forwarded":
                    events[0]["successfulResponsesForwarded"] = 1
                elif change == "extra-event":
                    events.append({"event": "upstream-failed"})
                elif change == "pointer":
                    api["pointer"] = {"metadata": {"uid": "unexpected"}}
                elif change == "bytes":
                    api["receipt"]["binaryData"]["request.json"] = "eA=="
                elif change == "origin":
                    expected["metadata"]["annotations"]["perfeng.io/first-workflow-uid"] = "foreign"
                elif change == "worker":
                    workers[0]["nodeID"] = "plan"
                elif change == "exit":
                    workers[0]["exitCode"] = 0
                elif change == "timeout":
                    workers[0]["exitCode"] = 137
                elif change == "account":
                    workers[0]["account"] = "default"
                elif change == "result":
                    nodes["adoption"]["outputs"] = {
                        "parameters": [{"name": "result", "value": "{}"}]
                    }
                elif change == "downstream":
                    nodes["create-pointer"] = {
                        "displayName": "create-pointer",
                        "type": "Pod",
                        "phase": "Failed",
                    }
                elif change == "readiness":
                    nodes["readiness"]["phase"] = "Failed"
                elif change == "barrier":
                    nodes["barrier"]["phase"] = "Running"
                elif change == "composition":
                    workflow["status"]["storedWorkflowTemplateSpec"]["serviceAccountName"] = (
                        "default"
                    )
                elif change == "parent":
                    workflow["spec"]["workflowTemplateRef"]["name"] = "foreign"
                else:
                    nodes["plan"]["outputs"]["parameters"][0]["value"] = "foreign"
                with self.assertRaises((AssertionError, KeyError, ValueError)):
                    MODULE.verify(*args)

    def test_fixture_is_main_only_and_recovery_uses_normal_parent(self):
        shell = (FIXTURE / "adoption-response.sh").read_text()
        self.assertIn("${scenario:-} == response-loss", shell)
        self.assertIn("-z ${GITHUB_TOKEN:-}", shell)
        self.assertLess(
            shell.index('a resume "$response_workflow"'),
            shell.index('adoption_case "$scenario" "$scenario"'),
        )
        self.assertLess(
            shell.index('adoption_case "$scenario" "$scenario"'),
            shell.index('adoption_case "$response_recovery" success'),
        )
        self.assertNotIn("--request-timeout", shell)
        parent = (FIXTURE / "adoption-parent.sh").read_text()
        self.assertIn('source "$source_dir/adoption-response.sh"', parent)
        self.assertIn("--from workflowtemplate/perfeng-release-adopt-existing-v2", parent)

    @unittest.skipUnless(BASH and shutil.which("jq"), "Bash and jq required for barrier polling")
    def test_barrier_poll_allows_uninitialized_status_and_refuses_terminal_phase(self):
        assert BASH is not None
        shell = (FIXTURE / "adoption-response.sh").read_text()
        polling = (
            "response_deadline="
            + shell.split("response_deadline=", 1)[1].split("\nPYTHONPATH=", 1)[0]
        )
        for phase, succeeds in (
            (None, True),
            ("", True),
            ("Pending", True),
            ("Running", True),
            ("Failed", False),
            ("Error", False),
            ("Succeeded", False),
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                first = {"status": {"phase": phase}} if phase is not None else {}
                second = {
                    "status": {
                        "phase": "Running",
                        "nodes": {
                            "barrier": {
                                "displayName": MODULE.BARRIER,
                                "type": "Suspend",
                                "phase": "Running",
                            }
                        },
                    }
                }
                (directory / "state-0.json").write_text(json.dumps(first))
                (directory / "state-1.json").write_text(json.dumps(second))
                (directory / "count").write_text("0")
                script = (
                    """set -euo pipefail
fixture=$1
response_workflow=fixture
response_barrier=receipt-response-barrier
k() { local index; index=$(cat "$fixture/count"); echo $((index+1)) > "$fixture/count"; cat "$fixture/state-$index.json"; }
sleep() { :; }
"""
                    + polling
                )
                result = subprocess.run(
                    [BASH, "-c", script, "barrier-test", directory.as_posix()],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                self.assertEqual(
                    (directory / "count").read_text().strip(), "2" if succeeds else "1"
                )

    def test_cli_binds_barrier_plan_and_failed_parent_to_safe_report(self):
        self.check_cli("receipt")

    def test_pointer_cli_binds_committed_pointer_to_actual_plan(self):
        self.check_cli("pointer")

    def check_cli(self, operation):
        args = pointer_failure() if operation == "pointer" else failure()
        label = "pointer-response-loss" if operation == "pointer" else "response-loss"
        workflow, parameters, preflight, raw, _, api, workers, events, expected = args
        barrier = copy.deepcopy(workflow)
        barrier["status"]["phase"] = "Running"
        barrier["status"]["nodes"]["barrier"]["phase"] = "Running"
        del barrier["status"]["nodes"]["create-" + operation]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, value in (
                ("adoption-response-barrier.workflow.json", barrier),
                (f"adoption-parent-{label}.workflow.json", workflow),
                (f"adoption-parent-{label}.workers.json", workers),
                ("parent-parameters.json", parameters),
                ("parent-first.result.json", preflight),
                ("adoption-parent-api.json", api),
                ("ledger-response-expected.json", expected),
            ):
                (directory / name).write_text(json.dumps(value), encoding="utf-8")
            (directory / "parent-request.json").write_bytes(raw)
            (directory / "ledger-response-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events), encoding="utf-8"
            )

            def run(mode):
                return subprocess.run(
                    [
                        sys.executable,
                        str(FIXTURE / "adoption_response.py"),
                        mode,
                        str(directory),
                        str(directory),
                        operation,
                    ],
                    cwd=ROOT,
                    env={**os.environ, "PYTHONPATH": str(ROOT)},
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )

            generated = json.loads(run("template").stdout)
            self.assertEqual(generated, MODULE.template(*sources(), operation))
            self.assertEqual(json.loads(run("expected").stdout), expected)
            run("verify")
            self.assertEqual(
                json.loads((directory / f"adoption-{label}.outcome.json").read_text()),
                MODULE.verify(*args, operation=operation),
            )
            barrier["status"]["nodes"]["barrier"]["phase"] = "Succeeded"
            (directory / "adoption-response-barrier.workflow.json").write_text(
                json.dumps(barrier), encoding="utf-8"
            )
            with self.assertRaises(subprocess.CalledProcessError):
                run("expected")


if __name__ == "__main__":
    unittest.main()
