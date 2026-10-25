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
from test_adoption_receipt_recovery import recovered
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


def conflict_failure(kind="receipt"):
    args = pointer_failure() if kind == "pointer" else failure()
    operation = "pointer-conflict" if kind == "pointer" else "conflict"
    workflow, _, _, _, _, _, workers, events, _ = args
    workflow["status"]["storedWorkflowTemplateSpec"] = MODULE.template(*sources(), operation)[
        "spec"
    ]
    node = workflow["status"]["nodes"]["create-" + kind]
    del node["templateName"]
    node["templateRef"] = {"name": sources()[1]["metadata"]["name"], "template": "create-" + kind}
    # Pinned Argo v4.1.5's actual native AlreadyExists shape from qualified kind
    # runs: executor exit 64, a manifest-path context, and an unescaped final line.
    node["message"] = (
        "main: Error (exit code 64): no more retries Error from server (AlreadyExists): "
        'error when creating "/tmp/manifest.yaml": configmaps "'
        + args[-1]["metadata"]["name"]
        + '" already exists'
    )
    workers[0]["exitCode"] = 64
    events.clear()
    return args


def conflict_keywords(args, kind="receipt"):
    return {
        "winner": copy.deepcopy(args[5][kind]),
        "diagnostic": args[0]["status"]["nodes"]["create-" + kind]["message"],
    }


