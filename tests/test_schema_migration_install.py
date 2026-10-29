"""Offline first-install identity, credential retention and fail-stop acceptance."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import test_schema_migration_parent as repeats
import test_server_integration as server
import yaml
from test_release_workflows_integration import BASH

SPEC = importlib.util.spec_from_file_location(
    "schema_migration_install", repeats.pause.FIXTURE / "schema_migration_install.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
with patch.dict(
    "sys.modules", {"schema_migration_parent": repeats.MODULE, "server_inputs": server.MODULE}
):
    SPEC.loader.exec_module(MODULE)


def populate(root, results):
    def save(name, value):
        (root / name).write_text(json.dumps(value))

    _, workflow, workers, old_params, _, raw, before, _, job, pods = repeats.evidence("first")
    parameters = repeats.pause.evidence()[2]
    for obj in (workflow, job):
        text = (
            json.dumps(obj)
            .replace(old_params["request-sha256"], parameters["request-sha256"])
            .replace(old_params["request-path"], parameters["request-path"])
        )
        obj.clear()
        obj.update(json.loads(text))
    save("parent-parameters.json", parameters)
    (root / "parent-request.json").write_bytes(raw)
    save("request-ref.json", {"commit": parameters["request-revision"]})
    original = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "kubernetes.io/dockerconfigjson",
        "metadata": {"uid": "source-pull"},
        "data": {".dockerconfigjson": "private-credential-bytes"},
    }
    pull = server.MODULE.pull_secret(original)
    pull["metadata"].update(uid="retained-pull", resourceVersion="10")
    save("schema-install.pull-source.json", original)
    save("schema-install.pull-before.json", pull)
    save("schema-install.retained-pull.json", pull)
    for app in before["applications"]["items"]:
        app["metadata"]["namespace"] = "argocd"
    deployment = deepcopy(before["deployment"])
    deployment["spec"]["replicas"] = 0
    deployment["spec"]["template"] = {"spec": {"containers": [{"name": "never-started-fixture"}]}}
    deployment["status"] = {"observedGeneration": deployment["metadata"]["generation"]}
    foundation = {
        "kind": "Pod",
        "metadata": {"name": "postgres-0", "uid": "foundation-pod"},
        "spec": {},
        "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
    }
    for label in ("before", "after"):
        for key, value in (
            ("objects", {"items": [deployment, pull]}),
            ("applications", before["applications"]),
            ("pods", {"items": [foundation]}),
            (
                "pvs",
                {
                    "items": [
                        {
                            "kind": "PersistentVolume",
                            "metadata": {"name": "volume", "uid": "pv-uid"},
                            "spec": {"claimRef": {"uid": "claim-uid"}},
                        }
                    ]
                },
            ),
            ("roles", {"roles": "private-role-state"}),
            ("namespace", before["namespace"]),
        ):
            save(f"schema-install.{label}.{key}.json", value)
    save(
        "schema-install.empty.json",
        {
            "schemaAbsent": True,
            "applicationRelations": 0,
            "runtimeCreate": False,
            "migrationCreate": True,
        },
    )
    templates = [
        yaml.safe_load((repeats.MODULE.DIRECTORY / name).read_text())
        for name in repeats.MODULE.SOURCES
    ]
    for obj in templates:
        obj["metadata"]["uid"] = obj["metadata"]["name"]
    intent = yaml.safe_load(
        (repeats.MODULE.DIRECTORY / "schema-migration-intent-v1.yaml").read_text()
    )
    intent["metadata"]["uid"] = "policy-uid"
    for label in ("before", "after"):
        save(f"schema-install.templates-{label}.json", {"items": templates})
        save(f"schema-install.intent-{label}.json", intent)
    save("schema-install.workflow.json", workflow)
    save("schema-install.retained-workflow.json", workflow)
    save(
        "schema-parent.retained-workflows.json",
        {"items": [{"metadata": {"uid": f"later-{i}"}} for i in range(4)]},
    )
    save("schema-install.workers.json", workers)
    save("schema-install.job.json", job)
    save("schema-install.retained-job.json", job)
    save("schema-install.job-pods.json", pods)
    failed = deepcopy(job)
    failed["metadata"].update(uid="failed-job", name="perfeng-schema-failed")
    failed["status"] = {
        "failed": 1,
        "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
    }
    save("schema-contention.job.json", failed)
    save("schema-contention.workflow.json", {"metadata": {"uid": "failed-workflow"}})
    rollback = deepcopy(failed)
    rollback["metadata"].update(uid="rollback-job", name="perfeng-schema-rollback")
    save("schema-rollback.job.json", rollback)
    save("schema-rollback.workflow.json", {"metadata": {"uid": "rollback-workflow"}})
    save("schema-parent-baseline.jobs.json", {"items": [failed, rollback, job]})
    save("schema-install.jobs-before.json", {"items": []})
    save("schema-install.jobs-after.json", {"items": [failed, rollback, job]})
    source = repeats.image.MigrationImageIntegrationTests()
    source.setUp()
    (results / "migration-image-first.schema.json").write_text(json.dumps(source.schema()))


def run(mode, root, results):
    with patch("sys.argv", ["install", mode, str(root), str(results)]):
        MODULE.main()


class SchemaMigrationInstallTests(unittest.TestCase):
    def test_missing_job_diagnostics_never_expose_untrusted_workflow_messages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            (root / "schema-install.job.json").write_text("null")
            path = root / "schema-install.workflow.json"
            workflow = json.loads(path.read_text())
            workflow["status"]["nodes"]["preflight"]["message"] = (
                "https://private-token@host.invalid"
            )
            path.write_text(json.dumps(workflow))
            run("diagnose", root, results)
            text = (results / "schema-install.startup.json").read_text()
            self.assertEqual(json.loads(text)["job"], "absent")
            self.assertNotIn("private-token", text)
            self.assertNotIn("host.invalid", text)

    def test_complete_install_and_retention_publish_only_verified_bounded_outcomes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for mode in ("pull", "before", "verify", "baseline-retained", "retained"):
                run(mode, root, results)
            report = (results / "schema-install.outcome.json").read_text()
            self.assertEqual(json.loads(report)["decision"], "schema-parent-install-verified")
            self.assertNotIn("private", report)
            self.assertTrue((results / "schema-install-retention.outcome.json").exists())

    def test_nonempty_schema_wrong_prefix_credentials_storage_or_job_cannot_qualify(self):
        cases = (
            ("schema-install.empty.json", lambda x: x.update(schemaAbsent=False)),
            ("schema-install.empty.json", lambda x: x.update(applicationRelations=1)),
            (
                "schema-install.after.namespace.json",
                lambda x: x["metadata"].update(uid="replacement"),
            ),
            ("schema-install.after.roles.json", lambda x: x.update(roles="changed")),
            ("schema-install.after.objects.json", lambda x: x["items"][1]["data"].clear()),
            (
                "schema-install.after.pvs.json",
                lambda x: x["items"][0]["spec"]["claimRef"].update(uid="replacement"),
            ),
            (
                "schema-install.after.pods.json",
                lambda x: x["items"][0]["metadata"].update(uid="replacement"),
            ),
            (
                "schema-install.before.objects.json",
                lambda x: x["items"][0]["spec"].update(replicas=1),
            ),
            (
                "schema-install.after.applications.json",
                lambda x: x["items"][0]["spec"]["syncPolicy"]["automated"].update(enabled=True),
            ),
            ("schema-install.jobs-before.json", lambda x: x["items"].append({})),
            (
                "schema-install.workers.json",
                lambda x: x[0].update(account="release-external-state"),
            ),
            (
                "schema-install.workflow.json",
                lambda x: x["status"]["nodes"]["quiescence"].update(phase="Skipped"),
            ),
            (
                "schema-install.job.json",
                lambda x: x["spec"]["template"]["spec"]["containers"][0]["env"][0]["valueFrom"][
                    "secretKeyRef"
                ].update(key="runtime-url"),
            ),
            (
                "schema-install.templates-after.json",
                lambda x: x["items"][0]["metadata"].update(uid="replacement"),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for file, mutate in cases:
                with self.subTest(file=file):
                    target = root / file
                    original = target.read_bytes()
                    value = json.loads(original)
                    mutate(value)
                    target.write_text(json.dumps(value))
                    with self.assertRaises((AssertionError, KeyError)):
                        run("verify", root, results)
                    self.assertFalse((results / "schema-install.outcome.json").exists())
                    target.write_bytes(original)
            target = results / "migration-image-first.schema.json"
            value = json.loads(target.read_text())
            value["ledger"].pop()
            target.write_text(json.dumps(value))
            with self.assertRaises(AssertionError):
                run("verify", root, results)

    def test_later_retention_refuses_install_job_workflow_or_pull_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            populate(root, results)
            for suffix in ("job", "workflow", "pull"):
                path = root / f"schema-install.retained-{suffix}.json"
                original = path.read_bytes()
                value = json.loads(original)
                value["metadata"]["uid"] = "replacement"
                path.write_text(json.dumps(value))
                with self.assertRaises(AssertionError):
                    run("retained", root, results)
                self.assertFalse((results / "schema-install-retention.outcome.json").exists())
                path.write_bytes(original)

    @unittest.skipUnless(BASH, "Bash required")
    def test_actual_install_suite_stops_before_legacy_repeat_on_unverified_boundaries(self):
        assert BASH
        text = (repeats.pause.FIXTURE / "schema-migration-install.sh").read_text()
        library = text[
            text.index("schema_native_parent() {") : text.rindex("\nschema_install_suite")
        ]
        setup = r"""#!/usr/bin/env bash
