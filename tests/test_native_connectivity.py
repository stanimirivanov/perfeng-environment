"""Gateway checks must preserve TLS, local identity and preview-only safety."""

import contextlib
import io
import json
import ssl
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import native_connectivity as gateway


class NativeConnectivityTests(unittest.TestCase):
    def setUp(self):
        health = patch.object(
            gateway.native_target_health, "verify", return_value="native-search-fixture"
        )
        self.health = health.start()
        self.addCleanup(health.stop)

    def responses(self):
        return [
            (200, b"ok\n"),
            (200, (gateway.ROOT / "native-gateway/search/index.html").read_bytes()),
            (200, b"ready\n"),
            (200, (gateway.ROOT / "native-gateway/search/index.html").read_bytes()),
            (200, (gateway.ROOT / "native-gateway/search/search.js").read_bytes()),
            (403, b"<Error><Code>AccessDenied</Code></Error>"),
        ]

    def test_preview_all_actions_is_offline_without_a_ca(self):
        with patch.object(gateway, "run_tool") as run, patch.object(gateway, "check") as check:
            for action in (
                "gateway",
                "forward-api",
                "forward-s3",
                "forward-target",
                "validate",
                "check",
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(gateway.main([action]), 0)
            run.assert_not_called()
            check.assert_not_called()
            self.health.assert_not_called()

    def test_forward_commands_are_dedicated_and_loopback_only(self):
        for action, (service, ports) in gateway.FORWARDS.items():
            command = gateway.command(action)
            self.assertIn("--address=127.0.0.1", command)
            self.assertIn("kind-perfeng-local", command)
            self.assertIn("--request-timeout=10s", command)
            self.assertEqual(command[-2:], [f"service/{service}", ports])
            self.assertIn("perfeng-local.kubeconfig", command[2])

    def test_foreign_cluster_prevents_service_query_and_forward(self):
        with (
            patch.object(gateway.cluster, "verify_local_docker"),
            patch.object(
                gateway.cluster, "verify_context", side_effect=ValueError("foreign cluster")
            ),
            patch.object(gateway.cluster, "run") as query,
            patch.object(gateway.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "foreign cluster"):
                gateway.run_tool("forward-api")
            query.assert_not_called()
            run.assert_not_called()

    def test_s3_service_checked_before_forward(self):
        service = {
            "metadata": {"name": "seaweedfs", "namespace": "perf-platform"},
            "spec": {
                "type": "ClusterIP",
                "clusterIP": "None",
                "selector": {
                    "app.kubernetes.io/instance": "seaweedfs",
                    "app.kubernetes.io/name": "seaweedfs",
                },
                "ports": [{"name": "s3", "port": 8333, "protocol": "TCP", "targetPort": "s3"}],
            },
        }
        with (
            patch.object(gateway.cluster, "verify_local_docker"),
            patch.object(gateway.cluster, "verify_context"),
            patch.object(gateway.cluster, "run", return_value=json.dumps(service)) as query,
        ):
            gateway.verify_forward("forward-s3", gateway.ROOT)
            self.assertEqual(
                query.call_args.args[0][-5:], ["get", "service", "seaweedfs", "-o", "json"]
            )
            service["spec"]["selector"] = {}
            query.return_value = json.dumps(service)
            with self.assertRaisesRegex(ValueError, "Service differs"):
                gateway.verify_forward("forward-s3", gateway.ROOT)

    def test_caddy_version_and_session_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(gateway.subprocess, "run") as run:
                run.side_effect = lambda command, **kwargs: subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=(
                        b"go\tgo1.26.9\ndep\tgolang.org/x/net\tv0.60.0\th1:fixture\n"
                        if command == ["caddy", "build-info"]
                        else b"v2.11.7 fixture\n"
                    ),
                )
                gateway.run_tool("gateway", root)
                self.assertEqual(run.call_args.kwargs["timeout"], 3600)
                self.assertEqual(run.call_args.kwargs["cwd"], root)
                gateway.run_tool("validate", root)
                self.assertEqual(run.call_args.kwargs["timeout"], 10)
                run.reset_mock()
                run.side_effect = None
                run.return_value = subprocess.CompletedProcess([], 0, stdout=b"v2.10.0\n")
                with self.assertRaisesRegex(ValueError, "reviewed Caddy"):
                    gateway.run_tool("gateway", root)
                self.assertEqual(run.call_count, 1)

    def test_serving_rejects_known_vulnerable_or_unverifiable_builds(self):
        approved = "go\tgo1.26.9\ndep\tgolang.org/x/net\tv0.60.0\th1:fixture\n"
        gateway.verify_serving_build(approved)
        gateway.verify_serving_build(approved.replace("go1.26.9", "go1.26.10"))
        for rejected in (
            approved.replace("go1.26.9", "go1.26.8"),
            approved.replace("v0.60.0", "v0.59.0"),
            approved.replace("go1.26.9", "devel"),
            "",
            approved + "=>\t../unreviewed\t(devel)\n",
        ):
            with self.assertRaisesRegex(ValueError, "serving requires"):
                gateway.verify_serving_build(rejected)

    def test_malformed_service_shape_fails_before_forward(self):
        for value in ([], None, {"metadata": [], "spec": {}}, {"metadata": {}, "spec": []}):
            with (
                patch.object(gateway.cluster, "verify_local_docker"),
                patch.object(gateway.cluster, "verify_context"),
                patch.object(gateway.cluster, "run", return_value=json.dumps(value)),
                patch.object(gateway.subprocess, "run") as run,
            ):
                with self.assertRaisesRegex(ValueError, "Invalid Service"):
                    gateway.run_tool("forward-api")
                run.assert_not_called()

    def test_redirected_local_state_prevents_gateway_start(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(gateway.subprocess, "run") as run,
            patch.object(Path, "is_symlink", return_value=True),
        ):
            run.return_value = subprocess.CompletedProcess([], 0, stdout=b"v2.11.7\n")
            with self.assertRaisesRegex(ValueError, "Redirected gateway state"):
                gateway.run_tool("gateway", Path(directory).resolve())
            self.assertEqual(run.call_count, 1)

    def test_release_checksum_selection_agrees_with_tool_version(self):
        release = json.loads((gateway.ROOT / "native-gateway/caddy-release.json").read_text())
        self.assertEqual("v" + release["version"], gateway.CADDY_VERSION)
        self.assertEqual(set(release["sha512"]), {"linux_amd64", "windows_amd64"})
        for value in release["sha512"].values():
            self.assertRegex(value, r"^[0-9a-f]{128}$")

    def test_check_requires_explicit_ca_and_preserves_tls_verification(self):
        with patch.object(
            gateway.ssl,
            "create_default_context",
            return_value=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        ) as create:
            with patch.object(gateway, "read_endpoint", side_effect=self.responses()) as read:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    gateway.check(Path("reviewed-root.crt"))
                create.assert_called_once_with(cafile="reviewed-root.crt")
                self.assertEqual(read.call_count, 6)
                self.assertTrue(all(call.args[1].check_hostname for call in read.call_args_list))
                self.assertIn("not verified", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()), patch.object(gateway, "check") as check:
            self.assertEqual(gateway.main(["check", "--execute"]), 1)
            check.assert_not_called()

    def test_check_rejects_unready_redirected_oversized_changed_or_anonymous_storage(self):
        for index, response in (
            (0, (503, b"not ready")),
            (0, (200, b"other server")),
            (0, (302, b"redirect")),
            (0, (200, b"x" * (gateway.MAX_RESPONSE_BYTES + 1))),
            (1, (200, b"different fixture")),
            (2, (200, b"different health")),
            (4, (200, b"different script")),
            (5, (200, b"public bucket")),
            (5, (403, b"proxy error")),
        ):
            responses = self.responses()
            responses[index] = response
            with self.subTest(index=index, response=response[0]):
                with (
                    patch.object(gateway.ssl, "create_default_context"),
                    patch.object(gateway, "read_endpoint", side_effect=responses),
                ):
                    with self.assertRaises(ValueError), contextlib.redirect_stdout(io.StringIO()):
                        gateway.check(Path("root.crt"))

    def test_transport_refuses_redirects_and_environment_proxies(self):
        response = Mock(status=200)
        response.read.return_value = b"ok\n"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(gateway.urllib.request, "build_opener") as build:
            build.return_value.open.return_value = response
            context = ssl.create_default_context()
            self.assertEqual(
                gateway.read_endpoint(gateway.ORIGINS["controlPlane"], context), (200, b"ok\n")
            )
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIs(build.call_args.args[1]._context, context)
            redirect = build.call_args.args[2]
            self.assertIsNone(
                redirect.redirect_request(None, None, 302, "found", {}, "https://foreign.invalid")
            )
            response.read.assert_called_once_with(gateway.MAX_RESPONSE_BYTES + 1)

    def test_target_forward_uses_verified_pod_not_a_service_selected_replacement(self):
        with patch.object(gateway.subprocess, "run") as run:
            gateway.run_tool("forward-target")
        self.health.assert_called_once_with(gateway.ROOT)
        self.assertEqual(run.call_args.args[0][-2:], ["pod/native-search-fixture", "18081:8080"])
        self.assertEqual(run.call_args.kwargs["timeout"], 3600)

    def test_target_identity_failure_prevents_forward_and_https_checks(self):
        self.health.side_effect = ValueError("wrong candidate image")
        with patch.object(gateway.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "wrong candidate"):
                gateway.run_tool("forward-target")
            run.assert_not_called()
        with patch.object(gateway, "read_endpoint") as read:
            with self.assertRaisesRegex(ValueError, "wrong candidate"):
                gateway.check(Path("root.crt"))
            read.assert_not_called()

    def test_target_pod_replacement_during_check_is_not_accepted(self):
        self.health.side_effect = ["native-search-before", "native-search-after"]
        with (
            patch.object(gateway.ssl, "create_default_context"),
            patch.object(gateway, "read_endpoint", side_effect=self.responses()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(ValueError, "Pod changed"):
                gateway.check(Path("root.crt"))

    def test_gateway_is_proxy_only_for_the_target(self):
        configuration = (gateway.ROOT / "native-gateway/Caddyfile").read_text()
        self.assertIn("reverse_proxy 127.0.0.1:18081", configuration)
        self.assertNotIn("file_server", configuration)
        self.assertNotIn("root *", configuration)

    def test_transport_failure_does_not_echo_sensitive_vendor_text(self):
        with patch.object(gateway.urllib.request, "build_opener") as build:
            build.return_value.open.side_effect = urllib.error.URLError("secret-signature")
            with self.assertRaises(ValueError) as raised:
                gateway.read_endpoint(gateway.ORIGINS["s3"], ssl.create_default_context())
            self.assertNotIn("secret-signature", str(raised.exception))

    def test_target_identity_changes_with_exact_bytes_not_checkout_directory(self):
        import shutil

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shutil.copytree(gateway.ROOT / "native-gateway/search", root / "native-gateway/search")
            self.assertEqual(gateway.target_identity(), gateway.target_identity(root))
            (root / "native-gateway/search/search.js").write_bytes(b"different")
            self.assertNotEqual(gateway.target_identity(), gateway.target_identity(root))


if __name__ == "__main__":
    unittest.main()
