"""Read-only maintenance evidence rejects incomplete, changing or active state."""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"
SPEC = importlib.util.spec_from_file_location(
    "quiescence", DIRECTORY / "scripts/verify-quiescence.py"
)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
REVISION = "a" * 40


class MaintenanceCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        import hashlib

        self.raw = (ROOT / "deployments/local-control-plane.json").read_bytes()
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
                + hashlib.sha256(self.raw).hexdigest()
            },
        }
        self.before = [
            yaml.safe_load((ROOT / name).read_text())
            for name in (
                "gitops/bootstrap/local-root-application.yaml",
                "gitops/environments/local/control-plane-application.yaml",
            )
        ]
        for index, obj in enumerate(self.before):
            obj["metadata"].update(uid=f"application-{index}", resourceVersion=str(index + 10))
            obj["spec"]["syncPolicy"]["automated"] = {"enabled": False}
        self.before.append(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {
                    "name": "control-plane",
                    "namespace": "perf-platform",
                    "uid": "deployment-uid",
                    "resourceVersion": "20",
                    "generation": 7,
                    "labels": {
                        "app.kubernetes.io/name": "perfeng-control-plane",
                        "app.kubernetes.io/instance": "control-plane",
                    },
                },
                "spec": {
                    "replicas": 0,
                    "selector": {
                        "matchLabels": {
                            "app.kubernetes.io/name": "perfeng-control-plane",
                            "app.kubernetes.io/instance": "control-plane",
                        }
                    },
                    "template": {"spec": {"serviceAccountName": "control-plane"}},
                },
                "status": {"observedGeneration": 7},
            }
        )
        self.after = deepcopy(self.before)
        self.pods = {
            "apiVersion": "v1",
            "kind": "PodList",
            "metadata": {"resourceVersion": "30"},
            "items": [],
        }
        self.namespace: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": "kube-system", "uid": "cluster-uid", "resourceVersion": "1"},
        }

    def verify(self) -> dict[str, Any]:
        return verifier.verify(
            self.preflight, self.raw, REVISION, self.before, self.pods, self.after, self.namespace
        )

    def test_owned_stable_paused_state_has_strict_observation_contract(self) -> None:
        result = self.verify()
        schema = json.loads(
            (ROOT / "deployments/maintenance-checkpoint-v1.schema.json").read_text()
        )
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        validator.validate(result)
        self.assertEqual(result["status"], "quiescence-observed")
        self.assertEqual(result["resources"][2]["resourceVersion"], "20")
        self.assertFalse(validator.is_valid({**result, "migrationAuthorized": True}))

    def test_unpaused_foreign_or_syncing_applications_are_refused(self) -> None:
        for index in (0, 1):
            for change in (
                lambda obj: obj["spec"]["syncPolicy"].update(automated={"enabled": True}),
                lambda obj: obj["spec"].pop("syncPolicy"),
                lambda obj: obj.update(operation={"sync": {}}),
                lambda obj: obj.update(status={"operationState": {"phase": "Running"}}),
                lambda obj: obj.update(status={"operationState": {"phase": "Terminating"}}),
                lambda obj: obj["metadata"].update(ownerReferences=[{"kind": "ApplicationSet"}]),
                lambda obj: obj["metadata"].update(deletionTimestamp="2026-10-18T00:00:00Z"),
                lambda obj: obj["spec"].update(project="foreign"),
                lambda obj: obj["spec"].update(
                    destination={"server": "https://foreign", "namespace": "argocd"}
                ),
            ):
                with self.subTest(index=index, change=change):
                    original = deepcopy(self.before[index])
                    change(self.before[index])
                    with self.assertRaises((ValueError, KeyError)):
                        self.verify()
                    self.before[index] = original
        self.before[1]["spec"]["sources"][0]["targetRevision"] = "main"
        with self.assertRaisesRegex(ValueError, "child sources"):
            self.verify()

    def test_nonzero_stale_foreign_or_replaced_deployment_is_refused(self) -> None:
        for change in (
            lambda obj: obj["spec"].update(replicas=1),
            lambda obj: obj["status"].update(replicas=1),
            lambda obj: obj["status"].update(readyReplicas=1),
            lambda obj: obj["status"].update(observedGeneration=6),
            lambda obj: obj["spec"]["template"]["spec"].update(serviceAccountName="foreign"),
            lambda obj: obj["spec"].update(selector={"matchLabels": {}}),
        ):
            original = deepcopy(self.before[2])
            change(self.before[2])
            with self.assertRaises(ValueError):
                self.verify()
            self.before[2] = original
        for field in ("uid", "resourceVersion"):
            self.after[2]["metadata"][field] = "replaced"
            with self.assertRaisesRegex(ValueError, "changed during"):
                self.verify()
            self.after = deepcopy(self.before)

    def test_remaining_terminating_or_paginated_pods_refuse_absence(self) -> None:
        for pods in (
            {**self.pods, "items": [{"metadata": {"deletionTimestamp": "now"}}]},
            {**self.pods, "metadata": {"continue": "next", "resourceVersion": "30"}},
            {**self.pods, "metadata": {}},
            {**self.pods, "kind": "List"},
        ):
            with self.subTest(pods=pods), self.assertRaises((ValueError, KeyError)):
                verifier.verify(
                    self.preflight,
                    self.raw,
                    REVISION,
                    self.before,
                    pods,
                    self.after,
                    self.namespace,
                )

    def test_mismatched_request_lock_cluster_and_incomplete_samples_refuse(self) -> None:
        self.namespace["metadata"]["uid"] = "different"
        with self.assertRaisesRegex(ValueError, "incarnation"):
            self.verify()
        self.namespace["metadata"]["uid"] = "cluster-uid"
        self.preflight["selectionRevision"] = "d" * 40
        with self.assertRaisesRegex(ValueError, "preflight"):
            self.verify()
        self.preflight["selectionRevision"] = REVISION
        with self.assertRaisesRegex(ValueError, "lock differs"):
            verifier.verify(
                self.preflight,
                self.raw + b"\n",
                REVISION,
                self.before,
                self.pods,
                self.after,
                self.namespace,
            )
        self.after.pop()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.verify()

    def test_bounded_authenticated_reader_rejects_redirects_and_bad_responses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            account = Path(temporary)
            (account / "token").write_text("private-token")
            for status, raw in (
                (200, b'{"kind":"PodList"}'),
                (403, b"{}"),
                (200, b"x" * 1048577),
                (200, b'{"a":1,"a":2}'),
            ):
                response = MagicMock(status=status)
                response.read.return_value = raw
                response.__enter__.return_value = response
                opener = MagicMock()
                opener.open.return_value = response
                with (
                    patch.object(verifier, "ACCOUNT", account),
                    patch.object(verifier.ssl, "create_default_context"),
                    patch.object(verifier, "build_opener", return_value=opener),
                ):
                    read = verifier.reader()
                    if status == 200 and raw == b'{"kind":"PodList"}':
                        self.assertEqual(read(verifier.PODS)["kind"], "PodList")
                        request = opener.open.call_args.args[0]
                        self.assertEqual(request.get_method(), "GET")
                        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)
                        self.assertIn("limit=1", request.full_url)
                    else:
                        with self.assertRaises(ValueError):
                            read(verifier.PODS)
                    response.read.assert_called_once_with(1048577)
                    with self.assertRaisesRegex(ValueError, "unexpected observation"):
                        read("/api/v1/secrets")
        with self.assertRaises(ValueError):
            verifier.NoRedirect().redirect_request(None, None, 302, "", {}, "https://foreign")

    def test_output_requires_complete_reads_and_failures_do_not_leak_responses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "selection"
            (root / ".git").mkdir(parents=True)
            (root / ".git/HEAD").write_text(REVISION + "\n")
            (root / "deployments").mkdir()
            (root / "deployments/local-control-plane.json").write_bytes(self.raw)
            output = Path(temporary) / "result.json"
            paths = {"/inputs/selection": root, "/tmp/checkpoint.json": output}
            with (
                patch.dict(
                    os.environ,
                    {
                        "SELECTION_REVISION": REVISION,
                        "PREFLIGHT_RESULT": json.dumps(self.preflight),
                    },
                ),
                patch.object(verifier, "Path", side_effect=lambda value: paths[value]),
            ):
                read = MagicMock(side_effect=[*self.before, self.pods, *self.after, self.namespace])
                with patch.object(verifier, "reader", return_value=read):
                    verifier.main()
                self.assertEqual(read.call_count, 8)
                self.assertEqual(json.loads(output.read_text())["status"], "quiescence-observed")
                output.unlink()
                for error in (
                    OSError("private-token vendor-body"),
                    ValueError("private-token vendor-body"),
                ):
                    with (
                        patch.object(verifier, "reader", side_effect=error),
                        redirect_stderr(io.StringIO()) as stderr,
                    ):
                        self.assertEqual(verifier.cli(), 2)
                    self.assertFalse(output.exists())
                    self.assertNotIn("private-token", stderr.getvalue())

    def test_staged_workflow_and_rbac_preserve_read_only_boundary(self) -> None:
        parent = yaml.safe_load((DIRECTORY / "release-maintenance-checkpoint.yaml").read_text())
        child = yaml.safe_load((DIRECTORY / "verify-release-quiescence.yaml").read_text())
        self.assertEqual(parent["spec"]["serviceAccountName"], "release-maintenance-checkpoint")
        self.assertEqual(
            parent["spec"]["synchronization"]["mutexes"], [{"name": "perfeng-local-external-state"}]
        )
        steps = parent["spec"]["templates"][0]["steps"]
        self.assertEqual(steps[0][0]["templateRef"]["name"], "perfeng-release-preflight-v1")
        self.assertEqual(child["spec"]["templates"][0]["activeDeadlineSeconds"], 180)
        for path in [
            *(DIRECTORY / "rbac/maintenance").glob("*.yaml"),
            ROOT / "gitops/release-workflows-bootstrap/maintenance-application-access.yaml",
        ]:
            for obj in yaml.safe_load_all(path.read_text()):
                for rule in obj.get("rules", []):
                    if rule["resources"] == ["workflowtaskresults"]:
                        self.assertEqual(rule["verbs"], ["create", "patch"])
                    elif rule["resources"] == ["pods"]:
                        self.assertEqual(rule["verbs"], ["list"])
                        self.assertEqual(obj["metadata"]["namespace"], "perf-platform")
                    else:
                        self.assertEqual(rule["verbs"], ["get"])
                        self.assertTrue(rule["resourceNames"])


if __name__ == "__main__":
    unittest.main()