set -euo pipefail
fixture=$1; results=$1; source_dir=$1; failure=$3; current_stage=outer; KUBECONFIG=$1/kubeconfig
step() { echo "$1" >> "$fixture/trace"; [[ $failure != "$1" ]]; }
start_stage() { :; }; complete_stage() { step complete; }
schema_install_snapshot() { step "snapshot-$1"; }
schema_install_sources() { step "sources-$1"; }
sql() { cat >/dev/null; echo '{}'; }
migration_snapshot() { step schema; echo '{}' > "$1"; }
uv() { step "verify-${6}"; }
k() { if [[ $* == *'--ignore-not-found -o name'* ]]; then return; fi; if [[ $* == 'create '* ]]; then step create-pull >&2; fi; echo '{}'; }
a() { if [[ $* == submit* ]]; then step submit >&2 || return $?; fi; echo '{}'; }
jq() { if [[ $* == '-er '* ]]; then echo workflow; else cat >/dev/null; echo '{}'; fi; }
kubectl() { :; }
argo() { step wait; }
timeout() { if [[ $1 == --kill-after=* ]]; then shift; fi; shift; "$@"; }
source "$2"
schema_install_suite
echo legacy-repeat >> "$fixture/trace"
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "case.sh").write_text(library, newline="\n")
            (root / "run.sh").write_text(setup, newline="\n")
            for failure in (
                "pass",
                "verify-pull",
                "create-pull",
                "snapshot-before",
                "sources-before",
                "verify-before",
                "submit",
                "wait",
                "verify-diagnose-install",
                "contention",
                "rollback",
                "snapshot-after",
                "sources-after",
                "schema",
                "verify-verify",
            ):
                case = root / failure
                case.mkdir()
                (case / "schema-migration-contention.sh").write_text(
                    "step contention\n", newline="\n"
                )
                (case / "schema-migration-rollback.sh").write_text("step rollback\n", newline="\n")
                process = subprocess.run(
                    [
                        BASH,
                        (root / "run.sh").as_posix(),
                        case.as_posix(),
                        (root / "case.sh").as_posix(),
                        failure,
                    ],
                    cwd=repeats.pause.ROOT,
                    env={**os.environ, "BASH_ENV": ""},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                with self.subTest(failure=failure):
                    trace = (case / "trace").read_text().splitlines()
                    self.assertEqual(
                        process.returncode, 0 if failure == "pass" else 1, process.stderr
                    )
                    if failure == "pass":
                        self.assertEqual(trace[-1], "legacy-repeat")
                        self.assertLess(trace.index("verify-before"), trace.index("wait"))
                    else:
                        self.assertNotIn("legacy-repeat", trace)


if __name__ == "__main__":
    unittest.main()