class AdoptionResponseLossTests(unittest.TestCase):
    @unittest.skipUnless(
        BASH and shutil.which("jq"), "Bash and jq required for fresh conflict observation"
    )
    def test_conflicts_collect_fresh_api_state_before_verification(self):
        assert BASH is not None
        source = (FIXTURE / "adoption-parent.sh").read_text()
        snapshot = source.split('  adoption_records "$fixture/adoption-parent-records.json"\n', 1)[
            1
        ].split("  if [[ $expected == success ]]; then", 1)[0]
        for kind in ("receipt", "pointer"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                api = conflict_failure(kind)[5]
                records = [api["receipt"]] + ([api["pointer"]] if api["pointer"] else [])
                (directory / "adoption-parent-records.json").write_text(json.dumps(records))
                (directory / "adoption-parent-api.json").write_text('{"stale":true}')
                script = (
                    """set -euo pipefail
fixture=$1; expected=$2; cluster_uid=owned-cluster
k() { if [[ $2 == argocd ]]; then printf '%s' '{"metadata":{"uid":"fresh"}}'; else printf '%s' '{"items":[]}'; fi; }
"""
                    + snapshot
                )
                result = subprocess.run(
                    [
                        BASH,
                        "-c",
                        script,
                        "fresh-api",
                        directory.as_posix(),
                        kind + "-create-conflict",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                fresh = json.loads((directory / "adoption-parent-api.json").read_text())
                self.assertNotIn("stale", fresh)
                self.assertEqual(fresh["receipt"], api["receipt"])
                self.assertEqual(fresh["pointer"], api["pointer"])
                self.assertEqual(fresh["application"]["metadata"]["uid"], "fresh")

    def test_pointer_conflict_barrier_reverses_to_exact_production_parent(self):
        parent, native = sources()
        generated = MODULE.template(parent, native, "pointer-conflict")
        spec = generated["spec"]
        self.assertEqual(spec["templates"].pop(), {"name": MODULE.POINTER_BARRIER, "suspend": {}})
        steps = spec["templates"][0]["steps"]
        barrier = next(
            i for i, group in enumerate(steps) if group[0]["name"] == MODULE.POINTER_BARRIER
        )
        del steps[barrier]
        self.assertEqual(
            steps[barrier][0]["templateRef"],
            {"name": native["metadata"]["name"], "template": "create-pointer"},
        )
        self.assertEqual(spec, parent["spec"])

    def test_pointer_conflict_requires_native_refusal_and_preserves_both_records(self):
        args = conflict_failure("pointer")
        report = MODULE.verify(
            *args, operation="pointer-conflict", **conflict_keywords(args, "pointer")
        )
        self.assertEqual(report["decision"], "complete-parent-pointer-create-conflict-observed")
        self.assertEqual(report["pointerUID"], args[5]["pointer"]["metadata"]["uid"])
        for mutation in (
            "exit",
            "authorization",
            "name",
            "winner",
            "pointer",
            "receipt",
            "provenance",
            "plan",
            "transport",
            "events",
            "downstream",
            "result",
        ):
            with self.subTest(mutation=mutation):
                values = copy.deepcopy(args)
                kwargs = conflict_keywords(values, "pointer")
                nodes = values[0]["status"]["nodes"]
                if mutation == "exit":
                    values[6][0]["exitCode"] = 1
                elif mutation == "authorization":
                    kwargs["diagnostic"] = "Forbidden: already exists"
                elif mutation == "name":
                    kwargs["diagnostic"] = kwargs["diagnostic"].replace(
                        "perfeng-local-current", "other"
                    )
                elif mutation == "winner":
                    kwargs["winner"]["metadata"]["uid"] = "other"
                elif mutation == "pointer":
                    values[5]["pointer"]["data"]["receiptUID"] = "other"
                elif mutation == "receipt":
                    values[5]["receipt"]["metadata"]["uid"] = "other"
                elif mutation == "provenance":
                    values[5]["pointer"]["metadata"]["annotations"] = {"unexpected": "other"}
                elif mutation == "plan":
                    nodes["pointer-plan"]["outputs"]["parameters"][0]["value"] = "other"
                elif mutation == "transport":
                    nodes["create-pointer"]["templateRef"]["template"] = "create-receipt"
                elif mutation == "events":
                    values[7].append({"event": "pointer-committed-response-dropped"})
                elif mutation == "downstream":
                    nodes["stored-pointer"] = {
                        "displayName": "stored-pointer",
                        "type": "Pod",
                        "phase": "Succeeded",
                    }
                elif mutation == "result":
                    nodes["adoption"]["outputs"] = {
                        "parameters": [{"name": "result", "value": "{}"}]
                    }
                with self.assertRaises(AssertionError):
                    MODULE.verify(*values, operation="pointer-conflict", **kwargs)

    def test_conflict_barrier_reverses_to_exact_production_parent(self):
        parent, native = sources()
        generated = MODULE.template(parent, native, "conflict")
        spec = generated["spec"]
        self.assertEqual(spec["templates"].pop(), {"name": MODULE.BARRIER, "suspend": {}})
        steps = spec["templates"][0]["steps"]
        barrier = next(i for i, group in enumerate(steps) if group[0]["name"] == MODULE.BARRIER)
        del steps[barrier]
        self.assertEqual(
            steps[barrier][0]["templateRef"],
            {"name": native["metadata"]["name"], "template": "create-receipt"},
        )
        self.assertEqual(spec, parent["spec"])

    def test_conflict_requires_native_already_exists_and_unchanged_winner(self):
        args = conflict_failure()
        report = MODULE.verify(*args, operation="conflict", **conflict_keywords(args))
        self.assertEqual(report["decision"], "complete-parent-receipt-create-conflict-observed")
        for mutation in (
            "diagnostic",
            "object-name",
            "exit",
            "timeout",
            "uid",
            "winner",
            "transport",
            "downstream",
            "result",
            "readiness",
            "plan",
            "message",
        ):
            with self.subTest(mutation=mutation):
                values = copy.deepcopy(args)
                kwargs = conflict_keywords(values)
                nodes = values[0]["status"]["nodes"]
                if mutation == "diagnostic":
                    kwargs["diagnostic"] = "Forbidden: configmaps already exists"
                elif mutation == "object-name":
                    kwargs["diagnostic"] = kwargs["diagnostic"].replace(
                        values[-1]["metadata"]["name"], "foreign"
                    )
                elif mutation == "exit":
                    values[6][0]["exitCode"] = 1
                elif mutation == "timeout":
                    values[6][0]["exitCode"] = 137
                elif mutation == "uid":
                    values[5]["receipt"]["metadata"]["uid"] = "replaced"
                elif mutation == "winner":
                    kwargs["winner"]["binaryData"]["request.json"] = "eA=="
                elif mutation == "transport":
                    values[0]["status"]["storedWorkflowTemplateSpec"] = MODULE.template(*sources())[
                        "spec"
                    ]
                elif mutation == "downstream":
                    nodes["stored-receipt"] = {
                        "displayName": "stored-receipt",
                        "type": "Pod",
                        "phase": "Succeeded",
                    }
                elif mutation == "result":
                    nodes["adoption"]["outputs"] = {
                        "parameters": [{"name": "result", "value": "{}"}]
                    }
                elif mutation == "readiness":
                    nodes["readiness"]["phase"] = "Failed"
                elif mutation == "plan":
                    nodes["plan"]["outputs"]["parameters"][0]["value"] = "foreign"
                elif mutation == "message":
                    nodes["create-receipt"]["message"] = "DeadlineExceeded"
                with self.assertRaises(AssertionError):
                    MODULE.verify(*values, operation="conflict", **kwargs)

    def test_conflict_retry_creates_only_pointer_and_preserves_winning_receipt(self):
        original = conflict_failure()[5]["receipt"]
        args = recovered()
        self.assertEqual(args[5]["receipt"], original)
        report = ADOPTION.success(*args, receipt_only=True)
        self.assertEqual(report["receiptUID"], original["metadata"]["uid"])
        self.assertNotEqual(
            args[0]["metadata"]["uid"],
            original["metadata"]["annotations"]["perfeng.io/first-workflow-uid"],
        )
        for name in ("create-receipt", "create-pointer"):
            bad = copy.deepcopy(args)
            node = bad[0]["status"]["nodes"][name]
            if name == "create-receipt":
                node.update(type="Pod", phase="Succeeded")
            else:
                node.update(type="Skipped", phase="Skipped")
            with self.assertRaises(AssertionError):
                ADOPTION.success(*bad, receipt_only=True)

    def test_conflict_shell_creates_winner_before_resume_without_proxy(self):
        shell = (FIXTURE / "adoption-response.sh").read_text()
        conflict_branch = shell.split(
            "if [[ $response_kind == conflict || $response_kind == pointer-conflict ]]; then", 1
        )[1].split("\nelse", 1)[0]
        self.assertIn(
            'k create -f "$fixture/ledger-response-expected.json" -o json', conflict_branch
        )
        self.assertNotIn("proxy", conflict_branch)
        self.assertLess(
            shell.index('> "$fixture/adoption-conflict-winner.json"'),
            shell.index('a resume "$response_workflow"'),
        )
        self.assertIn('"$fixture/adoption-$scenario.records.json"', shell)

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
        self.check_pointer_retry(pointer_failure())

    def test_pointer_conflict_retry_preserves_both_records_without_creates(self):
        self.check_pointer_retry(conflict_failure("pointer"))

    def check_pointer_retry(self, fault):
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
            "elif [[ $scenario == response-loss || $scenario == pointer-response-loss || $scenario == receipt-create-conflict || $scenario == pointer-create-conflict ]]; then\n",
            1,
        )[1].split("\nelse", 1)[0]
        definition = re.search(r"^adoption_response_case\(\) \{\n.*?^\}", parent, re.M | re.S)
        definitions = definition.group(0) if definition else ""
        guards = (FIXTURE / "adoption-response.sh").read_text().split("\nPYTHONPATH=", 1)[0]
        for case, code in (
            ("success", 0),
            ("pointer-success", 0),
            ("pointer-exit", 23),
            ("conflict-success", 0),
            ("conflict-exit", 23),
            ("pointer-conflict-success", 0),
            ("pointer-conflict-exit", 23),
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
if [[ $test_case == explicit-exit || $test_case == pointer-exit || $test_case == conflict-exit || $test_case == pointer-conflict-exit ]]; then exit 23; fi
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
[[ $test_case != conflict-* ]] || scenario=receipt-create-conflict
[[ $test_case != pointer-conflict-* ]] || scenario=pointer-create-conflict
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
[[ $scenario != pointer-response-loss && $scenario != pointer-create-conflict ]] || expected_recovery=pointer-recovered
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
                        "conflict-success",
                        "conflict-exit",
                        "pointer-conflict-success",
                        "pointer-conflict-exit",
                        "command-failure",
                        "explicit-exit",
                    },
                )
                self.assertEqual(
                    (directory / "returned").exists(),
                    case
                    in {
                        "success",
                        "pointer-success",
                        "conflict-success",
                        "pointer-conflict-success",
                    },
                )
                self.assertEqual(
                    (directory / "completed").exists(),
                    case
                    in {
                        "success",
                        "pointer-success",
                        "conflict-success",
                        "pointer-conflict-success",
                    },
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

    def test_conflict_cli_roundtrip(self):
        self.check_cli("conflict")

    def test_pointer_conflict_cli_roundtrip(self):
        self.check_cli("pointer-conflict")

    def check_cli(self, operation):
        args = (
            conflict_failure("pointer")
            if operation == "pointer-conflict"
            else conflict_failure()
            if operation == "conflict"
            else pointer_failure()
            if operation == "pointer"
            else failure()
        )
        label = (
            "pointer-create-conflict"
            if operation == "pointer-conflict"
            else "receipt-create-conflict"
            if operation == "conflict"
            else "pointer-response-loss"
            if operation == "pointer"
            else "response-loss"
        )
        workflow, parameters, preflight, raw, _, api, workers, events, expected = args
        barrier = copy.deepcopy(workflow)
        barrier["status"]["phase"] = "Running"
        barrier["status"]["nodes"]["barrier"]["phase"] = "Running"
        kind = "pointer" if operation in MODULE.POINTERS else "receipt"
        del barrier["status"]["nodes"]["create-" + kind]
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
            if operation in MODULE.CONFLICTS:
                (directory / "adoption-conflict-winner.json").write_text(json.dumps(api[kind]))
                (directory / "adoption-parent-worker.log").write_text(
                    workflow["status"]["nodes"]["create-" + kind]["message"]
                )
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
                MODULE.verify(
                    *args,
                    operation=operation,
                    **conflict_keywords(args, kind) if operation in MODULE.CONFLICTS else {},
                ),
            )
            barrier["status"]["nodes"]["barrier"]["phase"] = "Succeeded"
            (directory / "adoption-response-barrier.workflow.json").write_text(
                json.dumps(barrier), encoding="utf-8"
            )
            with self.assertRaises(subprocess.CalledProcessError):
                run("expected")


if __name__ == "__main__":
    unittest.main()
