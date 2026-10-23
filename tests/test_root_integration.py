"""Refusal boundaries for root convergence without child activation."""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml
from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SPEC = importlib.util.spec_from_file_location("root_inputs", FIXTURE / "root_inputs.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RootIntegrationTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "gitops/environments/local"
        resources = yaml.safe_load((base / "kustomization.yaml").read_text())["resources"]
        self.objects = [
            o for path in resources for o in yaml.safe_load_all((base / path).read_text())
        ]
        self.revision = "a" * 40

    def observation(self) -> dict[str, Any]:
        spec = MODULE.root_spec()
        return {
            "metadata": {"uid": "root-owned"},
            "spec": spec,
            "status": {
                "sync": {
                    "status": "Synced",
                    "revision": self.revision,
                    "comparedTo": {"source": spec["source"], "destination": spec["destination"]},
                },
                "health": {"status": "Healthy"},
                "resources": [
                    {
                        "kind": o["kind"],
                        "namespace": o["metadata"].get("namespace", ""),
                        "name": o["metadata"]["name"],
                        "status": "Synced",
                    }
                    for o in self.objects
                ],
                "operationState": {
                    "phase": "Succeeded",
                    "syncResult": {"source": spec["source"], "revision": self.revision},
                    "operation": {
                        "info": [{"name": "ci-case", "value": "root-repeat"}],
                        "sync": {"revision": self.revision, "syncStrategy": {"apply": {}}},
                    },
                },
            },
        }

    def test_root_rejects_wrong_revision_stale_operation_automation_and_partial_inventory(self):
        original = self.observation()
        MODULE.converged(original, "root-owned", "root-repeat", self.revision, self.objects)
        for field, replacement in (
            ("metadata.uid", "foreign"),
            ("metadata.annotations", {MODULE.SKIP: "true"}),
            ("spec.source.targetRevision", self.revision),
            ("spec.syncPolicy.automated.enabled", True),
            ("status.sync.status", "OutOfSync"),
            ("status.sync.revision", "b" * 40),
            ("status.sync.comparedTo.source", {}),
            ("status.health.status", "Progressing"),
            ("status.conditions", [{"type": "ComparisonError"}]),
            ("status.resources", original["status"]["resources"][:-1]),
            ("status.operationState.phase", "Failed"),
            ("status.operationState.syncResult.revision", "b" * 40),
            ("status.operationState.operation.info", []),
            ("status.operationState.operation.sync.prune", True),
            ("status.operationState.operation.sync.manifests", ["override"]),
            ("operation", {"sync": {}}),
        ):
            with self.subTest(field=field):
                value = copy.deepcopy(original)
                target = value
                *parents, key = field.split(".")
                for parent in parents:
                    target = target[parent]
                target[key] = replacement
                with self.assertRaises(AssertionError):
                    MODULE.converged(
                        value, "root-owned", "root-repeat", self.revision, self.objects
                    )

    def test_inventory_expansion_is_refused_before_preparing_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tree.yaml"
            path.write_text(yaml.safe_dump_all(self.objects), encoding="utf-8")
            self.assertEqual(len(MODULE.tree(path)), 14)
            extra = {"kind": "Secret", "metadata": {"name": "unexpected"}}
            for objects in (
                self.objects[:-1],
                self.objects + [self.objects[0]],
                self.objects + [extra],
            ):
                path.write_text(yaml.safe_dump_all(objects), encoding="utf-8")
                with self.assertRaises(AssertionError):
                    MODULE.tree(path)

    def test_prepare_creates_exactly_six_skipped_documents_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tree.yaml"
            path.write_text(yaml.safe_dump_all(self.objects), encoding="utf-8")
            original = path.read_bytes()
            subprocess.run(
                [sys.executable, str(FIXTURE / "root_inputs.py"), "prepare", str(path), temporary],
                check=True,
                timeout=20,
            )
            documents = list(Path(temporary).glob("*.json"))
            self.assertEqual({p.stem for p in documents}, MODULE.CHILDREN)
            for document in documents:
                actual = json.loads(document.read_text(encoding="utf-8"))
                expected = copy.deepcopy(
                    next(
                        o
                        for o in self.objects
                        if o["kind"] == "Application" and o["metadata"]["name"] == document.stem
                    )
                )
                expected["metadata"].setdefault("annotations", {})[MODULE.SKIP] = "true"
                self.assertEqual(actual, expected)
            self.assertEqual(path.read_bytes(), original)

    def test_six_skips_and_unchanged_child_operations_are_required(self):
        apps = copy.deepcopy([o for o in self.objects if o["kind"] == "Application"])
        for app in apps:
            app["metadata"]["uid"] = app["metadata"]["name"] + "-uid"
            if app["metadata"]["name"] in MODULE.CHILDREN:
                app["metadata"].setdefault("annotations", {})[MODULE.SKIP] = "true"
        before = {"items": apps}
        MODULE.children(before, self.objects, before)
        for name in MODULE.CHILDREN:
            for change in ("skip", "uid", "operation", "status", "spec"):
                with self.subTest(name=name, change=change):
                    current = copy.deepcopy(before)
                    app = next(o for o in current["items"] if o["metadata"]["name"] == name)
                    if change == "skip":
                        app["metadata"]["annotations"].pop(MODULE.SKIP)
                    elif change == "uid":
                        app["metadata"]["uid"] = "replaced"
                    elif change == "operation":
                        app["operation"] = {"sync": {}}
                    elif change == "status":
                        app["status"] = {"operationState": {"phase": "Succeeded"}}
                    else:
                        app["spec"]["source"]["targetRevision"] = "other"
                    with self.assertRaises(AssertionError):
                        MODULE.children(current, self.objects, before)

    @unittest.skipUnless(BASH, "Bash required")
    def test_phase_is_not_a_standalone_deployment_entrypoint(self):
        assert BASH is not None
        result = subprocess.run(
            [BASH, (FIXTURE / "root.sh").as_posix()], capture_output=True, timeout=20
        )
        self.assertEqual(result.returncode, 2)
        subprocess.run([BASH, "-n", (FIXTURE / "root.sh").as_posix()], check=True, timeout=20)
        chain = (FIXTURE / "preflight-parent.sh").read_text()
        self.assertLess(
            chain.index("seaweedfs_application_acceptance"),
            chain.index('source "$source_dir/root.sh"'),
        )


if __name__ == "__main__":
    unittest.main()
