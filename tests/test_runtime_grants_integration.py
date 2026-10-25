"""Offline policy parity, worker authority and refusal classification checks."""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from test_release_workflows_integration import BASH

from scripts import control_plane_migration

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"


class RuntimeGrantsIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(BASH and shutil.which("jq"), "Bash and jq required")
    def test_connection_failure_is_explained_without_accepting_a_policy_refusal(self):
        assert BASH is not None
        source = (FIXTURE / "runtime-grants.sh").read_text()
        function = re.search(r"^runtime_probe_refuse\(\) \{\n.*?^\}", source, re.M | re.S)
        assert function is not None
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "probe"
            for code in (2, 3, 137, None):
                with self.subTest(code=code):
                    prefix.with_suffix(".worker.json").write_text(
                        json.dumps(
                            [
                                {
                                    "containers": [{"name": "probe", "exitCode": code}],
                                    "private": "credential-sentinel",
                                }
                            ]
                        )
                    )
                    result = subprocess.run(
                        [BASH, "-s", "--", prefix.as_posix()],
                        input=function[0] + '\nruntime_probe_refuse "$1"\n',
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertNotIn("credential-sentinel", result.stderr)
                    self.assertIn(
                        "database-connection" if code == 2 else "RUNTIME-POLICY", result.stderr
                    )
                    self.assertEqual(prefix.with_suffix(".failure.txt").read_text(), result.stderr)

    def test_grants_are_the_exact_supported_transaction(self):
        with patch.object(control_plane_migration.control_plane_database, "psql") as psql:
            control_plane_migration.grant_runtime(Path("unused"))
        self.assertEqual((FIXTURE / "runtime-grants.sql").read_bytes(), psql.call_args.args[1])

    def test_worker_has_only_runtime_credentials_and_no_api_token(self):
        workflow = yaml.safe_load((FIXTURE / "runtime-grants-workflow.yaml").read_text())
        self.assertEqual(workflow["spec"]["serviceAccountName"], "release-external-state")
        template = workflow["spec"]["templates"][0]
        self.assertEqual(template["resource"]["action"], "create")
        job = yaml.safe_load(template["resource"]["manifest"])
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertLessEqual(job["spec"]["activeDeadlineSeconds"], 90)
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(len(pod["containers"]), 1)
        self.assertNotIn("initContainers", pod)
        worker = pod["containers"][0]
        server = yaml.safe_load((FIXTURE / "postgres-pod.yaml").read_text())
        self.assertEqual(worker["image"], server["spec"]["containers"][0]["image"])
        self.assertEqual(worker["command"], ["psql"])
        self.assertIn("ON_ERROR_STOP=1", worker["args"])
        self.assertIn("-qAt", worker["args"])
        self.assertEqual(
            [e["valueFrom"]["secretKeyRef"] for e in worker["env"] if "valueFrom" in e],
            [{"name": "perfeng-control-plane-database", "key": "runtime-url"}],
        )
        self.assertEqual(
            pod["volumes"], [{"name": "sql", "configMap": {"name": "runtime-grants-ci"}}]
        )
        self.assertTrue(worker["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(worker["securityContext"]["allowPrivilegeEscalation"])

    @unittest.skipUnless(BASH and shutil.which("jq"), "Bash and jq required")
    def test_expected_refusal_rejects_unrelated_failures(self):
        assert BASH is not None
        source = (FIXTURE / "runtime-grants.sh").read_text()
        function = re.search(r"^runtime_probe_outcome\(\) \{\n.*?^\}", source, re.M | re.S)
        assert function is not None
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for scenario in ("policy", "auth", "network", "pull", "timeout", "worker", "succeeded"):
                with self.subTest(scenario=scenario):
                    values = {
                        "workflow": {
                            "status": {
                                "phase": "Succeeded" if scenario == "succeeded" else "Failed"
                            }
                        },
                        "job": {"status": {"failed": 1}},
                        "worker": [
                            {
                                "containers": [
                                    {
                                        "name": "other" if scenario == "worker" else "probe",
                                        "exitCode": None if scenario in {"pull", "timeout"} else 3,
                                        "waitingReason": "ImagePullBackOff"
                                        if scenario == "pull"
                                        else None,
                                    }
                                ]
                            }
                        ],
                    }
                    for suffix, value in values.items():
                        (directory / f"probe.{suffix}.json").write_text(json.dumps(value))
                    log = directory / "worker.log"
                    log.write_text(
                        {
                            "auth": "psql: error: password authentication failed\n",
                            "network": "psql: error: connection refused\n",
                        }.get(
                            scenario,
                            "psql:/sql/probe.sql:95: ERROR:  runtime privilege matrix mismatch\n",
                        )
                    )
                    result = subprocess.run(
                        [BASH, "-s", "--", (directory / "probe").as_posix(), log.as_posix()],
                        input=function[0] + '\nruntime_probe_outcome failure "$1" "$2"\n',
                        text=True,
                        capture_output=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode == 0, scenario == "policy", result.stderr)

    def test_phase_is_main_only_and_follows_actual_migration(self):
        parent = (FIXTURE / "preflight-parent.sh").read_text()
        self.assertLess(parent.index('/migration-image.sh"'), parent.index('/runtime-grants.sh"'))
        script = (FIXTURE / "runtime-grants.sh").read_text()
        self.assertIn("${mode:-} == preflight-parent", script)
        self.assertEqual(
            re.findall(r"^runtime_probe (\S+) (success|failure)$", script, re.M),
            [
                ("ungranted", "failure"),
                ("first", "success"),
                ("repeat", "success"),
                ("excess-delete", "failure"),
                ("corrected", "success"),
            ],
        )
        self.assertIn(
            '"$source_dir/runtime-grants-workflow.yaml"', (FIXTURE / "run.sh").read_text()
        )


if __name__ == "__main__":
    unittest.main()
