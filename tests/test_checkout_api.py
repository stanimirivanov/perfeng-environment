"""Checkout API sessions bound the time spent waiting for the selected forward."""

import subprocess
import unittest
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import MagicMock, patch

from scripts import checkout_api


class CheckoutApiTests(unittest.TestCase):
    def test_request_deadline_interrupts_slow_headers_and_body(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "operator.token"
            token.write_text("token")
            token.chmod(0o600)
            for phase in ("headers", "body"):
                with self.subTest(phase=phase):
                    self.check_deadline(root, token, phase)

    def check_deadline(self, root, token, phase):
        stopped = Event()

        def forward(command, *, stdout, stderr):
            self.assertEqual(stderr, subprocess.DEVNULL)
            stdout.write(b"Forwarding from 127.0.0.1:12345 -> 8080\n")
            stdout.flush()
            process = MagicMock()
            process.__enter__.return_value = process
            process.poll.return_value = None
            process.kill.side_effect = stopped.set
            return process

        def wait_for_disconnect(*args):
            self.assertTrue(stopped.wait(1), "request overran its deadline")
            raise ConnectionResetError

        conn = MagicMock()
        response = conn.getresponse.return_value
        response.status = 200
        response.headers = Message()
        response.headers["Content-Type"] = "application/json"
        if phase == "headers":
            conn.getresponse.side_effect = wait_for_disconnect
        else:
            response.read.side_effect = wait_for_disconnect

        with (
            patch.object(checkout_api, "REQUEST_TIMEOUT", 0.05),
            patch.object(
                checkout_api.native_connectivity,
                "command",
                return_value=["forward", "8080"],
            ),
            patch.object(checkout_api.subprocess, "Popen", side_effect=forward),
            patch.object(checkout_api.http.client, "HTTPConnection", return_value=conn),
            checkout_api.session(root, token) as api,
            self.assertRaises(ValueError),
        ):
            api("/runs")
