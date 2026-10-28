"""Exercise CI request publication without network, external Git writes or Kubernetes."""

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/integration/release-workflows/request_ref.py"
SPEC = importlib.util.spec_from_file_location("ci_request_ref", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REVISION = "a" * 40
COMMIT = "b" * 40
REF = "refs/heads/ci/preflight/123-2"
ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "RUNNER_OS": "Linux",
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REPOSITORY": MODULE.REPOSITORY,
    "GITHUB_RUN_ID": "123",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_SHA": REVISION,
    "GITHUB_TOKEN": "private-token-never-in-argv",
}


class PreflightRequestRefTests(unittest.TestCase):
    def test_main_workflow_has_no_pr_trigger_and_only_approved_authority(self):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-preflight-main.yml").read_text()
        )
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(set(events), {"push", "workflow_dispatch"})
        self.assertEqual(events["push"]["branches"], ["main"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["preflight-parent"]
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertEqual(job["permissions"], {"contents": "write", "packages": "read"})
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertLessEqual(job["timeout-minutes"], 60)  # Owner-approved ceiling, ADR 0034.
        self.assertFalse(job["steps"][0]["with"]["persist-credentials"])
        self.assertEqual(
            job["strategy"],
            {
                "fail-fast": False,
                "matrix": {
                    "adoption-scenario": [
                        "initial",
                        "receipt-only",
                        "response-loss",
                        "pointer-response-loss",
                        "receipt-create-conflict",
                        "pointer-create-conflict",
                    ]
                },
            },
        )
        execute = next(
            s for s in job["steps"] if s.get("run") == "make release-workflows-integration"
        )
        self.assertEqual(
            execute["env"]["RELEASE_ADOPTION_SCENARIO"], "${{ matrix.adoption-scenario }}"
        )
        upload = next(
            s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact")
        )
        self.assertTrue(upload["with"]["name"].endswith("-${{ matrix.adoption-scenario }}"))

    def test_scenario_refs_are_disjoint_and_cleanup_cannot_cross_scenarios(self):
        with patch.dict(os.environ, ENVIRONMENT, clear=True):
            self.assertEqual(MODULE.owned_ref(), REF)
        for scenario, expected in (
            ("initial", REF),
            ("receipt-only", REF + "-receipt-only"),
            ("response-loss", REF + "-response-loss"),
            ("pointer-response-loss", REF + "-pointer-response-loss"),
            ("receipt-create-conflict", REF + "-receipt-create-conflict"),
            ("pointer-create-conflict", REF + "-pointer-create-conflict"),
        ):
            with (
                tempfile.TemporaryDirectory() as temporary,
                patch.dict(
                    os.environ, {**ENVIRONMENT, "RELEASE_ADOPTION_SCENARIO": scenario}, clear=True
                ),
            ):
                self.assertEqual(MODULE.owned_ref(), expected)
                path = Path(temporary)
                foreign = REF if scenario != "initial" else REF + "-receipt-only"
                (path / "request-ref.json").write_text(
                    json.dumps({"ref": foreign, "commit": COMMIT})
                )
                with patch.object(MODULE.subprocess, "run") as run, self.assertRaises(ValueError):
                    MODULE.cleanup(path)
                run.assert_not_called()

    def test_untrusted_context_refused_before_publication(self):
        for key, value in (
            ("GITHUB_REF", "refs/pull/1/merge"),
            ("GITHUB_EVENT_NAME", "pull_request"),
            ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("GITHUB_RUN_ID", "123/other"),
            ("GITHUB_REPOSITORY", "foreign/repository"),
            ("RELEASE_ADOPTION_SCENARIO", "../other"),
        ):
            with (
                self.subTest(key=key),
                patch.dict(os.environ, {**ENVIRONMENT, key: value}, clear=True),
            ):
                with self.assertRaises(ValueError), patch.object(MODULE, "api") as api:
                    MODULE.publish(Path("unused"), "00000000-0000-0000-0000-000000000001")
                api.assert_not_called()

    def test_publication_binds_raw_bytes_and_journals_before_create(self):
        calls = []
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, ENVIRONMENT, clear=True),
        ):
            directory = Path(temporary)

            def api(path, payload=None):
                calls.append((path, payload))
                if path == "git/refs":
                    self.assertEqual(
                        json.loads((directory / "request-ref.json").read_text()),
                        {"ref": REF, "commit": COMMIT},
                    )
                    return {}
                if path.startswith("git/commits/"):
                    return {"tree": {"sha": "c" * 40}}
                return {"sha": COMMIT}

            with (
                patch.object(MODULE, "api", side_effect=api),
                patch.object(
                    MODULE.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, REVISION + "\n", ""),
                ),
            ):
                MODULE.publish(directory, "00000000-0000-0000-0000-000000000001")
            raw = (directory / "parent-request.json").read_bytes()
            self.assertEqual(base64.b64decode(calls[0][1]["content"]), raw)
            parameters = json.loads((directory / "parent-parameters.json").read_text())
            self.assertEqual(
                parameters["request-sha256"], "sha256:" + hashlib.sha256(raw).hexdigest()
            )
            self.assertEqual(parameters["selection-revision"], REVISION)
            self.assertEqual(parameters["request-revision"], COMMIT)
            self.assertEqual(calls[-2][1]["parents"], [REVISION])
            self.assertEqual(calls[-1], ("git/refs", {"ref": REF, "sha": COMMIT}))
            blobs = [
                base64.b64decode(payload["content"])
                for path, payload in calls
                if path == "git/blobs"
            ]
            self.assertEqual(len(blobs), 4)
            self.assertEqual(blobs[1], raw + b"\n")
            self.assertEqual(json.loads(blobs[1]), json.loads(raw))
            upgrade = json.loads(blobs[2])
            original = json.loads(raw)
            self.assertEqual(
                upgrade,
                {
                    **original,
                    "promotion": {
                        **original["promotion"],
                        "expectedCurrent": parameters["request-sha256"],
                    },
                },
            )
            migration = json.loads(blobs[3])
            self.assertEqual(
                migration,
                {
                    **upgrade,
                    "promotion": {
                        **upgrade["promotion"],
                        "migration": {"mode": "none"},
                    },
                },
            )
            tree = next(payload["tree"] for path, payload in calls if path == "git/trees")
            self.assertEqual([item["path"] for item in tree], list(MODULE.REQUEST_PATHS.values()))
            digests = []
            for (label, request_path), content in zip(
                MODULE.REQUEST_PATHS.items(), blobs, strict=True
            ):
                self.assertEqual((directory / f"{label}-request.json").read_bytes(), content)
                item = json.loads((directory / f"{label}-parameters.json").read_text())
                self.assertEqual(
                    item,
                    {
                        **parameters,
                        "request-path": request_path,
                        "request-sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                    },
                )
                digests.append(item["request-sha256"])
            self.assertEqual(len(set(digests)), 4)
            request = json.loads(raw)
            for path, digest in request["selection"]["lockDigests"].items():
                self.assertEqual(
                    digest, "sha256:" + hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                )

    def test_other_scenarios_publish_only_the_original_three_requests(self):
        for scenario in (
            "receipt-only",
            "response-loss",
            "pointer-response-loss",
            "receipt-create-conflict",
            "pointer-create-conflict",
        ):
            calls = []

            def api(path, payload=None, calls=calls):
                calls.append((path, payload))
                return (
                    {"tree": {"sha": COMMIT}}
                    if path.startswith("git/commits/")
                    else {"sha": COMMIT}
                )

            with (
                tempfile.TemporaryDirectory() as temporary,
                patch.dict(
                    os.environ, {**ENVIRONMENT, "RELEASE_ADOPTION_SCENARIO": scenario}, clear=True
                ),
                patch.object(MODULE, "api", side_effect=api),
                patch.object(
                    MODULE.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, REVISION, ""),
                ),
            ):
                directory = Path(temporary)
                MODULE.publish(directory, "00000000-0000-0000-0000-000000000001")
                self.assertEqual(len([p for p, _ in calls if p == "git/blobs"]), 3)
                self.assertFalse((directory / "migration-none-parameters.json").exists())
                self.assertFalse((directory / "migration-none-request.json").exists())
                tree = next(payload["tree"] for path, payload in calls if path == "git/trees")
                self.assertNotIn(MODULE.REQUEST_PATHS["migration-none"], [e["path"] for e in tree])

    def test_lost_create_response_retains_cleanup_intent(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, ENVIRONMENT, clear=True),
        ):
            directory = Path(temporary)
            responses = [
                {"sha": COMMIT},
                {"sha": COMMIT},
                {"sha": COMMIT},
                {"sha": COMMIT},
                {"tree": {"sha": COMMIT}},
                {"sha": COMMIT},
                {"sha": COMMIT},
                ValueError("ambiguous create"),
            ]
            with (
                patch.object(MODULE, "api", side_effect=responses),
                patch.object(
                    MODULE.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, REVISION, ""),
                ),
                self.assertRaises(ValueError),
            ):
                MODULE.publish(directory, "00000000-0000-0000-0000-000000000001")
            self.assertTrue((directory / "request-ref.json").is_file())
            self.assertFalse((directory / "parent-parameters.json").exists())

    def test_cleanup_is_atomic_and_never_passes_credentials_in_arguments(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, ENVIRONMENT, clear=True),
        ):
            directory = Path(temporary)
            (directory / "request-ref.json").write_text(json.dumps({"ref": REF, "commit": COMMIT}))
            with patch.object(
                MODULE.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")
            ) as run:
                MODULE.cleanup(directory)
            command = run.call_args.args[0]
            self.assertEqual(command[-1], ":" + REF)
            self.assertIn(f"--force-with-lease={REF}:{COMMIT}", command)
            self.assertNotIn(ENVIRONMENT["GITHUB_TOKEN"], " ".join(command))
            self.assertEqual(
                json.loads((directory / "request-ref-cleanup.json").read_text())["status"], "absent"
            )

    def test_cleanup_refuses_foreign_moved_and_unverifiable_refs(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, ENVIRONMENT, clear=True),
        ):
            directory = Path(temporary)
            journal = directory / "request-ref.json"
            journal.write_text(json.dumps({"ref": "refs/heads/main", "commit": COMMIT}))
            with patch.object(MODULE.subprocess, "run") as run, self.assertRaises(ValueError):
                MODULE.cleanup(directory)
            run.assert_not_called()
            journal.write_text(json.dumps({"ref": REF, "commit": COMMIT}))
            for response in (
                [{"ref": REF, "object": {"sha": "d" * 40}}],
                ValueError("unavailable"),
            ):
                with (
                    self.subTest(response=response),
                    patch.object(
                        MODULE.subprocess,
                        "run",
                        return_value=subprocess.CompletedProcess([], 1, "", ""),
                    ),
                    patch.object(MODULE, "api", side_effect=[response]),
                    self.assertRaises(ValueError),
                ):
                    MODULE.cleanup(directory)
                self.assertFalse((directory / "request-ref-cleanup.json").exists())
            with (
                patch.object(
                    MODULE.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 1, "", ""),
                ),
                patch.object(MODULE, "api", return_value=[]),
            ):
                MODULE.cleanup(directory)
            self.assertTrue((directory / "request-ref-cleanup.json").is_file())

    def test_receive_pack_lease_preserves_a_moved_ref_and_deletes_the_expected_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            remote = directory / "remote.git"
            subprocess.run(
                ["git", "init", "--bare", "--quiet", str(remote)], check=True, timeout=20
            )

            def git(*arguments, input=None):
                return subprocess.run(
                    [
                        "git",
                        "--git-dir",
                        str(remote),
                        "-c",
                        "user.name=CI test",
                        "-c",
                        "user.email=ci@example.invalid",
                        *arguments,
                    ],
                    input=input,
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=20,
                ).stdout.strip()

            tree = git("mktree", input="")
            expected = git("commit-tree", tree, "-m", "expected")
            moved = git("commit-tree", tree, "-m", "foreign move")
            git("update-ref", REF, moved)
            (directory / "request-ref.json").write_text(
                json.dumps({"ref": REF, "commit": expected})
            )
            # The production push runs against a local bare transport here; no
            # external write or real credential is involved.
            with (
                patch.dict(os.environ, ENVIRONMENT),
                patch.object(MODULE, "URL", str(remote)),
                patch.object(MODULE, "api", return_value=[{"ref": REF, "object": {"sha": moved}}]),
            ):
                with self.assertRaises(ValueError):
                    MODULE.cleanup(directory)
                self.assertEqual(git("rev-parse", REF), moved)
                git("update-ref", REF, expected, moved)
                MODULE.cleanup(directory)
            self.assertEqual(git("for-each-ref", "--format=%(refname)", REF), "")
            self.assertTrue((directory / "request-ref-cleanup.json").is_file())


if __name__ == "__main__":
    unittest.main()
