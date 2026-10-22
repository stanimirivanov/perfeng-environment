"""Offline source authorization, runtime wiring and native projection refusals."""

import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SPEC = importlib.util.spec_from_file_location("server_inputs", FIXTURE / "server_inputs.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ServerIntegrationTests(unittest.TestCase):
    def test_source_token_stays_in_pinned_checkout_with_unchanged_job_authority(self):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-preflight-main.yml").read_text()
        )
        job = workflow["jobs"]["preflight-parent"]
        self.assertEqual(job["permissions"], {"contents": "write", "packages": "read"})
        self.assertIn("refs/heads/main", job["if"])
        checkout = next(
            s["with"]
            for s in job["steps"]
            if s.get("with", {}).get("repository") == "stanimirivanov/perfeng-k8s"
        )
        self.assertEqual(checkout["ref"], MODULE.release.load_lock().deployment_revision)
        self.assertFalse(checkout["persist-credentials"])
        self.assertFalse(checkout["submodules"])
        self.assertFalse(checkout["lfs"])
        self.assertEqual(checkout["path"], ".ci/perfeng-k8s")
        self.assertEqual(
            checkout["token"], "${{ secrets.PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN }}"
        )
        self.assertEqual(
            sum("PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN" in json.dumps(s) for s in job["steps"]),
            1,
        )

    def test_checkout_refuses_wrong_head_and_modified_content_before_render(self):
        lock = MODULE.release.load_lock()
        for head, status in (("0" * 40, ""), (lock.deployment_revision, " M chart.yaml")):
            with (
                self.subTest(head=head, status=status),
                patch.object(MODULE.release, "revision_reader"),
                patch.object(MODULE.release, "git", side_effect=[head.encode(), status.encode()]),
            ):
                with self.assertRaises(ValueError):
                    MODULE.source_selection(Path("unused"))

    def test_private_inputs_use_runtime_url_and_existing_storage_pull_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            items = []
            for name, data in (
                (
                    "perfeng-control-plane-database",
                    {"runtime-url": "runtime-only", "migration-url": "admin-never-copied"},
                ),
                ("perfeng-s3-auth", {"access-key": "storage-key", "secret-key": "storage-secret"}),
                ("migration-ci-registry", {".dockerconfigjson": "package-identity"}),
            ):
                items.append(
                    {
                        "metadata": {"name": name},
                        "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()},
                    }
                )
            (directory / "server-input-secrets.json").write_text(json.dumps({"items": items}))
            with patch.object(MODULE, "source_selection", return_value=MODULE.release.load_lock()):
                MODULE.prepare(Path("unused"), directory)
            content = (directory / "server-secrets.json").read_text()
            self.assertNotIn("admin-never-copied", content)
            documents = json.loads(content)["items"]
            runtime = documents[0]
            self.assertEqual(
                runtime["stringData"],
                {
                    "PERFENG_DATABASE_URL": "runtime-only",
                    "AWS_ACCESS_KEY_ID": "storage-key",
                    "AWS_SECRET_ACCESS_KEY": "storage-secret",
                },
            )
            self.assertTrue(all(d["immutable"] for d in documents))
            self.assertEqual(len(documents), 3)

    def test_native_projection_refuses_wrong_image_mounts_uid_stale_rollout_and_service(self):
        lock = MODULE.release.load_lock()
        original = json.loads(
            (ROOT / "tests/fixtures/release-readiness-observations.json").read_text()
        )
        for change in (None, "uid", "image", "stale", "secret", "port", "replicas"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                d, s = json.loads(json.dumps([original["deployment"], original["service"]]))
                d.update(
                    uid="deployment-ci",
                    generation=1,
                    observedGeneration=1,
                    images=[lock.image],
                    runtimeConfigMaps=[lock.runtime_config_map],
                    registryConfigMaps=[lock.registry_config_map],
                )
                if change == "uid":
                    d["uid"] = "foreign"
                if change == "image":
                    d["images"] = ["unreviewed:latest"]
                if change == "stale":
                    d["observedGeneration"] = 0
                if change == "secret":
                    d["runtimeSecrets"] = ["migration-credentials"]
                if change == "port":
                    s["ports"][0]["targetPort"] = 9999
                if change == "replicas":
                    d["ready"] = 0
                w = {
                    "metadata": {"name": "ci"},
                    "status": {
                        "phase": "Succeeded",
                        "nodes": {
                            "ci": {
                                "outputs": {
                                    "parameters": [
                                        {"name": "deployment", "value": json.dumps(d)},
                                        {"name": "service", "value": json.dumps(s)},
                                    ]
                                }
                            }
                        },
                    },
                }
                path = Path(temporary) / "workflow.json"
                path.write_text(json.dumps(w))
                if change:
                    with self.assertRaises(AssertionError):
                        MODULE.observed(path, "deployment-ci")
                else:
                    MODULE.observed(path, "deployment-ci")

    def test_wrapper_reuses_native_children_and_installed_adapter_without_fabricated_inputs(self):
        w = yaml.safe_load((FIXTURE / "server-workflow.yaml").read_text())
        observe, http = w["spec"]["templates"]
        self.assertEqual(
            [s[0]["templateRef"]["template"] for s in observe["steps"][:2]],
            ["deployment", "service"],
        )
        self.assertTrue(
            all(
                s[0]["templateRef"]["name"] == "perfeng-observe-release-readiness-v1"
                for s in observe["steps"][:2]
            )
        )
        self.assertIn("runtime.probe_endpoints()", http["container"]["args"][0])
        self.assertNotIn("verify_readiness", http["container"]["args"][0])
        self.assertIn("assert not Path", http["container"]["args"][0])
        self.assertIn(
            {
                "name": "no-api-token",
                "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
                "readOnly": True,
            },
            http["container"]["volumeMounts"],
        )
        self.assertIn(
            'source "$source_dir/server.sh"', (FIXTURE / "preflight-parent.sh").read_text()
        )


if __name__ == "__main__":
    unittest.main()
