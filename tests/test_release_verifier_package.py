"""Bounded package inventory and explicit, auditable source retirement."""

import ast
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import unittest
from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "gitops/release-workflows/"
INDEX = "deployments/release-workflow-retirements.json"


@cache
def snapshot(revision):
    raw = subprocess.run(
        ["git", "archive", revision, PREFIX], cwd=ROOT, capture_output=True, check=True
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        result = {}
        for item in archive:
            if item.isfile():
                content = archive.extractfile(item)
                assert content is not None
                result[item.name] = content.read()
        return result


def git_bytes(revision, path):
    return snapshot(revision)[path]


def versioned(path, raw):
    if not path.startswith(PREFIX):
        return False
    if path.endswith((".py", ".sh")):
        return True
    if path.endswith(".yaml") and path != PREFIX + "release-external-state.yaml":
        return any(
            obj.get("kind") == "WorkflowTemplate"
            for obj in yaml.safe_load_all(raw)
            if isinstance(obj, dict)
        )
    return False


def check_lifecycle(previous, current, index, old_index, historical, active):
    """Reject edits; permit deletions only with immutable provenance and replacements."""
    assert index["schemaVersion"] == 1
    entries = {entry["path"]: entry for entry in index["retired"]}
    assert len(entries) == len(index["retired"]), "duplicate retirement"

    def replaced(path, seen):
        assert path not in seen, "replacement cycle"
        if path in current:
            return path in active
        return path in entries and all(
            replaced(p, seen | {path}) for p in entries[path]["replacedBy"]
        )

    for entry in old_index.get("retired", []):
        assert entries.get(entry["path"]) == entry, "retirement history changed"
    for path, entry in entries.items():
        assert path not in current, f"retired source is still present: {path}"
        assert re.fullmatch(r"[0-9a-f]{40}", entry["sourceRevision"])
        raw = historical(entry["sourceRevision"], path)
        assert versioned(path, raw), f"not a published source: {path}"
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"], "retirement hash mismatch"
        if path in previous:
            assert previous[path] == raw, "retirement does not match merge-base bytes"
        replacements = entry["replacedBy"]
        assert replacements and len(set(replacements)) == len(replacements)
        assert all(replaced(p, {path}) for p in replacements), "inactive replacement"
        if path.endswith(".yaml"):
            original = yaml.safe_load(raw)
            assert entry["kubernetesName"] == original["metadata"]["name"]
            for replacement in replacements:
                raw_replacement = (
                    current[replacement]
                    if replacement in current
                    else historical(entries[replacement]["sourceRevision"], replacement)
                )
                obj = yaml.safe_load(raw_replacement)
                assert obj["kind"] == original["kind"]
                old_name = re.fullmatch(r"(.+)-v([0-9]+)", entry["kubernetesName"])
                new_name = re.fullmatch(r"(.+)-v([0-9]+)", obj["metadata"]["name"])
                assert old_name and new_name
                assert old_name[1] == new_name[1] and int(new_name[2]) > int(old_name[2]), (
                    "version not advanced"
                )
                assert obj["metadata"]["namespace"] == original["metadata"]["namespace"]
    for path, raw in previous.items():
        if not versioned(path, raw):
            continue
        if path in current:
            assert current[path] == raw, f"published source changed in place: {path}"
        else:
            assert path in entries, f"unrecorded source retirement: {path}"


class ReleaseVerifierPackageTests(unittest.TestCase):
    def test_entire_staged_python_inventory_is_bounded_and_mounted(self):
        directory = ROOT / PREFIX
        files = sorted(directory.rglob("*.py"))
        self.assertEqual(
            [p.relative_to(directory).as_posix() for p in files],
            ["verifiers-v3/release_policy_v3.py", "verifiers-v3/release_runtime_v3.py"],
        )
        counts = [len(p.read_text().splitlines()) for p in files]
        self.assertLessEqual(sum(counts), 708)
        self.assertLessEqual(max(counts), 500)
        for path in files:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for module in node.names:
                        self.assertIn(
                            module.name.split(".")[0],
                            sys.stdlib_module_names | {"release_policy_v3"},
                        )
                if isinstance(node, ast.ImportFrom):
                    assert node.module is not None
                    self.assertIn(node.module.split(".")[0], sys.stdlib_module_names)
        kustomization = yaml.safe_load((directory / "kustomization.yaml").read_text())
        package = kustomization["configMapGenerator"][1]
        self.assertEqual(package["name"], "perfeng-release-verifiers-v3")
        self.assertEqual(package["namespace"], "argo-workflows")
        self.assertEqual(
            set(package["files"]), {p.relative_to(directory).as_posix() for p in files}
        )
        self.assertTrue(package["options"]["disableNameSuffixHash"])
        modes = set()
        for path in directory.glob("*.yaml"):
            obj = yaml.safe_load(path.read_text())
            for template in obj.get("spec", {}).get("templates", []):
                for kind in ("script", "container"):
                    body = template.get(kind, {})
                    if "python:" not in body.get("image", ""):
                        continue
                    self.assertEqual(kind, "container", "embedded Python is forbidden")
                    self.assertEqual(
                        body["command"], ["python", "/opt/release/release_runtime_v3.py"]
                    )
                    modes.add(body["args"][0])
                    volumes = {v["name"]: v for v in template["volumes"]}
                    mount = next(
                        m for m in body["volumeMounts"] if m["mountPath"] == "/opt/release"
                    )
                    self.assertTrue(mount["readOnly"])
                    self.assertEqual(volumes[mount["name"]]["configMap"]["name"], package["name"])
        self.assertEqual(modes, {"readiness", "quiescence", "adoption", "preflight", "selection"})

    def test_retirement_gate_rejects_unaudited_or_inactive_replacements(self):
        path, replacement = PREFIX + "scripts/old.py", PREFIX + "verifiers-v3/new.py"
        raw = b"print('old')\n"
        entry = {
            "path": path,
            "sourceRevision": "a" * 40,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "replacedBy": [replacement],
        }
        index = {"schemaVersion": 1, "retired": [entry]}

        def check(current, value=index, old=None, active=None):
            check_lifecycle(
                {path: raw},
                current,
                value,
                old or {},
                lambda *_: raw,
                active if active is not None else {replacement},
            )

        check({replacement: b"replacement"})
        for current, value, old, active in (
            ({path: b"edited"}, {"schemaVersion": 1, "retired": []}, {}, set()),
            ({replacement: b"replacement"}, {"schemaVersion": 1, "retired": []}, {}, {replacement}),
            ({replacement: b"replacement"}, index, {}, set()),
            (
                {replacement: b"replacement"},
                index,
                {"retired": [{**entry, "sha256": "0" * 64}]},
                {replacement},
            ),
        ):
            with self.subTest(current=current, value=value), self.assertRaises(AssertionError):
                check(current, value, old, active)
        bad_entry = {**entry, "sha256": "0" * 64}
        bad = {"schemaVersion": 1, "retired": [bad_entry]}
        with self.assertRaisesRegex(AssertionError, "hash mismatch"):
            check({replacement: b"replacement"}, bad)

    def test_retirement_chains_resolve_to_active_sources_and_refuse_cycles(self):
        paths = [PREFIX + f"verifiers-v{i}/policy.py" for i in (1, 2, 3)]
        entries = [
            {
                "path": path,
                "sourceRevision": "a" * 40,
                "sha256": hashlib.sha256(b"old").hexdigest(),
                "replacedBy": [successor],
            }
            for path, successor in zip(paths[:-1], paths[1:], strict=True)
        ]
        index = {"schemaVersion": 1, "retired": entries}
        check_lifecycle(
            {paths[1]: b"old"},
            {paths[2]: b"new"},
            index,
            {"retired": entries[:1]},
            lambda *_: b"old",
            {paths[2]},
        )
        entries[1]["replacedBy"] = [paths[0]]
        with self.assertRaisesRegex(AssertionError, "cycle"):
            check_lifecycle({}, {}, index, {}, lambda *_: b"old", set())

    @unittest.skipUnless(os.environ.get("GITHUB_BASE_REF"), "PR-only immutable source check")
    def test_published_versions_and_retirement_history(self):
        base = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/" + os.environ["GITHUB_BASE_REF"]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        previous = snapshot(base)
        current = {
            p.relative_to(ROOT).as_posix(): p.read_bytes()
            for p in (ROOT / PREFIX).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }
        old = subprocess.run(["git", "show", f"{base}:{INDEX}"], cwd=ROOT, capture_output=True)
        kustomization = yaml.safe_load((ROOT / PREFIX / "kustomization.yaml").read_text())
        active = {PREFIX + p for p in kustomization["resources"]}
        active.update(PREFIX + p for g in kustomization["configMapGenerator"] for p in g["files"])
        check_lifecycle(
            previous,
            current,
            json.loads((ROOT / INDEX).read_text()),
            json.loads(old.stdout) if old.returncode == 0 else {},
            git_bytes,
            active,
        )


if __name__ == "__main__":
    unittest.main()
