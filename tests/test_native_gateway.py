"""Managed native setup and lifecycle must fail safely and expose useful classifications."""

import contextlib
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import cluster
from scripts import native_gateway as gateway
from scripts import native_gateway_doctor as doctor
from scripts import native_gateway_host as host
from scripts import native_gateway_release as release
from scripts import native_gateway_tool as tool
from scripts.native_gateway_processes import ProcessScope

INFO = "go\tgo1.26.9\ndep\tgithub.com/caddyserver/caddy/v2\tv2.11.7\th1:fixture\ndep\tgolang.org/x/net\tv0.60.0\th1:fixture\nbuild\tGOOS=windows\nbuild\tGOARCH=amd64\n"


def redirect_headers(location: str) -> Message:
    headers = Message()
    headers["Location"] = location
    return headers


def selection(data: bytes = b"binary") -> dict:
    return {
        "schemaVersion": 1,
        "platform": "windows/amd64",
        "caddyVersion": "v2.11.7",
        "goVersion": "go1.26.9",
        "release": {
            "tag": "native-gateway-" + "a" * 40,
            "assetId": 7,
            "sha256": hashlib.sha256(data).hexdigest(),
            "sizeBytes": len(data),
            "sourceRevision": "a" * 40,
        },
    }


class GatewayToolTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(tool, "private_state")
        self.private = guard.start()
        self.addCleanup(guard.stop)

    def test_ambiguous_boolean_schema_and_duplicate_fields_are_rejected(self):
        value = selection()
        value["schemaVersion"] = True
        with self.assertRaisesRegex(tool.GatewayError, "RELEASE_INVALID"):
            tool.parse_selection(value)
        with self.assertRaisesRegex(tool.GatewayError, "Duplicate"):
            json.loads('{"release": null, "release": {}}', object_pairs_hook=tool.unique_object)

    def test_real_unknown_version_is_classified_with_path_expected_and_observed(self):
        with patch.object(
            tool.subprocess,
            "run",
            side_effect=[
                subprocess.CompletedProcess([], 0, stdout=b"unknown\n"),
                subprocess.CompletedProcess([], 0, stdout=INFO.encode()),
            ],
        ):
            with self.assertRaisesRegex(
                tool.GatewayError, r"TOOL_VERSION.*expected v2.11.7; observed unknown"
            ) as raised:
                tool.metadata(Path("managed/caddy.exe"))
        self.assertIn("managed", str(raised.exception))
        self.assertIn("native:setup", str(raised.exception))

    def test_security_provenance_and_platform_are_required(self):
        for info, code in (
            (INFO, None),
            (INFO.replace("go1.26.9", "go1.26.8"), "TOOL_SECURITY"),
            (INFO.replace("v0.60.0", "v0.59.0"), "TOOL_SECURITY"),
            (INFO.replace("v2.11.7", "v2.11.6"), "TOOL_PROVENANCE"),
            (INFO + "=>\t../replacement\t(devel)\n", "TOOL_PROVENANCE"),
            (INFO.replace("GOOS=windows", "GOOS=linux"), "TOOL_PLATFORM"),
        ):
            with (
                self.subTest(code=code),
                patch.object(
                    tool.subprocess,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, stdout=b"v2.11.7 fixture\n"),
                        subprocess.CompletedProcess([], 0, stdout=info.encode()),
                    ],
                ),
            ):
                if code:
                    with self.assertRaisesRegex(tool.GatewayError, code):
                        tool.metadata(Path("caddy.exe"))
                else:
                    self.assertEqual(tool.metadata(Path("caddy.exe")), INFO)

    def test_metadata_never_echoes_vendor_stderr(self):
        with patch.object(
            tool.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, [], stderr=b"secret-signature"),
        ):
            with self.assertRaises(tool.GatewayError) as raised:
                tool.metadata(Path("caddy.exe"))
        self.assertNotIn("secret-signature", str(raised.exception))

    def test_security_shell_uses_builtin_path_and_isolates_core_module_lookup(self):
        with (
            patch.dict(
                host.os.environ,
                {"SystemRoot": "C:/Windows", "PSModulePath": "unrelated-core-modules"},
            ),
            patch.object(
                host.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout="private"),
            ) as run,
        ):
            self.assertEqual(host.powershell("fixed-script", Path("private-state")), "private")
        self.assertNotIn("PSModulePath", run.call_args.kwargs["env"])
        self.assertEqual(Path(run.call_args.args[0][0]).name, "powershell.exe")
        self.assertIn("WindowsPowerShell", run.call_args.args[0][0])
        self.assertEqual(run.call_args.kwargs["timeout"], host.POWERSHELL_TIMEOUT)
        self.assertGreater(host.POWERSHELL_TIMEOUT, 20)
        self.assertLess(host.POWERSHELL_TIMEOUT, 45)

    def test_security_timeout_is_distinct_sanitized_and_never_retried(self):
        failures = (
            (
                subprocess.TimeoutExpired(
                    ["powershell", "private-command"],
                    host.POWERSHELL_TIMEOUT,
                    output="private-output",
                    stderr="private-error",
                ),
                "HOST_SECURITY_TIMEOUT",
            ),
            (
                subprocess.CalledProcessError(
                    1,
                    ["powershell", "private-command"],
                    output="private-output",
                    stderr="private-error",
                ),
                "HOST_SECURITY",
            ),
        )
        for failure, code in failures:
            with (
                self.subTest(code=code),
                patch.dict(host.os.environ, {"SystemRoot": "C:/Windows"}),
                patch.object(host.subprocess, "run", side_effect=failure) as run,
                self.assertRaises(tool.GatewayError) as raised,
            ):
                host.powershell(host.SECURE_SCRIPT, Path("private-state"))
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("private-output", str(raised.exception))
            self.assertNotIn("private-error", str(raised.exception))
            self.assertNotIn("private-command", str(raised.exception))
            run.assert_called_once()

    def test_unpublished_release_fails_closed(self):
        value = selection()
        value["release"] = None
        with self.assertRaisesRegex(tool.GatewayError, "RELEASE_UNSELECTED"):
            tool.parse_selection(value)

    def test_invalid_asset_and_source_inputs_are_rejected(self):
        for field, bad in (
            ("assetId", True),
            ("assetId", 0),
            ("sha256", "latest"),
            ("sizeBytes", tool.MAX_BINARY_BYTES + 1),
            ("sourceRevision", "main"),
            ("tag", "latest"),
        ):
            value = selection()
            value["release"][field] = bad
            with self.subTest(field=field), self.assertRaises(tool.GatewayError):
                tool.parse_selection(value)

    def test_setup_is_repeatable_and_does_not_overwrite_modified_or_legacy_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "native-gateway").mkdir()
            (root / "native-gateway/serving-release.json").write_text(json.dumps(selection()))
            state = root / ".local/native-gateway"
            state.mkdir(parents=True)
            legacy = state / "caddy.exe"
            legacy.write_bytes(b"legacy")
            with (
                patch.object(
                    tool, "download", side_effect=lambda _, path: path.write_bytes(b"binary")
                ) as download,
                patch.object(tool, "metadata"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                path = tool.setup(root)
                self.assertEqual(tool.setup(root), path)
                download.assert_called_once()
                self.assertEqual(legacy.read_bytes(), b"legacy")
                path.write_bytes(b"tampered")
                with self.assertRaisesRegex(tool.GatewayError, "TOOL_CHECKSUM"):
                    tool.setup(root)
                self.assertEqual(path.read_bytes(), b"tampered")

    def test_bad_download_is_never_executed_or_installed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "native-gateway").mkdir()
            (root / "native-gateway/serving-release.json").write_text(json.dumps(selection()))
            (root / ".local/native-gateway").mkdir(parents=True)
            with (
                patch.object(
                    tool, "download", side_effect=lambda _, path: path.write_bytes(b"wrong")
                ),
                patch.object(tool, "metadata") as metadata,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaisesRegex(tool.GatewayError, "DOWNLOAD_CHECKSUM"):
                    tool.setup(root)
                metadata.assert_not_called()
            self.assertFalse(tool.executable(root, tool.selection(root)).exists())

    def test_selection_preview_is_read_only_and_execute_validates_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "native-gateway").mkdir()
            record = root / "release-selection.json"
            record.write_text(json.dumps(selection()))
            with contextlib.redirect_stdout(io.StringIO()):
                release.select(record, root)
                self.assertFalse((root / "native-gateway/serving-release.json").exists())
                release.select(record, root, execute=True)
            self.assertEqual(tool.selection(root).asset_id, 7)

    def test_private_download_drops_authorization_at_storage_redirect(self):
        redirect = urllib.error.HTTPError(
            "https://api.github.com/asset",
            302,
            "redirect",
            redirect_headers("https://release-assets.githubusercontent.com/asset?sig=fixture"),
            None,
        )
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [b"binary", b""]
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                tool.os.environ, {"PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN": "fixture-token"}
            ),
            patch.object(tool.urllib.request, "build_opener") as build,
        ):
            build.return_value.open.side_effect = [redirect, response]
            target = Path(temporary) / "caddy.exe"
            tool.download(tool.parse_selection(selection()), target)
            self.assertEqual(target.read_bytes(), b"binary")
            first, second = build.return_value.open.call_args_list
            self.assertEqual(first.args[0].get_header("Authorization"), "Bearer fixture-token")
            self.assertIsNone(second.args[0].get_header("Authorization"))

    def test_foreign_redirect_and_missing_auth_are_safe_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "caddy.exe"
            with (
                patch.dict(tool.os.environ, {}, clear=True),
                self.assertRaisesRegex(tool.GatewayError, "REPOSITORY_AUTH"),
            ):
                tool.download(tool.parse_selection(selection()), target)
            redirect = urllib.error.HTTPError(
                "https://api.github.com/asset",
                302,
                "redirect",
                redirect_headers("https://foreign.invalid/?credential=fixture"),
                None,
            )
            with (
                patch.dict(
                    tool.os.environ, {"PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN": "fixture-token"}
                ),
                patch.object(tool.urllib.request, "build_opener") as build,
            ):
                build.return_value.open.side_effect = redirect
                with self.assertRaisesRegex(tool.GatewayError, "DOWNLOAD_REDIRECT") as raised:
                    tool.download(tool.parse_selection(selection()), target)
                self.assertNotIn("credential=", str(raised.exception))
                self.assertEqual(build.return_value.open.call_count, 1)

    def test_build_and_publisher_use_real_locked_windows_checks(self):
        import yaml

        workflow = yaml.safe_load((tool.ROOT / ".github/workflows/native-gateway.yml").read_text())
        build = workflow["jobs"]["windows-serving"]
        self.assertEqual(build["runs-on"], "windows-latest")
        self.assertIn("native_gateway_release build", str(build))
        self.assertIn("native_gateway_smoke", str(build))
        publisher = workflow["jobs"]["publish"]
        self.assertIn("inputs.publish", publisher["if"])
        self.assertIn("refs/heads/main", publisher["if"])
        self.assertEqual(publisher["needs"], "windows-serving")
        self.assertIn("record.development", str(publisher))


class GatewayWorkflowTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows OWNER RIGHTS ACL semantics")
    def test_python_private_temp_directory_and_hard_link_remain_account_private(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary).resolve()
            host.secure_permissions(root)
            state = tool.safe_state(root)
            with tempfile.TemporaryDirectory(dir=state) as download:
                source = Path(download) / "fixture"
                source.write_bytes(b"fixture")
                target = state / "tools/fixture/caddy.exe"
                target.parent.mkdir(parents=True)
                os.link(source, target)
                host.private_permissions(root)

    def test_every_cli_preview_is_offline_and_has_no_writes(self):
        for action in ("setup", "doctor", "permissions", "up"):
            with (
                patch.object(gateway.tool, "setup") as setup,
                patch.object(gateway.doctor, "collect") as collect,
                patch.object(gateway.host, "secure_permissions") as permissions,
                patch.object(gateway, "up") as up,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(gateway.main([action]), 0)
                for operation in (setup, collect, permissions, up):
                    operation.assert_not_called()

    def test_doctor_collects_independent_failures_without_foreign_resource_reads(self):
        with (
            patch.object(
                doctor,
                "installed",
                side_effect=tool.GatewayError("TOOL_MISSING", "missing", "setup"),
            ),
            patch.object(host, "private_permissions", side_effect=ValueError("private")),
            patch.object(doctor.cluster, "verify_local_docker", side_effect=ValueError("foreign")),
            patch.object(doctor, "cluster_ready", side_effect=ValueError("unavailable")),
            patch.object(doctor.connectivity, "verify_forward") as forward,
            patch.object(
                doctor,
                "port_available",
                side_effect=tool.GatewayError("PORT_CONFLICT", "occupied", "inspect"),
            ),
            patch.object(host, "fingerprint", return_value=None),
        ):
            results = doctor.collect(Path("."))
        forward.assert_not_called()
        self.assertEqual(sum(result.status == "FAIL" for result in results), 10)
        self.assertEqual(sum(result.status == "NOT CHECKED" for result in results), 3)
        self.assertTrue(any(result.status == "ACTION REQUIRED" for result in results))

    def test_occupied_port_is_a_conflict_not_a_takeover(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.assertRaisesRegex(tool.GatewayError, "PORT_CONFLICT"):
                doctor.port_available(listener.getsockname()[1])
            self.assertNotEqual(listener.fileno(), -1)

    def test_command_budget_terminates_a_stalled_probe_and_restores_context(self):
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.communicate.return_value = (b"", b"private-vendor-data")
        with (
            patch.object(cluster.subprocess, "Popen", return_value=process),
            patch.object(cluster.time, "monotonic", side_effect=[0, 0, 2]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(ValueError, "bounded command deadline"):
                with cluster.command_deadline(1):
                    cluster.run(["kind", "get", "clusters"])
        process.kill.assert_called_once()
        self.assertIsNone(cluster._COMMAND_DEADLINE.get())

    def test_child_exit_and_expired_startup_are_classified(self):
        scope = ProcessScope()
        child = Mock()
        child.poll.return_value = 7
        scope.children.append(("owned-fixture", child))
        try:
            with self.assertRaisesRegex(tool.GatewayError, "CHILD_EXIT.*status 7"):
                scope.check()
        finally:
            scope.children.clear()
            scope.close()
        with (
            patch.object(gateway.time, "monotonic", return_value=10),
            self.assertRaisesRegex(tool.GatewayError, "STARTUP_TIMEOUT"),
        ):
            gateway.wait_port(Mock(), 8443, 5)

    def test_failed_preflight_starts_no_children(self):
        with (
            patch.object(
                doctor, "collect", return_value=[doctor.Result("tool", "FAIL", "missing")]
            ),
            patch.object(gateway, "ProcessScope") as scope,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(tool.GatewayError, "PREFLIGHT"):
                gateway.up()
            scope.assert_not_called()

    def run_supervisor(self, failure: str):
        scope = Mock()
        with (
            patch.object(doctor, "collect", return_value=[]),
            patch.object(tool, "installed", return_value=Path("managed/caddy.exe")),
            patch.object(host, "private_permissions"),
            patch.object(host, "ca_file", return_value=Path("root.crt")),
            patch.object(host, "fingerprint", return_value="a" * 64),
            patch.object(
                gateway.connectivity, "verify_forward", return_value="native-search-exact"
            ),
            patch.object(gateway.connectivity, "check") as check,
            patch.object(gateway, "ProcessScope", return_value=scope),
            patch.object(
                gateway,
                "wait_port",
                side_effect=tool.GatewayError("STARTUP_TIMEOUT", "port", "doctor")
                if failure == "startup"
                else None,
            ),
            patch.object(gateway.time, "sleep", side_effect=KeyboardInterrupt),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(
                tool.GatewayError if failure == "startup" else KeyboardInterrupt
            ):
                gateway.up()
        scope.close.assert_called_once()
        if failure != "startup":
            self.assertEqual(scope.start.call_count, 4)
            self.assertEqual(scope.start.call_args_list[2].args[1][-2], "pod/native-search-exact")
            self.assertEqual(
                scope.start.call_args_list[3].args[1][0], str(Path("managed/caddy.exe"))
            )
            self.assertEqual(check.call_args.kwargs["expected_pod"], "native-search-exact")

    def test_interrupt_stops_every_owned_child(self):
        self.run_supervisor("interrupt")

    def test_partial_startup_cleans_up_without_starting_the_gateway(self):
        self.run_supervisor("startup")

    def test_real_process_scope_stops_its_child_but_not_an_unrelated_process(self):
        foreign = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        scope = ProcessScope()
        try:
            scope.start(
                "owned-fixture", [sys.executable, "-c", "import time; time.sleep(60)"], Path.cwd()
            )
            scope.check()
            scope.close()
            self.assertIsNotNone(scope.children[0][1].poll())
            self.assertIsNone(foreign.poll())
        finally:
            scope.close()
            foreign.terminate()
            foreign.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
