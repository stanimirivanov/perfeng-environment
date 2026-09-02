import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import k6_run as runner

RUN = "perf-20260902-120000-abcdef12"


def sample_deployment():
    root = runner.cluster.ROOT
    image = runner.yaml.safe_load((root / "charts/sample-sut/values.yaml").read_text())["image"]
    digest = runner.hashlib.sha256(
        (root / "charts/sample-sut/files/server.py").read_bytes()
    ).hexdigest()
    return json.dumps(
        {
            "spec": {
                "template": {
                    "metadata": {"annotations": {"checksum/api": digest}},
                    "spec": {"containers": [{"name": "sample-api", "image": image}]},
                }
            }
        }
    )


class K6RunTests(unittest.TestCase):
    def test_current_sample_accepted_and_obsolete_or_changed_sample_rejected(self):
        runner.validate_sample(sample_deployment())
        for value in [
            "{}",
            sample_deployment().replace("sample-api", "echo"),
            sample_deployment().replace("sha256:", "wrong:"),
        ]:
            with self.assertRaisesRegex(ValueError, "stale"):
                runner.validate_sample(value)

    def test_preview_never_executes(self):
        with (
            patch("sys.argv", ["k6_run.py"]),
            patch.object(runner.cluster, "run") as run,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(runner.main(), 0)
            run.assert_not_called()
            self.assertIn(runner.IMAGE, output.getvalue())
            self.assertNotIn("stringData", output.getvalue())

    def test_invalid_id_rejected_before_access(self):
        for value in ["", "../data", "$(id)", RUN + ";id", RUN.upper()]:
            with patch.object(runner.cluster, "run") as run:
                with self.assertRaises(ValueError):
                    runner.execute(value)
                run.assert_not_called()

    def test_isolated_runner_and_sequential_readonly_uploader(self):
        job = runner.job(RUN)
        self.assertEqual(job["metadata"]["namespace"], "perf-platform")
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 900)
        self.assertNotIn("ttlSecondsAfterFinished", job["spec"])
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertFalse(pod["enableServiceLinks"])
        self.assertEqual(pod["restartPolicy"], "Never")
        self.assertEqual(pod["nodeSelector"]["workload"], "performance-generator")
        self.assertEqual(pod["nodeSelector"]["kubernetes.io/arch"], "amd64")
        k6 = pod["initContainers"][0]
        upload = pod["containers"][0]
        self.assertEqual(k6["image"], runner.IMAGE)
        self.assertNotIn("secretKeyRef", json.dumps(k6))
        self.assertNotIn("AWS_", json.dumps(k6))
        self.assertIn("secretKeyRef", json.dumps(upload))
        self.assertNotIn('"perfeng-s3-auth"', json.dumps(job))
        self.assertIn('"perfeng-s3-uploader-auth"', json.dumps(upload))
        self.assertTrue(upload["volumeMounts"][1]["readOnly"])
        self.assertEqual(pod["securityContext"]["runAsUser"], 12345)
        for container in [k6, upload]:
            security = container["securityContext"]
            self.assertTrue(security["readOnlyRootFilesystem"])
            self.assertFalse(security["allowPrivilegeEscalation"])
            self.assertEqual(security["capabilities"]["drop"], ["ALL"])

    def test_capture_preserves_failures_without_claiming_measurement_window(self):
        script = runner.runner_script()
        self.assertIn("|| code=$?", script)
        self.assertIn(runner.CONFIG_HASH, script)
        self.assertIn("processStartedAt", script)
        self.assertNotIn("measurementWindow", script)
        self.assertIn("status.json", script)
        self.assertIn("--no-usage-report", script)

    def test_upload_is_conditional_with_readback_and_receipt_last(self):
        script = runner.upload_script(RUN)
        self.assertIn('--if-none-match "*"', script)
        self.assertIn("get-object", script)
        self.assertIn('test "$1" = "$expected"', script)
        self.assertIn("sizeBytes", script)
        self.assertLess(script.index("done"), script.index("upload /tmp/receipt.json"))
        self.assertNotIn("delete-object", script)
        self.assertNotIn("create-bucket", script)

    def test_threshold_failure_is_distinct_from_upload_success(self):
        for code in [0, 99, 107]:
            text = json.dumps(
                {
                    "runId": RUN,
                    "artifactsComplete": True,
                    "status": {"exitCode": code},
                }
            )
            self.assertEqual(runner.outcome(text, RUN), code)

    def test_missing_artifacts_status_and_wrong_identity_fail_closed(self):
        cases = [
            {},
            {"runId": RUN, "artifactsComplete": True},
            {"runId": RUN, "artifactsComplete": True, "status": {"exitCode": True}},
            {"runId": RUN, "artifactsComplete": True, "status": {"exitCode": -1}},
            {"runId": RUN, "artifactsComplete": False, "status": {"exitCode": 0}},
            {"runId": "other", "artifactsComplete": True, "status": {"exitCode": 0}},
        ]
        for value in cases:
            with self.assertRaises(ValueError):
                runner.outcome(json.dumps(value), RUN)

    def test_foreign_cluster_blocks_preflights_and_creation(self):
        with (
            patch.object(runner.cluster, "verify_local_docker"),
            patch.object(runner.cluster, "verify_context", side_effect=ValueError("foreign")),
            patch.object(runner.cluster, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "foreign"):
                runner.execute(RUN)
            run.assert_not_called()

    def test_execute_returns_threshold_failure_after_verified_upload(self):
        for code in [0, 99]:
            with (
                patch.object(runner.cluster, "verify_local_docker"),
                patch.object(runner.cluster, "verify_context"),
                patch.object(runner.storage_access, "ensure_credentials") as secret,
                patch.object(
                    runner.cluster,
                    "run",
                    side_effect=[
                        "",
                        "",
                        sample_deployment(),
                        '{"status":{"phase":"Bound"}}',
                        "",
                        '{"status":{"conditions":[{"type":"Complete","status":"True"}]}}',
                        json.dumps(
                            {
                                "runId": RUN,
                                "artifactsComplete": True,
                                "status": {"exitCode": code},
                            }
                        ),
                    ],
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(runner.execute(RUN), code)
                self.assertIn("Verified capture", output.getvalue())
                secret.assert_called_once_with(runner.cluster.ROOT, create=False)

    def test_unbound_storage_prevents_job_creation(self):
        with (
            patch.object(runner.cluster, "verify_local_docker"),
            patch.object(runner.cluster, "verify_context"),
            patch.object(runner.storage_access, "ensure_credentials"),
            patch.object(
                runner.cluster,
                "run",
                side_effect=[
                    "",
                    "",
                    sample_deployment(),
                    '{"status":{"phase":"Pending"}}',
                ],
            ) as run,
        ):
            with self.assertRaisesRegex(ValueError, "not Bound"):
                runner.execute(RUN)
            self.assertFalse(any("create" in c.args[0] for c in run.call_args_list))

    def test_observer_timeout_retains_job_and_never_deletes(self):
        with (
            patch.object(runner.cluster, "verify_local_docker"),
            patch.object(runner.cluster, "verify_context"),
            patch.object(runner.storage_access, "ensure_credentials"),
            patch.object(
                runner.cluster,
                "run",
                side_effect=[
                    "",
                    "",
                    sample_deployment(),
                    '{"status":{"phase":"Bound"}}',
                    "",
                ],
            ) as run,
            patch.object(runner.time, "monotonic", side_effect=[0, 961]),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaisesRegex(ValueError, "Timed out"):
                runner.execute(RUN)
            self.assertFalse(any("delete" in c.args[0] for c in run.call_args_list))

    def test_execution_does_not_retry_failed_jobs_or_report_success(self):
        with (
            patch.object(runner.cluster, "verify_local_docker"),
            patch.object(runner.cluster, "verify_context"),
            patch.object(runner.storage_access, "ensure_credentials"),
            patch.object(
                runner.cluster,
                "run",
                side_effect=[
                    "",
                    "",
                    sample_deployment(),
                    '{"status":{"phase":"Bound"}}',
                    "",
                    '{"status":{"conditions":[{"type":"Failed","status":"True"}]}}',
                ],
            ) as run,
            redirect_stdout(io.StringIO()) as output,
        ):
            with self.assertRaisesRegex(ValueError, "infrastructure failure"):
                runner.execute(RUN)
            self.assertEqual(sum("create" in c.args[0] for c in run.call_args_list), 1)
            self.assertNotIn("Verified capture", output.getvalue())


if __name__ == "__main__":
    unittest.main()
