"""Readiness contract failures are offline and never create workload state."""

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from jsonschema import Draft202012Validator

from tests.release_verifier_support import policy, runtime

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"
verifier = runtime
REVISION = "a" * 40


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_lock = (ROOT / "deployments/local-control-plane.json").read_bytes()
        self.observations = json.loads(
            (ROOT / "tests/fixtures/release-readiness-observations.json").read_text()
        )
        self.preflight = {
            "schemaVersion": 1,
            "kind": "PerfengReleasePreflight",
            "status": "identity-verified",
            "environment": "perfeng-local",
            "selectionRevision": REVISION,
            "requestRevision": "b" * 40,
            "requestDigest": "sha256:" + "c" * 64,
            "clusterUID": "cluster-uid",
            "lockDigests": {
                "deployments/local-control-plane.json": "sha256:"
                + hashlib.sha256(self.raw_lock).hexdigest()
            },
        }

    def test_selected_converged_release_emits_strict_versioned_evidence(self) -> None:
        result = policy.verify_readiness(self.preflight, self.raw_lock, self.observations, REVISION)
        schema = json.loads(
            (ROOT / "deployments/release-readiness-result-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(result)
        self.assertEqual(result["requestDigest"], self.preflight["requestDigest"])
        self.assertEqual(result["clusterUID"], "cluster-uid")
        self.assertFalse(Draft202012Validator(schema).is_valid({**result, "approved": True}))

    def test_stale_or_partial_workload_rollouts_are_rejected(self) -> None:
        for key, field, value in (
            ("deployment", "observedGeneration", 6),
            ("deployment", "replicas", 2),
            ("deployment", "desired", 0),
            ("deployment", "available", 0),
            ("postgres", "ready", 0),
            ("seaweedfs", "updateRevision", "different"),
        ):
            changed = deepcopy(self.observations)
            changed[key][field] = value
            with self.subTest(key=key, field=field), self.assertRaises(ValueError):
                policy.verify_readiness(self.preflight, self.raw_lock, changed, REVISION)

    def test_wrong_runtime_source_service_and_application_status_are_rejected(self) -> None:
        for key, field, value in (
            ("deployment", "images", ["wrong:latest"]),
            ("deployment", "runtimeConfigMaps", ["stale"]),
            ("deployment", "registryConfigMaps", ["stale"]),
            ("deployment", "runtimeSecrets", ["other-identity"]),
            ("application", "revisions", ["main", "main"]),
            ("application", "comparedSources", []),
            ("application", "sources", []),
            ("application", "health", "Degraded"),
            ("application", "operation", "Running"),
            ("application", "conditions", ["ComparisonError"]),
            ("service", "type", "ExternalName"),
            ("service", "selector", {}),
        ):
            changed = deepcopy(self.observations)
            changed[key][field] = value
            with self.subTest(key=key, field=field), self.assertRaises(ValueError):
                policy.verify_readiness(self.preflight, self.raw_lock, changed, REVISION)

    def test_preflight_and_lock_mismatches_fail_closed(self) -> None:
        for field, value in (
            ("environment", "other"),
            ("selectionRevision", "d" * 40),
            ("status", "unknown"),
        ):
            changed = {**self.preflight, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                policy.verify_readiness(changed, self.raw_lock, self.observations, REVISION)
        with self.assertRaisesRegex(ValueError, "lock differs"):
            policy.verify_readiness(
                self.preflight, self.raw_lock + b"\n", self.observations, REVISION
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            json.loads('{"image":1,"image":2}', object_pairs_hook=policy.unique)
        lock = json.loads(self.raw_lock)
        lock["schemaVersion"] = 2
        raw = json.dumps(lock).encode()
        changed = {
            **self.preflight,
            "lockDigests": {
                "deployments/local-control-plane.json": "sha256:" + hashlib.sha256(raw).hexdigest()
            },
        }
        with self.assertRaisesRegex(ValueError, "lock version"):
            policy.verify_readiness(changed, raw, self.observations, REVISION)

    def test_cli_reports_controlled_failures_without_vendor_details(self) -> None:
        for error, expected in (
            (policy.ReadinessRefused("Deployment differs"), "Deployment differs"),
            (ValueError("secret-vendor-body"), "invalid selection"),
        ):
            output = io.StringIO()
            with (
                patch.object(verifier, "run_readiness", side_effect=error),
                redirect_stderr(output),
            ):
                self.assertEqual(verifier.cli("readiness"), 2)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn("secret-vendor-body", output.getvalue())

    def test_output_is_written_only_after_all_checks_and_http_probes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "selection"
            (root / ".git").mkdir(parents=True)
            (root / ".git/HEAD").write_text(REVISION + "\n")
            (root / "deployments").mkdir()
            (root / "deployments/local-control-plane.json").write_bytes(self.raw_lock)
            output = Path(temporary) / "result.json"
            environment = {
                "SELECTION_REVISION": REVISION,
                "PREFLIGHT_RESULT": json.dumps(self.preflight),
                **{name.upper(): json.dumps(value) for name, value in self.observations.items()},
            }
            paths = {"/inputs/selection": root, "/tmp/readiness-result.json": output}
            with (
                patch.dict(os.environ, environment),
                patch.object(verifier, "Path", side_effect=lambda value: paths[value]),
            ):
                with patch.object(
                    verifier, "probe_endpoints", side_effect=ValueError("probe failed")
                ):
                    with self.assertRaisesRegex(ValueError, "probe failed"):
                        verifier.run_readiness()
                    self.assertFalse(output.exists())
                with patch.object(verifier, "probe_endpoints") as probe:
                    verifier.run_readiness()
                    probe.assert_called_once()
                    self.assertEqual(json.loads(output.read_text())["status"], "readiness-verified")
                output.unlink()
                (root / ".git/HEAD").write_text("main\n")
                with patch.object(verifier, "probe_endpoints") as probe:
                    with self.assertRaisesRegex(ValueError, "checkout"):
                        verifier.run_readiness()
                    probe.assert_not_called()
                    self.assertFalse(output.exists())

    def test_operational_probes_are_bounded_and_reject_bad_responses(self) -> None:
        for bodies, status, expected in (
            ([b"ok\n", b"ok\n", b"perfeng_control_plane_ready 1\n"], 200, True),
            ([b"login page"], 200, False),
            ([b"ok\n"], 302, False),
            ([b"x" * 1048577], 200, False),
            ([b"ok\n", b"ok\n", b"# perfeng_control_plane_ready 1\n"], 200, False),
        ):
            opener = MagicMock()
            responses = []
            for body in bodies:
                response = MagicMock(status=status)
                response.read.return_value = body
                response.__enter__.return_value = response
                responses.append(response)
            opener.open.side_effect = responses
            with (
                self.subTest(status=status, expected=expected),
                patch.object(verifier, "build_opener", return_value=opener),
            ):
                if expected:
                    verifier.probe_endpoints()
                    self.assertEqual(opener.open.call_count, 3)
                else:
                    with self.assertRaisesRegex(ValueError, "operational endpoint failed"):
                        verifier.probe_endpoints()
                self.assertTrue(
                    all(call.kwargs["timeout"] == 10 for call in opener.open.call_args_list)
                )
                for response in responses[: opener.open.call_count]:
                    response.read.assert_called_once_with(1048577)
        with self.assertRaises(ValueError):
            verifier.NoRedirect().redirect_request(None, None, 302, "", {}, "http://foreign")

    def test_workflow_uses_native_waits_and_named_read_only_authority(self) -> None:
        workflow = yaml.safe_load((DIRECTORY / "release-readiness-v2.yaml").read_text())
        observer = yaml.safe_load((DIRECTORY / "observe-release-readiness.yaml").read_text())
        bootstrap = yaml.safe_load(
            (ROOT / "gitops/release-workflows-bootstrap/project.yaml").read_text()
        )
        self.assertNotIn(
            "argocd", [item["namespace"] for item in bootstrap["spec"]["destinations"]]
        )
        verifier_template = yaml.safe_load(
            (DIRECTORY / "verify-release-readiness-v2.yaml").read_text()
        )["spec"]["templates"][0]
        self.assertEqual(
            json.loads(verifier_template["podSpecPatch"])["securityContext"]["fsGroup"], 10001
        )
        self.assertTrue(verifier_template["container"]["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(workflow["spec"]["serviceAccountName"], "release-readiness")
        self.assertEqual(
            workflow["spec"]["synchronization"]["mutexes"],
            [{"name": "perfeng-local-external-state"}],
        )
        self.assertEqual(workflow["spec"]["activeDeadlineSeconds"], 1200)
        self.assertEqual(
            workflow["spec"]["templates"][0]["steps"][0][0]["templateRef"]["name"],
            "perfeng-release-preflight-v2",
        )
        for template in observer["spec"]["templates"]:
            self.assertEqual(template["resource"]["action"], "get")
            self.assertEqual(template["activeDeadlineSeconds"], 300)
            self.assertIn("jqFilter", template["outputs"]["parameters"][0]["valueFrom"])
            if template["name"] != "service":
                self.assertIn("successCondition", template["resource"])
        paths = [
            *(DIRECTORY / "rbac/readiness").glob("*.yaml"),
            ROOT / "gitops/release-workflows-bootstrap/readiness-application-access.yaml",
        ]
        for path in paths:
            for obj in yaml.safe_load_all(path.read_text()):
                for rule in obj.get("rules", []):
                    if rule["resources"] == ["workflowtaskresults"]:
                        self.assertEqual(rule["verbs"], ["create", "patch"])
                    else:
                        self.assertEqual(rule["verbs"], ["get"])
                        self.assertTrue(rule["resourceNames"])
        root = yaml.safe_load((ROOT / "gitops/environments/local/kustomization.yaml").read_text())
        self.assertNotIn("release-readiness-v2.yaml", str(root))


if __name__ == "__main__":
    unittest.main()
