"""Exercise generated POSIX scripts without Kubernetes or S3 (Linux CI)."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import k6_run as runner

RUN = "perf-20260902-120000-abcdef12"


@unittest.skipIf(os.name == "nt", "Generated container scripts require a POSIX shell")
class CaptureShellTests(unittest.TestCase):
    def test_runner_preserves_exit_status_and_raw_bytes(self):
        for code in [0, 99, 107]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                results, tests = root / "results", root / "tests"
                results.mkdir()
                for name in ["workloads", "definitions"]:
                    (tests / name / "smoke").mkdir(parents=True)
                    (tests / name / "smoke/checkout.json").write_bytes(b"{}\n")
                fake = root / "k6"
                fake.write_text(
                    "#!/bin/sh\n"
                    'printf \'{"metrics":{}}\\n\' > "$CAPTURE_RESULTS/summary.json"\n'
                    'printf \'{"type":"Point"}\\n\' > "$CAPTURE_RESULTS/points.jsonl"\n'
                    'echo diagnostic\nexit "$CAPTURE_EXIT"\n'
                )
                fake.chmod(0o700)
                with patch.object(runner, "CONFIG_HASH", hashlib.sha256(b"{}\n").hexdigest()):
                    script = runner.runner_script()
                script = script.replace("/results", str(results)).replace("/tests", str(tests))
                completed = subprocess.run(
                    ["/bin/sh", "-c", script],
                    capture_output=True,
                    timeout=10,
                    env={
                        **os.environ,
                        "PATH": str(root) + os.pathsep + os.environ["PATH"],
                        "CAPTURE_RESULTS": str(results),
                        "CAPTURE_EXIT": str(code),
                    },
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(
                    json.loads((results / "status.json").read_text())["exitCode"], code
                )
                self.assertEqual((results / "summary.json").read_bytes(), b'{"metrics":{}}\n')

    def test_upload_receipt_failure_readback_and_duplicate_protection(self):
        for fault in ["none", "put", "get"]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                results, scratch, storage = root / "results", root / "scratch", root / "s3"
                for path in [results, scratch, storage]:
                    path.mkdir()
                for name in ["workload.json", "workload-definition.json", "summary.json"]:
                    (results / name).write_bytes(b"{}\n")
                (results / "status.json").write_text('{"exitCode":99}\n')
                (results / "points.jsonl").write_text('{"type":"Point"}\n')
                (results / "runner.log").write_text("diagnostic\n")
                fake = root / "aws"
                fake.write_text(
                    "#!" + sys.executable + "\n"
                    "import os, pathlib, sys\n"
                    "args = sys.argv[1:]\n"
                    "action = args[args.index('s3api') + 1]\n"
                    "key = args[args.index('--key') + 1]\n"
                    "obj = pathlib.Path(os.environ['CAPTURE_STORE']) / key\n"
                    "if action == 'put-object':\n"
                    "    assert args[args.index('--if-none-match') + 1] == '*'\n"
                    "    if os.environ['CAPTURE_FAULT'] == 'put': sys.exit(1)\n"
                    "    obj.parent.mkdir(parents=True, exist_ok=True)\n"
                    "    with obj.open('xb') as out:\n"
                    "        out.write(pathlib.Path(args[args.index('--body') + 1]).read_bytes())\n"
                    "elif action == 'get-object':\n"
                    "    data = obj.read_bytes()\n"
                    "    if os.environ['CAPTURE_FAULT'] == 'get': data = b'corrupted'\n"
                    "    pathlib.Path(args[-1]).write_bytes(data)\n"
                    "else: sys.exit(2)\n"
                )
                fake.chmod(0o700)
                script = runner.upload_script(RUN).replace("/tmp/", str(scratch) + "/")
                script = script.replace("/results", str(results))
                environment = {
                    **os.environ,
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "CAPTURE_STORE": str(storage),
                    "CAPTURE_FAULT": fault,
                }
                completed = subprocess.run(
                    ["/bin/sh", "-c", script],
                    capture_output=True,
                    timeout=30,
                    env=environment,
                )
                receipt = storage / "runs" / RUN / "receipt.json"
                if fault != "none":
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertFalse(receipt.exists())
                    continue
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(runner.outcome(completed.stdout.decode(), RUN), 99)
                inventory = json.loads(receipt.read_text())
                self.assertEqual(len(inventory["artifacts"]), 6)
                for artifact in inventory["artifacts"]:
                    data = (results / artifact["name"]).read_bytes()
                    self.assertEqual(artifact["sha256"], hashlib.sha256(data).hexdigest())
                    self.assertEqual(artifact["sizeBytes"], len(data))
                original = receipt.read_bytes()
                duplicate = subprocess.run(
                    ["/bin/sh", "-c", script],
                    capture_output=True,
                    timeout=30,
                    env=environment,
                )
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertEqual(receipt.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
