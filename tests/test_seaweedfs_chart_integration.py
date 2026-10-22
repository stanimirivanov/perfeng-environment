"""Offline provider routing and TCP peer classification; no live network or cluster."""

import contextlib
import io
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"


class SeaweedFSChartIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "Bash required")
    def test_main_routes_to_chart_and_pr_retains_direct_provider_with_stubbed_tools(self):
        assert BASH is not None
        for mode, pod in (
            ("preflight-parent", "seaweedfs-0"),
            ("boundaries", "bucket-ci-seaweedfs"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "owned").touch()
                code = r"""
set -euo pipefail
fixture=$1; source_dir=$2; mode=$3; KUBECONFIG="$fixture/kubeconfig"
export GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
k() { printf '%s\n' "$*" >> "$fixture/calls"; }
a() { :; }
uv() { :; }
source() {
  builtin source "$1"
  if [[ $1 == */seaweedfs-chart.sh ]]; then
    seaweedfs_chart_setup() { printf '%s\n' chart >> "$fixture/calls"; }
  fi
}
source "$source_dir/external-state-fixtures.sh"
bucket_setup
printf '%s\n' "$bucket_server_pod"
"""
                result = subprocess.run(
                    [BASH, "-s", "--", directory.as_posix(), FIXTURE.as_posix(), mode],
                    input=code,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), pod)
                calls = (directory / "calls").read_text()
                self.assertEqual("chart\n" in calls, mode == "preflight-parent")
                self.assertEqual("seaweedfs-pod.yaml" in calls, mode == "boundaries")
                self.assertEqual("seaweedfs-service.yaml" in calls, mode == "boundaries")
                self.assertIn("bucket-observer.yaml", calls)

    def test_tcp_peer_requires_proxy_success_and_exact_private_port_refusal(self):
        job = yaml.safe_load((FIXTURE / "seaweedfs-peer.yaml").read_text())
        code = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        for case, error in (
            ("closed", None),
            ("proxy", TimeoutError),
            ("timeout", TimeoutError),
            ("open", RuntimeError),
            ("bad-ip", ValueError),
        ):
            with self.subTest(case=case):
                calls = []

                def connect(address, timeout, *, calls=calls, case=case):
                    calls.append(address[1])
                    if address[1] == 8333:
                        if case == "proxy":
                            raise TimeoutError("unreachable")
                        return MagicMock()
                    if case == "timeout":
                        raise TimeoutError("not a refusal")
                    if case == "open":
                        return MagicMock()
                    raise ConnectionRefusedError("closed")

                with (
                    patch.dict(
                        os.environ, {"TARGET_IP": "localhost" if case == "bad-ip" else "10.0.0.4"}
                    ),
                    patch.object(socket, "create_connection", side_effect=connect),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    if error:
                        with self.assertRaises(error):
                            exec(compile(code, "peer", "exec"), {})
                    else:
                        exec(compile(code, "peer", "exec"), {})
                        self.assertEqual(
                            calls, [8333, 9000, 19000, 9333, 19333, 8888, 18888, 8080, 18080]
                        )
                        self.assertEqual(
                            output.getvalue().strip(),
                            "Selected proxy reachable; private listeners refused",
                        )
                if error:
                    self.assertEqual(output.getvalue(), "")

    def test_peer_has_no_credentials_api_token_or_unbounded_retry(self):
        job = yaml.safe_load((FIXTURE / "seaweedfs-peer.yaml").read_text())
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 60)
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertNotIn("volumes", pod)
        self.assertNotIn("imagePullSecrets", pod)
        worker = pod["containers"][0]
        self.assertNotIn("envFrom", worker)
        self.assertNotIn("env", worker)
        self.assertTrue(worker["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(worker["securityContext"]["allowPrivilegeEscalation"])
        w = yaml.safe_load((FIXTURE / "observer-workflow.yaml").read_text())
        template = next(t for t in w["spec"]["templates"] if t["name"] == "seaweedfs")
        self.assertEqual(
            template["steps"][0][0]["templateRef"],
            {"name": "perfeng-observe-release-readiness-v1", "template": "statefulset"},
        )


if __name__ == "__main__":
    unittest.main()
