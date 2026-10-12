"""Candidate packaging never promotes fabricated identity or accesses Docker from previews."""

import copy
import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import yaml

from scripts import native_target as target


class NativeTargetTests(unittest.TestCase):
    def metadata(self):
        return [
            {
                "Os": "linux",
                "Architecture": "amd64",
                "Config": {
                    "User": "101:101",
                    "Entrypoint": ["nginx"],
                    "Cmd": ["-g", "daemon off;"],
                    "Labels": {
                        "org.opencontainers.image.source": target.SOURCE,
                        "org.opencontainers.image.revision": "a" * 40,
                        "org.opencontainers.image.version": "1.0.0",
                        "dev.perfeng.target.sha256": target.target_identity(),
                    },
                },
            }
        ]

    def test_every_preview_is_offline_and_read_only(self):
        with (
            patch.object(target, "docker") as docker,
            patch.object(target.cluster, "verify_local_docker") as local,
        ):
            for action in ("validate", "build", "verify", "reference"):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(target.main([action]), 0)
            docker.assert_not_called()
            local.assert_not_called()

    def test_exact_packaging_and_build_context(self):
        selected = target.validate()
        self.assertEqual(selected["repository"], target.REPOSITORY)
        self.assertIn("@sha256:", selected["baseImage"])
        self.assertEqual(selected["targetSha256"], target.target_identity())
        self.assertEqual(
            (target.ROOT / "LICENSE").read_text().strip(),
            (target.ROOT / "native-gateway/search/LICENSE").read_text().strip(),
        )
        command = target.build_command("perfeng-native-search:local-check", "development")
        self.assertIn("--platform=linux/amd64", command)
        self.assertEqual(command[-1], str(target.ROOT / "native-gateway/search"))
        self.assertIn(f"TARGET_SHA256={selected['targetSha256']}", command)

    def test_rejects_floating_or_foreign_references_and_unreviewed_source(self):
        for image in ("latest", "--privileged", "ghcr.io/other/candidate:1", target.REPOSITORY):
            with self.assertRaises(ValueError):
                target.identity(image, "a" * 40)
        with self.assertRaisesRegex(ValueError, "exact source"):
            target.identity(target.REPOSITORY + ":build", "development")
        with self.assertRaisesRegex(ValueError, "temporary tag"):
            target.build_command(target.REPOSITORY + "@sha256:" + "a" * 64, "a" * 40)

    def test_validates_actual_runtime_metadata_not_just_a_claimed_label(self):
        value = self.metadata()
        target.verify_metadata(value, "a" * 40)
        for field, changed in (("Os", "windows"), ("Architecture", "arm64")):
            invalid = copy.deepcopy(value)
            invalid[0][field] = changed
            with self.assertRaisesRegex(ValueError, "differs"):
                target.verify_metadata(invalid, "a" * 40)
        for field, changed in (("User", "root"), ("Entrypoint", ["sh"]), ("Cmd", ["unsafe"])):
            invalid = copy.deepcopy(value)
            invalid[0]["Config"][field] = changed
            with self.assertRaisesRegex(ValueError, "differs"):
                target.verify_metadata(invalid, "a" * 40)
        with self.assertRaisesRegex(ValueError, "differs"):
            target.verify_metadata(value, "b" * 40)
        value[0]["Config"]["Labels"]["dev.perfeng.target.sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "differs"):
            target.verify_metadata(value, "a" * 40)

    def test_reference_requires_a_unique_real_repository_digest(self):
        expected = target.REPOSITORY + "@sha256:" + "a" * 64
        with patch.object(target, "docker", return_value=json.dumps([expected])):
            self.assertEqual(target.reference(target.REPOSITORY + ":build"), expected)
        for values in (
            [],
            None,
            ["sha256:" + "a" * 64],
            [expected, target.REPOSITORY + "@sha256:" + "b" * 64],
        ):
            with patch.object(target, "docker", return_value=json.dumps(values)):
                with self.assertRaisesRegex(ValueError, "unique"):
                    target.reference(target.REPOSITORY + ":build")

    def test_disposable_verification_is_loopback_hardened_and_ownership_checked(self):
        identifier = "c" * 64
        labels = {"dev.perfeng.owner": target.OWNER, "dev.perfeng.invocation": "nonce"}
        responses = [
            json.dumps(self.metadata()),
            identifier,
            json.dumps({"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "54321"}]}),
            json.dumps(labels),
            "",
        ]
        with (
            patch.object(target.uuid, "uuid4") as uuid,
            patch.object(target, "docker", side_effect=responses) as docker,
            patch.object(target, "probe", return_value=(200, b"ready\n", {})),
            patch.object(target, "verify_http") as http,
        ):
            uuid.return_value.hex = "nonce"
            target.verify("perfeng-native-search:ci", "a" * 40)
            run = docker.call_args_list[1].args[0]
            for value in (
                "--read-only",
                "--cap-drop=ALL",
                "--pids-limit=64",
                "--publish=127.0.0.1::8080",
            ):
                self.assertIn(value, run)
            http.assert_called_once_with(54321, target.ROOT)
            self.assertEqual(docker.call_args_list[-1].args[0], ["rm", "--force", identifier])

    def test_verification_failure_preserves_ownership_and_cleanup_failures(self):
        labels = {"dev.perfeng.owner": target.OWNER, "dev.perfeng.invocation": "nonce"}
        for owner, grouped in ((labels, True), ({}, False)):
            responses = [
                json.dumps(self.metadata()),
                "c" * 64,
                "{}",
                json.dumps(owner),
                ValueError("cleanup unavailable"),
            ]
            with (
                patch.object(target.uuid, "uuid4") as uuid,
                patch.object(target, "docker", side_effect=responses) as docker,
            ):
                uuid.return_value.hex = "nonce"
                with self.assertRaises(ExceptionGroup):
                    target.verify("perfeng-native-search:ci", "a" * 40)
                if not grouped:
                    self.assertFalse(any(call.args[0][0] == "rm" for call in docker.call_args_list))

    def test_http_requires_exact_bytes_and_closed_paths(self):
        directory = target.ROOT / "native-gateway/search"
        headers = {
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            "content-security-policy": target.CSP,
        }
        responses = [(200, (directory / "index.html").read_bytes(), headers)] * 2
        responses += [
            (200, (directory / "search.js").read_bytes(), headers),
            (200, b"ready\n", headers),
            (200, b"", headers),
        ]
        responses += [(404, b"", headers)] * 4 + [(405, b"", headers)] * 3
        with patch.object(target, "probe", side_effect=responses):
            target.verify_http(54321)
        with patch.object(target, "probe", return_value=(200, b"wrong", headers)):
            with self.assertRaisesRegex(ValueError, "content"):
                target.verify_http(54321)

    def test_docker_errors_are_bounded_and_do_not_echo_backend_output(self):
        result = subprocess.CompletedProcess([], 1, stdout="private", stderr="private")
        with (
            patch.object(target.subprocess, "run", return_value=result) as run,
            redirect_stderr(io.StringIO()),
        ):
            with self.assertRaisesRegex(ValueError, "exit 1") as failure:
                target.docker(["image", "inspect", "perfeng-native-search:ci"])
            self.assertNotIn("private", str(failure.exception))
            self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_publication_is_main_only_opt_in_and_digest_reverified(self):
        document = yaml.safe_load(
            (target.ROOT / ".github/workflows/publish-native-target.yml").read_text()
        )
        job = document["jobs"]["image"]
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
        steps = job["steps"]
        publication = next(
            step
            for step in steps
            if step.get("name") == "Publish and verify registry-addressable candidate"
        )
        self.assertEqual(publication["if"], "inputs.publish")
        self.assertIn('docker pull --platform linux/amd64 "$reference"', publication["run"])
        self.assertIn('verify --image "$reference"', publication["run"])
        self.assertIn("GITHUB_STEP_SUMMARY", publication["run"])


if __name__ == "__main__":
    unittest.main()
