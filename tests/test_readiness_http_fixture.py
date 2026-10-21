"""Exercise the CI peer with the actual production HTTP refusal policy."""

import importlib.util
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import Request, build_opener, urlopen

from tests.release_verifier_support import runtime

SOURCE = Path(__file__).parent / "integration/release-workflows/readiness_server.py"
SPEC = importlib.util.spec_from_file_location("readiness_http_fixture", SOURCE)
assert SPEC is not None and SPEC.loader is not None
peer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(peer)


class ReadinessHTTPFixtureTests(unittest.TestCase):
    def test_production_probes_accept_only_healthy_peer_and_never_follow_redirects(self):
        # Route only the fixed endpoint address to an isolated loopback socket;
        # production handlers, timeouts and response checks still execute.
        with peer.FixtureServer(("127.0.0.1", 0)) as server:
            address = f"http://127.0.0.1:{server.server_address[1]}"
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()

            class LocalPeer:
                def __init__(self, *handlers):
                    self.opener = build_opener(*handlers)

                def open(self, url, *, timeout):
                    return self.opener.open(
                        url.replace(
                            "http://control-plane.perf-platform.svc.cluster.local:8080", address
                        ),
                        timeout=timeout,
                    )

            try:
                for mode, failed_endpoint, expected_counts in (
                    ("healthy", None, [1, 1, 1, 0]),
                    ("readyz-unavailable", "readyz", [1, 1, 0, 0]),
                    ("metrics-redirect", "metrics", [1, 1, 1, 0]),
                    ("metrics-invalid", "metrics", [1, 1, 1, 0]),
                    ("healthy", None, [1, 1, 1, 0]),
                ):
                    with self.subTest(mode=mode):
                        with urlopen(
                            Request(address + "/fixture-mode/" + mode, method="POST"), timeout=5
                        ) as response:
                            self.assertEqual(response.status, 200)
                        with patch.object(runtime, "build_opener", LocalPeer):
                            if failed_endpoint:
                                with self.assertRaisesRegex(
                                    ValueError, "operational endpoint failed: " + failed_endpoint
                                ):
                                    runtime.probe_endpoints()
                            else:
                                runtime.probe_endpoints()
                        with urlopen(address + "/fixture-state", timeout=5) as response:
                            counts = json.load(response)
                        self.assertEqual(list(counts.values()), expected_counts)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_non_loopback_clients_cannot_read_or_change_fixture_control_state(self):
        handler = object.__new__(peer.Handler)
        handler.client_address = ("10.244.0.7", 12345)
        handler.path = "/fixture-mode/metrics-invalid"
        handler.reply = Mock()
        handler.do_POST()
        handler.reply.assert_called_once_with(403, b"")
        handler.reply.reset_mock()
        handler.path = "/fixture-state"
        handler.do_GET()
        handler.reply.assert_called_once_with(403, b"")


if __name__ == "__main__":
    unittest.main()
