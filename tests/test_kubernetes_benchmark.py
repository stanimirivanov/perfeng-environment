"""Qualification decisions, stable identity, immutable publication and offline command boundaries."""

import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import kubernetes_benchmark as benchmark
from scripts import kubernetes_benchmark_capture as capture
from scripts import kubernetes_benchmark_contract as contract
from scripts import kubernetes_benchmark_profile as cohort


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.profile = cohort.load()
        self.end = datetime(2026, 10, 13, 12, 0, 30, tzinfo=UTC)
        self.uid = "11111111-1111-4111-8111-111111111111"
        self.inventory: list[dict[str, Any]] = []
        self.measured: dict[str, dict[str, Any]] = {}
        for index, name in enumerate(self.profile.nodes, 2):
            uid = f"{index:08d}-2222-4222-8222-222222222222"
            self.inventory.append(
                {
                    "name": name,
                    "uid": uid,
                    "kubernetesVersion": "v1.35.0",
                    "containerRuntimeVersion": "containerd://2.2.0",
                    "architecture": "amd64",
                    "allocatable": {"cpuMilliCores": 4000, "memoryBytes": 8589934592},
                    "conditions": {
                        "ready": "TRUE",
                        "memoryPressure": "FALSE",
                        "diskPressure": "FALSE",
                        "pidPressure": "FALSE",
                    },
                }
            )
            self.measured[name] = {
                "source": "metrics-api",
                "windowStart": "2026-10-13T12:00:00.000000Z",
                "windowEnd": "2026-10-13T12:00:30.000000Z",
                "cpuMilliCores": 250,
                "memoryBytes": 1073741824,
            }
        self.observed = capture.Capture(
            self.uid,
            self.inventory,
            True,
            True,
            self.measured,
            {"kube-system", "perf-platform"},
            self.end - timedelta(seconds=5),
            self.end,
        )

    def test_accepted_integration_receipt_and_descriptor_hashes(self):
        result, descriptor = benchmark.evaluate(self.profile, self.observed)
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["profile"]["purpose"], "integration")
        self.assertEqual(result["scope"], {"kind": "preflight"})
        self.assertEqual(result["environment"]["sha256"], hashlib.sha256(descriptor).hexdigest())
        self.assertEqual(
            result["environment"]["fingerprint"], json.loads(descriptor)["fingerprint"]
        )
        self.assertEqual(
            result["captureWindow"]["start"],
            self.measured[self.inventory[0]["name"]]["windowStart"],
        )

    def test_usage_timestamps_do_not_change_environment_identity_but_cluster_recreation_does(self):
        first, _ = benchmark.evaluate(self.profile, self.observed)
        measured = copy.deepcopy(self.measured)
        measured[self.inventory[0]["name"]]["cpuMilliCores"] = 500
        second, _ = benchmark.evaluate(
            self.profile,
            replace(self.observed, measured=measured, end=self.end + timedelta(seconds=2)),
        )
        self.assertEqual(first["environment"], second["environment"])
        recreated, _ = benchmark.evaluate(
            self.profile, replace(self.observed, uid="22222222-1111-4111-8111-111111111111")
        )
        self.assertNotEqual(
            first["environment"]["fingerprint"], recreated["environment"]["fingerprint"]
        )

    def test_missing_probes_are_unknown_and_known_failure_dominates(self):
        missing = replace(
            self.observed, inventory=[], placement=None, capacity=None, measured={}, active=None
        )
        result, _ = benchmark.evaluate(self.profile, missing)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual([check["status"] for check in result["checks"]].count("UNKNOWN"), 5)
        result, _ = benchmark.evaluate(self.profile, replace(self.observed, measured={}))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["samples"][0]["usage"]["source"], "unavailable")
        self.inventory[0]["conditions"]["diskPressure"] = "TRUE"
        result, _ = benchmark.evaluate(self.profile, replace(self.observed, measured={}))
        self.assertEqual(result["status"], "REJECTED")

    def test_observed_runtime_or_capacity_drift_changes_the_comparison_fingerprint(self):
        first, _ = benchmark.evaluate(self.profile, self.observed)
        inventory = copy.deepcopy(self.inventory)
        inventory[0]["containerRuntimeVersion"] = "containerd://2.3.0"
        second, _ = benchmark.evaluate(self.profile, replace(self.observed, inventory=inventory))
        self.assertNotEqual(
            first["environment"]["fingerprint"], second["environment"]["fingerprint"]
        )

    def test_unreviewed_workloads_or_over_budget_usage_reject(self):
        for namespaces in ({"perf-generators"}, {"unreviewed-app"}):
            result, _ = benchmark.evaluate(self.profile, replace(self.observed, active=namespaces))
            self.assertEqual(result["status"], "REJECTED")
        self.measured[self.inventory[0]["name"]]["cpuMilliCores"] = 3001
        result, _ = benchmark.evaluate(self.profile, self.observed)
        self.assertEqual(result["status"], "REJECTED")

        result, _ = benchmark.evaluate(self.profile, replace(self.observed, placement=False))
        self.assertEqual(result["status"], "REJECTED")

    def test_expected_kind_storage_namespace_does_not_become_foreign_interference(self):
        result, _ = benchmark.evaluate(
            self.profile, replace(self.observed, active={"local-path-storage"})
        )
        self.assertEqual(result["status"], "ACCEPTED")
        result, _ = benchmark.evaluate(
            self.profile, replace(self.observed, active={"local-path-storage"}, measured={})
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("Metrics API", result["checks"][-1]["reason"])

    def test_policy_drift_during_capture_prevents_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with (
                patch.object(contract, "ROOT", root),
                patch.object(
                    cohort,
                    "load",
                    side_effect=[self.profile, replace(self.profile, sha256="f" * 64)],
                ),
                patch.object(capture, "collect", return_value=self.observed),
                patch.object(benchmark, "publish") as publish,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(benchmark.main(["check", "--execute"]), 2)
                publish.assert_not_called()

    def test_timestamp_format_validation_does_not_depend_on_optional_format_packages(self):
        result, _ = benchmark.evaluate(self.profile, self.observed)
        result["samples"][0]["observedAt"] = "2026-02-30T12:00:00Z"
        with self.assertRaises(ValueError):
            contract.validate(result)

    def test_profile_rejects_duplicate_fields_wrong_cohort_and_bad_budgets(self):
        original = json.loads(cohort.PROFILE.read_text())
        for key, value in (
            ("maximumCpuUsageRatio", True),
            ("maximumMemoryUsageRatio", 1.1),
            ("maximumMetricAgeSeconds", 0),
            ("architecture", "arm64"),
            ("allowedActiveNamespaces", ["perf-generators"]),
        ):
            document = copy.deepcopy(original)
            document["spec"][key] = value
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "profile.json"
                path.write_text(json.dumps(document))
                with self.subTest(key=key), self.assertRaises(ValueError):
                    cohort.load(path)
        with self.assertRaises(ValueError):
            contract.document(b'{"a":1,"a":2}')
        for content in (b'{"a":NaN}', b'{"a":1e999}'):
            with self.assertRaises(ValueError):
                contract.document(content)

    def test_schema_snapshot_and_exact_source_verification(self):
        contract.schema()
        content = (
            contract.ROOT / "contracts/kubernetes-benchmark-evidence.schema.json"
        ).read_bytes()
        with patch.object(contract, "revision_reader", return_value=lambda path: content):
            contract.validate_source(Path("unused"))
        with patch.object(contract, "revision_reader", return_value=lambda path: content + b" "):
            with self.assertRaises(ValueError):
                contract.validate_source(Path("unused"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "contracts").mkdir()
            (root / "contracts/kubernetes-benchmark-lock.json").write_bytes(
                (contract.ROOT / "contracts/kubernetes-benchmark-lock.json").read_bytes()
            )
            (root / "contracts/kubernetes-benchmark-evidence.schema.json").write_bytes(
                content + b" "
            )
            with self.assertRaisesRegex(ValueError, "checksum"):
                contract.schema(root)

    def test_preview_and_validation_do_not_start_processes_or_write_results(self):
        with (
            patch.object(capture, "collect") as collect,
            patch.object(benchmark, "publish") as publish,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(benchmark.main(["validate"]), 0)
            self.assertEqual(benchmark.main(["check", "--output", "results/unused.json"]), 0)
            self.assertEqual(benchmark.main(["validate", "--execute"]), 2)
            collect.assert_not_called()
            publish.assert_not_called()

    def test_main_retains_unknown_receipt_and_refuses_overwrite_before_cluster_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with (
                patch.object(contract, "ROOT", root),
                patch.object(
                    capture, "collect", return_value=replace(self.observed, measured={})
                ) as collect,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(benchmark.main(["check", "--execute"]), 1)
                receipt = next((root / "results").glob("m04-qualification-*.json"))
                if receipt.stem.endswith("-environment"):
                    receipt = next(
                        path
                        for path in (root / "results").glob("*.json")
                        if not path.stem.endswith("-environment")
                    )
                self.assertEqual(json.loads(receipt.read_bytes())["status"], "UNKNOWN")
                original = receipt.read_bytes()
                collect.reset_mock()
                self.assertEqual(
                    benchmark.main(["check", "--execute", "--output", str(receipt)]), 2
                )
                collect.assert_not_called()
                self.assertEqual(receipt.read_bytes(), original)

    def test_atomic_publication_preserves_race_winner_and_rejects_redirected_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "results/receipt.json"
            benchmark.destination(path, root)
            benchmark.publish(path, b"complete")
            with self.assertRaises(FileExistsError):
                benchmark.publish(path, b"replacement")
            self.assertEqual(path.read_bytes(), b"complete")
            self.assertEqual(list(path.parent.glob(".qualification-*")), [])
            with self.assertRaises(ValueError):
                benchmark.destination(root / "foreign.json", root)
            with patch.object(Path, "is_junction", return_value=True):
                with self.assertRaises(ValueError):
                    benchmark.destination(root / "results/new.json", root)


class CaptureTests(unittest.TestCase):
    def api_nodes(self, profile):
        return {
            "items": [
                {
                    "metadata": {
                        "name": name,
                        "uid": f"{index:08d}-2222-4222-8222-222222222222",
                        "labels": {"workload": role},
                    },
                    "spec": {"unschedulable": False},
                    "status": {
                        "nodeInfo": {
                            "kubeletVersion": "v1.35.0",
                            "containerRuntimeVersion": "containerd://2.2.0",
                            "architecture": "amd64",
                            "operatingSystem": "linux",
                        },
                        "allocatable": {"cpu": "4", "memory": "8Gi"},
                        "conditions": [
                            {"type": kind, "status": "True" if kind == "Ready" else "False"}
                            for kind in ("Ready", "MemoryPressure", "DiskPressure", "PIDPressure")
                        ],
                    },
                }
                for index, (name, role) in enumerate(profile.nodes.items(), 2)
            ]
        }

    def test_actual_inventory_checks_placement_capacity_cordon_and_missing_conditions(self):
        profile = cohort.load()
        value = self.api_nodes(profile)
        inventory, placement, capacity = capture.nodes(value, profile)
        self.assertTrue(placement)
        self.assertTrue(capacity)
        self.assertEqual(inventory[0]["allocatable"]["cpuMilliCores"], 4000)
        value["items"][0]["spec"]["unschedulable"] = True
        self.assertFalse(capture.nodes(value, profile)[1])
        value["items"][0]["status"]["nodeInfo"]["operatingSystem"] = "windows"
        self.assertFalse(capture.nodes(value, profile)[2])
        value["items"][0]["status"]["conditions"] = []
        self.assertEqual(capture.nodes(value, profile)[0][0]["conditions"]["ready"], "UNKNOWN")
        value["items"].append(copy.deepcopy(value["items"][0]))
        with self.assertRaises(ValueError):
            capture.nodes(value, profile)

    def test_live_adapter_uses_only_read_commands_and_refuses_changed_incarnation(self):
        profile = cohort.load()
        uid = "11111111-1111-4111-8111-111111111111"
        image = "kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f"
        calls = []
        unavailable_image = False

        def run(command):
            calls.append(command)
            if command[0] == "docker":
                self.assertEqual(command[1], "inspect")
                if unavailable_image:
                    if command[-1] == "perfeng-local-control-plane":
                        return json.dumps("unreviewed-image")
                    raise ValueError("image inspection unavailable")
                return json.dumps(image)
            self.assertIn("get", command)
            self.assertNotIn("secrets", command)
            if "nodes" in command:
                return json.dumps(self.api_nodes(profile))
            if "pods" in command:
                self.assertIn(
                    "custom-columns=NODE:.spec.nodeName,PHASE:.status.phase,NAMESPACE:.metadata.namespace",
                    command,
                )
                return "worker Running kube-system\n"
            if "namespace" in command:
                return uid
            raise ValueError("private-metrics-response")

        with (
            patch.object(capture, "identify", return_value=uid),
            patch.object(capture.cluster, "kubeconfig", return_value=Path("dedicated")),
            patch.object(capture.cluster, "run", side_effect=run),
            redirect_stdout(io.StringIO()),
        ):
            result = capture.collect(contract.ROOT, profile)
            self.assertEqual(result.uid, uid)
            self.assertEqual(result.measured, {})
            self.assertEqual(len(result.inventory), 3)
            old_uid = uid
            uid = "22222222-1111-4111-8111-111111111111"
            with self.assertRaisesRegex(ValueError, "incarnation changed"):
                capture.collect(contract.ROOT, profile)
            uid = old_uid
            unavailable_image = True
            result = capture.collect(contract.ROOT, profile)
            self.assertIs(result.capacity, False)
            evidence, _ = benchmark.evaluate(profile, result)
            self.assertEqual(evidence["status"], "REJECTED")

    def test_quantity_units_and_duration_rounding(self):
        self.assertEqual(capture.quantity("4", cpu=True), 4000)
        self.assertEqual(capture.quantity("1.5m", cpu=True), 1)
        self.assertEqual(capture.quantity("1.5m", cpu=True, usage=True), 2)
        self.assertEqual(capture.quantity("1000000n", cpu=True, usage=True), 1)
        self.assertEqual(capture.quantity("1Gi", cpu=False), 1073741824)
        self.assertEqual(capture.duration("1m0.5s"), 60.5)
        for value in ("-1", "NaN", "1e99999", True):
            with self.assertRaises(ValueError):
                capture.quantity(value, cpu=True)
        for value in ("0s", "1ns", "999h", "30seconds"):
            with self.assertRaises(ValueError):
                capture.duration(value)

    def test_metric_freshness_window_and_missing_data(self):
        profile = cohort.load()
        now = datetime(2026, 10, 13, 12, 0, 30, tzinfo=UTC)
        item = {
            "metadata": {"name": "perfeng-local-worker"},
            "timestamp": "2026-10-13T12:00:30Z",
            "window": "30s",
            "usage": {"cpu": "250m", "memory": "1Gi"},
        }
        self.assertEqual(
            capture.metrics({"items": [item]}, now, profile)["perfeng-local-worker"][
                "cpuMilliCores"
            ],
            250,
        )
        for timestamp in ("2026-10-13T11:50:00Z", "2026-10-13T12:00:31Z", "2026-10-13T12:00:30"):
            with self.assertRaises((ValueError, TypeError)):
                capture.metrics({"items": [{**item, "timestamp": timestamp}]}, now, profile)
        with self.assertRaises(ValueError):
            capture.metrics({"items": [item, item]}, now, profile)

    def test_foreign_context_stops_before_api_reads_and_failure_output_is_safe(self):
        with (
            patch.object(capture.cluster, "verify_local_docker"),
            patch.object(
                capture.cluster, "verify_context", side_effect=ValueError("secret-vendor-data")
            ),
            patch.object(capture.cluster, "run", return_value="kind v0.31.0") as run,
        ):
            with self.assertRaises(ValueError):
                capture.identify()
            self.assertEqual(run.call_count, 1)
        with (
            patch.object(capture, "collect", side_effect=ValueError("secret-vendor-data")),
            redirect_stdout(io.StringIO()) as output,
        ):
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.object(contract, "ROOT", Path(directory).resolve()),
            ):
                self.assertEqual(benchmark.main(["check", "--execute"]), 2)
            self.assertNotIn("secret-vendor-data", output.getvalue())

    def test_pod_inventory_contains_only_non_secret_columns(self):
        namespaces, busy = capture.pod_namespaces(
            "worker Running perf-generators\nworker Succeeded perf-platform\n"
        )
        self.assertTrue(busy)
        self.assertEqual(namespaces, {"perf-generators"})
        with self.assertRaises(ValueError):
            capture.pod_namespaces("unexpected output with extra columns")


if __name__ == "__main__":
    unittest.main()
