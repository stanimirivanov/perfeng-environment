"""Offline native observation and provider routing checks; all live tools mocked."""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from test_release_workflows_integration import BASH

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"


class PostgresChartIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(BASH, "Bash required")
    def test_main_uses_chart_and_pr_keeps_existing_provider_with_all_tools_stubbed(self):
        assert BASH is not None
        for mode, pod in (
            ("preflight-parent", "postgres-0"),
            ("boundaries", "database-ci-postgres"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "owned").touch()
                script = r"""
set -euo pipefail
fixture=$1; source_dir=$2; mode=$3; KUBECONFIG="$fixture/kubeconfig"
export GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
k() { printf '%s\n' "$*" >> "$fixture/calls"; }
a() { :; }
uv() { :; }
source() {
  builtin source "$1"
  if [[ $1 == */postgres-chart.sh ]]; then
    postgres_chart_setup() { printf '%s\n' chart >> "$fixture/calls"; }
  fi
}
source "$source_dir/external-state-fixtures.sh"
database_setup
printf '%s\n' "$database_pod"
"""
                result = subprocess.run(
                    [BASH, "-s", "--", directory.as_posix(), FIXTURE.as_posix(), mode],
                    input=script,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), pod)
                calls = (directory / "calls").read_text()
                self.assertEqual("chart\n" in calls, mode == "preflight-parent")
                self.assertEqual("postgres-pod.yaml" in calls, mode == "boundaries")
                self.assertEqual("postgres-service.yaml" in calls, mode == "boundaries")

    @unittest.skipUnless(BASH and shutil.which("jq"), "Bash and jq required")
    def test_native_readiness_rejects_stale_generation_revision_or_replica_state(self):
        assert BASH is not None
        function = re.search(
            r"^postgres_chart_native_ready\(\) \{\n.*?^\}",
            (FIXTURE / "postgres-chart.sh").read_text(),
            re.M | re.S,
        )
        assert function
        original = dict(
            name="postgres",
            namespace="perf-platform",
            uid="ci-sts",
            generation=2,
            observedGeneration=2,
            desired=1,
            replicas=1,
            ready=1,
            updated=1,
            currentRevision="rev-2",
            updateRevision="rev-2",
        )
        for key, value in (
            (None, None),
            ("uid", ""),
            ("observedGeneration", 1),
            ("ready", 0),
            ("updated", 0),
            ("currentRevision", "rev-1"),
            ("currentRevision", ""),
        ):
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as temporary:
                observation = dict(original)
                if key:
                    assert value is not None
                    observation[key] = value
                path = Path(temporary) / "observation.json"
                path.write_text(json.dumps(observation))
                result = subprocess.run(
                    [BASH, "-s", "--", path.as_posix()],
                    input=function[0] + '\npostgres_chart_native_ready "$1"\n',
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode == 0, key is None, result.stderr)

    def test_ci_wrapper_reuses_original_statefulset_child_and_chart_image(self):
        w = yaml.safe_load((FIXTURE / "observer-workflow.yaml").read_text())
        template = next(t for t in w["spec"]["templates"] if t["name"] == "postgres")
        step = template["steps"][0][0]
        self.assertEqual(
            step["templateRef"],
            {"name": "perfeng-observe-release-readiness-v1", "template": "statefulset"},
        )
        self.assertEqual(step["arguments"]["parameters"], [{"name": "name", "value": "postgres"}])
        values = yaml.safe_load((ROOT / "charts/postgres/values.yaml").read_text())
        pod = yaml.safe_load((FIXTURE / "postgres-pod.yaml").read_text())
        self.assertEqual(values["image"], pod["spec"]["containers"][0]["image"])
        self.assertEqual(values["existingSecret"], "perfeng-postgres-auth")
        client = yaml.safe_load((FIXTURE / "postgres-chart-client.yaml").read_text())
        self.assertEqual(client["spec"]["backoffLimit"], 3)
        self.assertEqual(client["spec"]["activeDeadlineSeconds"], 120)
        spec = client["spec"]["template"]["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(spec["containers"][0]["image"], values["image"])
        self.assertEqual(spec["containers"][0]["command"], ["psql"])
        self.assertIn(
            {"name": "PGHOST", "value": "postgres.perf-platform.svc.cluster.local"},
            spec["containers"][0]["env"],
        )


if __name__ == "__main__":
    unittest.main()
