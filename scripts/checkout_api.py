"""Bounded authenticated checkout API calls through an owned loopback port-forward."""

import contextlib
import http.client
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Timer

from scripts import native_connectivity
from scripts.checkout_checkpoint import private_bytes
from scripts.kubernetes_benchmark_contract import document

REQUEST_TIMEOUT = 15


@contextlib.contextmanager
def session(root: Path, token_file: Path):
    token = private_bytes(token_file, 8192).decode("ascii").rstrip("\r\n")
    if not re.fullmatch(r"[!-~]+", token):
        raise ValueError("Invalid operator token file")
    command = native_connectivity.command("forward-api", root)
    command[-1] = ":8080"
    with (
        tempfile.TemporaryFile() as output,
        subprocess.Popen(command, stdout=output, stderr=subprocess.DEVNULL) as process,
    ):
        try:
            deadline = time.monotonic() + 35
            while True:
                line = os.pread(output.fileno(), 160, 0).partition(b"\n")
                if line[1]:
                    match = re.fullmatch(rb"Forwarding from 127\.0\.0\.1:(\d+) -> 8080", line[0])
                    if not match:
                        raise ValueError("Selected API forward failed to bind")
                    port = int(match[1])
                    break
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError("Selected API forward failed or timed out")
                time.sleep(0.1)

            def api(path, *, request=None, key=None):
                if process.poll() is not None:
                    raise ValueError("Selected API forward stopped")
                headers = {"Authorization": "Bearer " + token}
                method, body, expected = "GET", None, 200
                if request is not None:
                    if not isinstance(key, str):
                        raise ValueError("Missing submission identity")
                    method, body, expected = "POST", json.dumps(request).encode(), 201
                    headers.update({"Content-Type": "application/json", "Idempotency-Key": key})
                with contextlib.closing(
                    http.client.HTTPConnection("127.0.0.1", port, timeout=REQUEST_TIMEOUT)
                ) as conn:
                    # A socket timeout only limits inactivity between successive bytes.
                    deadline = time.monotonic() + REQUEST_TIMEOUT
                    timer = Timer(REQUEST_TIMEOUT, process.kill)
                    timer.start()
                    try:
                        conn.request(method, path, body=body, headers=headers)
                        response = conn.getresponse()
                        if response.status != expected:
                            raise ValueError(
                                f"Authenticated {method} returned HTTP {response.status}"
                            )
                        if response.headers.get_content_type() != "application/json":
                            raise ValueError("Unexpected API media type")
                        content = response.read((1 << 20) + 1)
                    except (OSError, http.client.HTTPException):
                        raise ValueError(f"Authenticated {method} unavailable") from None
                    finally:
                        timer.cancel()
                        timer.join()
                    if time.monotonic() >= deadline:
                        raise ValueError(f"Authenticated {method} timed out")
                if len(content) > 1 << 20:
                    raise ValueError("Oversized API response")
                try:
                    return document(content)
                except (ValueError, RecursionError):
                    raise ValueError("Invalid API response") from None

            yield api
        finally:
            process.kill()
