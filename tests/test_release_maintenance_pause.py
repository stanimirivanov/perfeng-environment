"""Exercise the staged maintenance guards and actual local kubectl JSON Patch engine."""

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from tests.release_verifier_support import policy, runtime

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"
JQ = shutil.which("jq")
KUBECTL = shutil.which("kubectl")


class MaintenancePauseTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(
            (DIRECTORY / "release-maintenance-pause-v1.yaml").read_text()
        )
        helper = yaml.safe_load((DIRECTORY / "maintenance-resources-v1.yaml").read_text())
        self.templates = {t["name"]: t for t in helper["spec"]["templates"]}
        self.request = json.loads((ROOT / "tests/fixtures/release-request-v1.json").read_text())
        self.raw = json.dumps(self.request, sort_keys=True).encode() + b"\n"
        self.observations = json.loads(
            (ROOT / "tests/fixtures/release-readiness-observations.json").read_text()
        )
        self.resources: dict[str, dict[str, Any]] = {}
        for name, path in (
            ("root", "gitops/bootstrap/local-root-application.yaml"),
            ("child", "gitops/environments/local/control-plane-application.yaml"),
        ):
            obj = yaml.safe_load((ROOT / path).read_text())
            obj["metadata"].update(uid=name + "-uid", resourceVersion="10", generation=3)
            obj["status"] = {
                "sync": {"status": "Synced", "comparedTo": deepcopy(obj["spec"])},
                "health": {"status": "Healthy"},
                "operationState": {"phase": "Succeeded"},
            }
            if name == "root":
                obj["status"]["sync"]["revision"] = self.request["selection"]["revision"]
            else:
                obj["status"]["sync"]["revisions"] = [
                    s["targetRevision"] for s in obj["spec"]["sources"]
                ]
            self.resources[name] = obj
        observed = self.observations["deployment"]
        self.resources["deployment"] = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "control-plane",
                "namespace": "perf-platform",
                "uid": observed["uid"],
                "resourceVersion": "20",
                "generation": observed["generation"],
                "labels": deepcopy(policy.WORKLOAD_LABELS),
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": deepcopy(policy.WORKLOAD_LABELS)},
                "template": {
                    "spec": {
                        "serviceAccountName": "control-plane",
                        "containers": [
                            {
                                "name": "control-plane",
                                "image": observed["images"][0],
                                "envFrom": [
                                    {"configMapRef": {"name": observed["runtimeConfigMaps"][0]}},
                                    {"secretRef": {"name": observed["runtimeSecrets"][0]}},
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "registry",
                                "configMap": {"name": observed["registryConfigMaps"][0]},
                            },
                            {
                                "name": "authentication",
                                "secret": {"secretName": observed["authenticationSecrets"][0]},
                            },
                        ],
                        "imagePullSecrets": [{"name": observed["imagePullSecrets"][0]}],
                    }
                },
            },
            "status": {
                "observedGeneration": observed["generation"],
                "replicas": 1,
                "readyReplicas": 1,
                "availableReplicas": 1,
                "updatedReplicas": 1,
            },
        }

    def jq(self, code, value):
        assert JQ
        # Input stays on stdin; no shell, cluster, credential or network access.
        result = subprocess.run(
            [JQ, "-ce", code],
            input=json.dumps(value),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if result.returncode:
            raise ValueError(result.stderr)
        return json.loads(result.stdout)

    def guarded(self, name, current=None):
        code = self.templates["guard-" + name]["outputs"]["parameters"][0]["valueFrom"]["jqFilter"]
        code = code.replace("{{inputs.parameters.snapshot}}", json.dumps(self.resources[name]))
        return self.jq(code, self.resources[name] if current is None else current)

    def patch_document(self, name, observation):
        manifest = self.templates["patch-" + name]["resource"]["manifest"]
        return json.loads(
            re.sub(
                r'\{\{=toJson\(jsonpath\(inputs.parameters.observation, "\$\.([a-zA-Z]+)"\)\)\}\}',
                lambda m: json.dumps(observation[m[1]]),
                manifest,
            )
        )

    def test_composition_requires_existing_baseline_before_any_mutation(self):
        spec = self.workflow["spec"]
        self.assertEqual(spec["serviceAccountName"], "release-maintenance-pause")
        self.assertEqual(
            spec["synchronization"]["mutexes"], [{"name": "perfeng-local-external-state"}]
        )
        main, checkpoint = spec["templates"]
        steps = [step for group in main["steps"] for step in group]
        names = [s["name"] for s in steps]
        self.assertLess(names.index("readiness"), names.index("baseline"))
        self.assertLess(names.index("baseline"), names.index("guard-root"))
        self.assertEqual(
            names[-7:],
            [
                "guard-root",
                "pause-root",
                "guard-child",
                "pause-child",
                "guard-deployment",
                "pause-deployment",
                "checkpoint",
            ],
        )
        baseline = next(s for s in steps if s["name"] == "baseline")
        self.assertIn({"name": "phase", "value": "finish"}, baseline["arguments"]["parameters"])
        self.assertFalse(
            any("create" in s.get("templateRef", {}).get("template", "") for s in steps)
        )
        self.assertEqual(checkpoint["retryStrategy"]["retryPolicy"], "OnFailure")
        self.assertEqual(checkpoint["retryStrategy"]["limit"], "4")
        self.assertEqual(
            checkpoint["steps"][0][0]["templateRef"]["name"], "perfeng-verify-release-quiescence-v3"
        )
        for template in self.templates.values():
            self.assertNotIn("retryStrategy", template)
            self.assertNotIn("container", template)
            self.assertNotIn("script", template)
        self.assertEqual(
            main["outputs"]["parameters"][0]["valueFrom"]["parameter"],
            "{{steps.checkpoint.outputs.parameters.result}}",
        )

    def test_authority_has_exact_named_writes_and_no_ledger_writes(self):
        objects = []
        for path in (DIRECTORY / "rbac/maintenance-pause").glob("*.yaml"):
            objects.extend(yaml.safe_load_all(path.read_text()))
        for suffix in ("application", "ledger"):
            objects.extend(
                yaml.safe_load_all(
                    (
                        ROOT
                        / f"gitops/release-workflows-bootstrap/maintenance-pause-{suffix}-access.yaml"
                    ).read_text()
                )
            )
        writes = []
        for obj in objects:
            if obj["kind"] not in {"Role", "ClusterRole"}:
                continue
            for rule in obj["rules"]:
                if set(rule["verbs"]) - {"get", "list"}:
                    if rule["resources"] == ["workflowtaskresults"]:
                        self.assertEqual(obj["metadata"]["namespace"], "argo-workflows")
                        continue
                    self.assertEqual(rule["verbs"], ["get", "patch"])
                    writes.extend(
                        (obj["metadata"]["namespace"], r, n)
                        for r in rule["resources"]
                        for n in rule["resourceNames"]
                    )
        self.assertCountEqual(
            writes,
            [
                ("argocd", "applications", "perfeng-local-root"),
                ("argocd", "applications", "perfeng-local-control-plane"),
                ("perf-platform", "deployments", "control-plane"),
            ],
        )

    def test_captured_projection_and_snapshot_verify_the_same_selected_resource(self):
        for name, template in (("child", "application"), ("deployment", "deployment")):
            code = self.templates[template]["outputs"]["parameters"][0]["valueFrom"]["jqFilter"]
            actual = self.jq(code, self.resources[name])
            self.assertEqual(actual["snapshot"], self.resources[name])
            self.observations[template] = actual
            foreign = deepcopy(self.resources[name])
            foreign["metadata"]["ownerReferences"] = [{"uid": "other-owner"}]
            with self.assertRaises(ValueError):
                self.jq(code, foreign)
        raw = (ROOT / "deployments/local-control-plane.json").read_bytes()
        preflight = {
            "schemaVersion": 1,
            "kind": "PerfengReleasePreflight",
            "status": "identity-verified",
            "environment": "perfeng-local",
            "selectionRevision": self.request["selection"]["revision"],
            "requestRevision": "b" * 40,
            "requestDigest": "sha256:" + hashlib.sha256(self.raw).hexdigest(),
            "clusterUID": self.request["environment"]["clusterUID"],
            "lockDigests": {
                "deployments/local-control-plane.json": "sha256:" + hashlib.sha256(raw).hexdigest()
            },
        }
        readiness = policy.verify_readiness(
            preflight, raw, self.observations, preflight["selectionRevision"]
        )
        root = self.resources["root"]
        plan = policy.plan_adoption(self.raw, readiness, root, None, None)
        receipt = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "immutable": True,
            "metadata": {
                "name": plan["name"],
                "namespace": policy.NAMESPACE,
                "uid": "receipt-uid",
                "resourceVersion": "30",
                "labels": policy.labels("release-adoption"),
                "annotations": {
                    "perfeng.io/first-workflow-uid": "11111111-2222-3333-4444-555555555555",
                    "perfeng.io/first-request-revision": "b" * 40,
                },
            },
            "binaryData": {
                "request.json": plan["request-b64"],
                "receipt.json": plan["receipt-b64"],
            },
        }
        pointer = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": policy.POINTER,
                "namespace": policy.NAMESPACE,
                "uid": "pointer-uid",
                "resourceVersion": "31",
                "labels": policy.labels("current-release"),
            },
            "data": {
                "requestDigest": preflight["requestDigest"],
                "clusterUID": preflight["clusterUID"],
                "receiptName": plan["name"],
                "receiptUID": "receipt-uid",
            },
        }
        values = dict(
            readiness=readiness, root=root, root_after=root, receipt=receipt, pointer=pointer
        )
        with (
            patch.object(runtime, "request_bytes", return_value=self.raw),
            patch.object(runtime, "inputs", return_value=values),
            patch.object(runtime, "Path"),
            patch.object(runtime, "output") as emitted,
            patch.dict("os.environ", PHASE="finish"),
        ):
            runtime.run_adoption()
        result = next(c.args[1] for c in emitted.call_args_list if c.args[0] == "/tmp/result")
        self.assertEqual(result["pointerUID"], "pointer-uid")
        self.assertEqual(result["receiptUID"], "receipt-uid")
        for receipt, pointer in ((None, None), ({}, None)):
            values = dict(
                readiness=readiness, root=root, root_after=root, receipt=receipt, pointer=pointer
            )
            with (
                patch.object(runtime, "request_bytes", return_value=self.raw),
                patch.object(runtime, "inputs", return_value=values),
                patch.dict("os.environ", PHASE="finish"),
                self.assertRaises((ValueError, KeyError)),
            ):
                runtime.run_adoption()

    def test_fresh_reads_allow_status_refresh_but_refuse_spec_identity_and_ownership_changes(self):
        for name, original in self.resources.items():
            current = deepcopy(original)
            current["metadata"]["resourceVersion"] = "new-version"
            current["status"]["reconciledAt"] = "new-time"
            self.assertEqual(self.guarded(name, current)["resourceVersion"], "new-version")
            mutations = [
                ("metadata", "uid", "replacement"),
                ("metadata", "resourceVersion", ""),
                ("metadata", "deletionTimestamp", "now"),
                ("metadata", "ownerReferences", [{"uid": "foreign"}]),
                ("metadata", "labels", {}),
                ("spec", "replicas", 2),
            ]
            if name != "deployment":
                mutations.extend(
                    [
                        ("status", "operationState", {"phase": "Running"}),
                        ("status", "conditions", [{"type": "ComparisonError"}]),
                    ]
                )
            for section, key, value in mutations:
                changed = deepcopy(original)
                changed[section][key] = value
                with self.subTest(name=name, key=key), self.assertRaises(ValueError):
                    self.guarded(name, changed)
            if name != "deployment":
                changed = deepcopy(original)
                changed["operation"] = {"sync": {}}
                with self.assertRaises(ValueError):
                    self.guarded(name, changed)

    def test_actual_kubectl_patch_changes_only_pause_fields_and_rejects_stale_guards(self):
        assert KUBECTL
        with tempfile.TemporaryDirectory() as temp:
            source, patch_file = Path(temp) / "resource.json", Path(temp) / "patch.json"
            for name, original in self.resources.items():
                payload = self.patch_document(name, self.guarded(name))
                self.assertEqual(
                    [p["path"] for p in payload[:3]],
                    ["/metadata/uid", "/metadata/resourceVersion", "/spec"],
                )
                self.assertEqual({p["op"] for p in payload[:3]}, {"test"})
                patch_file.write_text(json.dumps(payload), encoding="utf-8")
                for stale in (None, "uid", "resourceVersion", "spec"):
                    current = deepcopy(original)
                    if stale == "spec":
                        current["spec"]["revisionHistoryLimit"] = 12
                    elif stale:
                        current["metadata"][stale] = "different"
                    source.write_text(json.dumps(current), encoding="utf-8")
                    result = subprocess.run(
                        [
                            KUBECTL,
                            "patch",
                            "--local",
                            "--type=json",
                            "-f",
                            str(source),
                            "--patch-file",
                            str(patch_file),
                            "-o",
                            "json",
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=60,
                    )
                    with self.subTest(name=name, stale=stale):
                        if stale:
                            self.assertNotEqual(result.returncode, 0)
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)
                            expected = deepcopy(original)
                            if name == "deployment":
                                expected["spec"]["replicas"] = 0
                            else:
                                expected["spec"].setdefault("syncPolicy", {}).setdefault(
                                    "automated", {}
                                )["enabled"] = False
                            self.assertEqual(json.loads(result.stdout), expected)


if __name__ == "__main__":
    unittest.main()
