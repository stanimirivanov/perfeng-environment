"""Credential isolation, bounded read-back and exact restart/cleanup identities."""

import base64
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from kubernetes_acceptance_fixtures import (
    CANCELLED,
    NS_UID,
    POD_UID,
    ROOT,
    RUN,
    fixture,
    reservation,
)

from scripts.kubernetes_acceptance import api_client, lifecycle, transport
from scripts.kubernetes_acceptance.database import Database


class AcceptanceTransportTests(unittest.TestCase):
    def setUp(self):
        self.selected, self.claims, _, self.contents = fixture()
        self.session = transport.Session(ROOT, "m04-acceptance-0123456789abcdef")
        self.session.uid = POD_UID

    def live_client(self):
        value = copy.deepcopy(self.session.expected)
        value["metadata"]["uid"] = POD_UID
        value["status"] = {"phase": "Running"}
        return value

    def test_client_reuses_pins_and_keeps_each_credential_in_its_own_container(self):
        spec = self.session.expected["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(spec["activeDeadlineSeconds"], 3600)
        api, reader = spec["containers"]
        self.assertIn("@sha256:", api["image"])
        self.assertIn("@sha256:", reader["image"])
        self.assertEqual(
            {item["name"] for item in api["env"]},
            {"PERFENG_OPERATOR_TOKEN", "HOME", "PYTHONDONTWRITEBYTECODE"},
        )
        self.assertNotIn("PERFENG_OPERATOR_TOKEN", str(reader))
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", str(api))
        self.assertNotIn("serviceAccountToken", str(spec))
        self.assertNotIn("put-object", transport.READ_OBJECT)
        self.assertNotIn("delete-object", transport.READ_OBJECT)
        self.assertIn('--range "bytes=0-$2"', transport.READ_OBJECT)

    @patch("scripts.kubernetes_acceptance.transport.cluster.run")
    def test_exec_refuses_replaced_or_modified_client_without_running_payload(self, run):
        for label, mutate in {
            "UID": lambda value: value["metadata"].update(uid=NS_UID),
            "image": lambda value: value["spec"]["containers"][0].update(image="foreign"),
            "credentials": lambda value: value["spec"]["containers"][0].update(env=[]),
            "mounts": lambda value: value["spec"].update(volumes=[]),
            "owner": lambda value: value["metadata"]["labels"].update(owner="foreign"),
        }.items():
            with self.subTest(label=label):
                value = self.live_client()
                mutate(value)
                run.reset_mock()
                run.return_value = json.dumps(value)
                with self.assertRaises(ValueError):
                    self.session.call("GET", "/v1/runs/" + RUN)
                self.assertEqual(run.call_count, 1)
                self.assertNotIn("exec", run.call_args.args[0])

    @patch("scripts.kubernetes_acceptance.transport.cluster.run")
    def test_read_back_requires_claimed_bytes_and_metadata(self, run):
        claim = self.claims["raw-result/v2"]
        payload = self.contents["raw-result/v2"]
        metadata = {
            "sizeBytes": claim["sizeBytes"],
            "mediaType": claim["mediaType"],
            "metadata": {
                "artifact-id": claim["id"],
                "sha256": claim["sha256"],
                "format": claim["format"],
            },
        }
        response = json.dumps(metadata) + "\n" + base64.b64encode(payload).decode()
        run.side_effect = [json.dumps(self.live_client()), response]
        self.assertEqual(self.session.read(claim, RUN), payload)
        command = run.call_args.args[0]
        self.assertIn("--context", command)
        self.assertEqual(command[-1], str(len(payload)))
        run.side_effect = [json.dumps(self.live_client()), response + "YQ=="]
        with self.assertRaises(ValueError):
            self.session.read(claim, RUN)
        metadata["metadata"]["sha256"] = "0" * 64
        run.side_effect = [
            json.dumps(self.live_client()),
            json.dumps(metadata) + "\n" + base64.b64encode(payload).decode(),
        ]
        with self.assertRaises(ValueError):
            self.session.read(claim, RUN)

    def test_foreign_signed_traversing_and_oversized_references_fail_before_exec(self):
        claim = self.claims["raw-result/v2"]
        for fields in (
            {"uri": "https://external.invalid/file"},
            {"uri": claim["uri"] + "?token=hidden"},
            {"uri": f"s3://perfeng-artifacts/runs/{RUN}/../secret"},
            {"runId": CANCELLED},
            {"sizeBytes": (8 << 20) + 1},
            {"sizeBytes": True},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                transport.reference({**claim, **fields}, RUN)

    @patch("scripts.kubernetes_acceptance.api_client.urllib.request.build_opener")
    @patch.dict("os.environ", {"PERFENG_OPERATOR_TOKEN": "test-token-never-printed"})
    def test_api_uses_fixed_origin_disables_redirects_and_bounds_responses(self, build):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"id":"example"}'
        response.headers.get_content_type.return_value = "application/json"
        response.code = 200
        build.return_value.open.return_value = response
        value = api_client.call({"method": "GET", "path": "/v1/runs/" + RUN})
        self.assertEqual(value["status"], 200)
        request = build.return_value.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith(api_client.API + "/v1/"))
        self.assertEqual(build.return_value.open.call_count, 1)
        response.read.assert_called_once_with(api_client.LIMIT + 1)
        self.assertTrue(
            any(isinstance(item, api_client.NoRedirect) for item in build.call_args.args)
        )
        self.assertIsNone(
            api_client.NoRedirect().redirect_request(
                request, None, 302, "Moved", {}, "https://outside.invalid"
            )
        )
        with self.assertRaises(ValueError):
            api_client.call({"method": "GET", "path": "https://outside.invalid"})

    @patch("scripts.kubernetes_acceptance.database.cluster.run")
    def test_database_inspection_is_server_read_only_and_does_not_export_credentials(self, run):
        run.return_value = '{"runs":0,"holds":0}'
        Database(ROOT).exclusive()
        command = run.call_args.args[0]
        sql = run.call_args.kwargs["input_data"].decode()
        self.assertIn("BEGIN READ ONLY", sql)
        self.assertIn("statement_timeout = '5s'", sql)
        self.assertIn("lock_timeout = '2s'", sql)
        self.assertNotIn("DELETE", sql)
        self.assertNotIn("UPDATE", sql)
        self.assertNotIn("INSERT", sql)
        self.assertIn("POSTGRES_PASSWORD_FILE", command[-1])
        self.assertNotIn("get", command)
        run.return_value = '{"runs":1,"holds":0}'
        with self.assertRaises(ValueError):
            Database(ROOT).exclusive()
        run.reset_mock()
        with self.assertRaises(ValueError):
            Database(ROOT).reservation("x'; DELETE FROM anything; --")
        run.assert_not_called()


class AcceptanceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.selected = fixture()[0]
        self.lifecycle = lifecycle.Lifecycle(ROOT, self.selected)
        self.database = Mock()
        self.lifecycle.database = self.database

    @patch("scripts.kubernetes_acceptance.lifecycle.cluster.run")
    def test_cleanup_requires_real_fence_and_absence_not_only_database_attestation(self, run):
        row = reservation(released=True)
        self.database.reservation.return_value = row
        labels = {"perfeng.io/run-id": CANCELLED, "app.kubernetes.io/managed-by": lifecycle.OWNER}
        annotations = {"perfeng.io/benchmark-plan-sha256": row["planSha256"]}
        namespace = {"metadata": {"uid": NS_UID, "labels": labels, "annotations": annotations}}
        quota = {
            "metadata": {"labels": labels, "annotations": annotations},
            "spec": {"hard": {"pods": "0"}},
            "status": {"hard": {"pods": "0"}},
        }
        objects = {"namespace": namespace, "resourcequota": quota}
        self.lifecycle.get = Mock(side_effect=lambda kind, name, ns: objects.get(kind, {}))
        run.return_value = '{"items":[]}'
        self.assertEqual(self.lifecycle.cleanup(CANCELLED), row)
        quota["spec"]["hard"]["pods"] = "10"
        with self.assertRaisesRegex(ValueError, "closed"):
            self.lifecycle.cleanup(CANCELLED)
        quota["spec"]["hard"]["pods"] = "0"
        objects["clusterrolebinding"] = {"metadata": {"name": "retained-authority"}}
        with self.assertRaisesRegex(ValueError, "authority"):
            self.lifecycle.cleanup(CANCELLED)
        del objects["clusterrolebinding"]
        run.return_value = '{"items":[{"metadata":{"name":"remaining-workload"}}]}'
        with self.assertRaisesRegex(ValueError, "termination"):
            self.lifecycle.cleanup(CANCELLED)
        self.assertTrue(all("get" in call.args[0] for call in run.call_args_list))

    def test_executor_requires_the_durable_uid_binding_and_selected_runner_not_labels_alone(self):
        sha = "d" * 64
        job = {
            "metadata": {
                "name": CANCELLED,
                "namespace": "perf-generators",
                "uid": POD_UID,
                "labels": {
                    "perfeng.io/run-id": CANCELLED,
                    "app.kubernetes.io/managed-by": lifecycle.OWNER,
                },
                "annotations": {"perfeng.io/execution-spec-sha256": sha},
            },
            "spec": {
                "template": {
                    "spec": {
                        "serviceAccountName": CANCELLED + "-benchmark",
                        "initContainers": [
                            {"image": self.selected.entry["rawProducer"]["artifact"]["image"]}
                        ],
                        "containers": [
                            {"image": self.selected.entry["rawProducer"]["artifact"]["image"]}
                        ],
                    }
                }
            },
        }
        self.lifecycle.get = Mock(return_value=job)
        bound = {
            "jobName": CANCELLED,
            "namespace": "perf-generators",
            "jobUid": POD_UID,
            "specSha256": sha,
        }
        self.database.execution.return_value = bound
        self.assertEqual(self.lifecycle.executor(CANCELLED), job)
        self.database.execution.return_value = {}
        self.assertEqual(self.lifecycle.executor(CANCELLED), {})
        self.database.execution.return_value = {**bound, "jobUid": NS_UID}
        with self.assertRaisesRegex(ValueError, "foreign"):
            self.lifecycle.executor(CANCELLED)

    def test_cleanup_proof_requires_exact_identity_and_durable_drain(self):
        valid = reservation(released=True)
        lifecycle.cleanup_proof(valid, CANCELLED, self.selected)
        for label, mutate in {
            "unreleased": lambda value: value.update(releasedAt=None),
            "early release": lambda value: value.update(releasedAt="2026-10-16T12:01:59Z"),
            "short drain": lambda value: value.update(notBefore="2026-10-16T12:01:59Z"),
            "false proof": lambda value: value["proof"].update(authorityRemoved=False),
            "integer proof": lambda value: value["proof"].update(authorityRemoved=1),
            "foreign plan": lambda value: value["proof"].update(planSha256="d" * 64),
            "new namespace": lambda value: value.update(namespaceUid=POD_UID),
        }.items():
            with self.subTest(label=label):
                value = copy.deepcopy(valid)
                mutate(value)
                with self.assertRaises(ValueError):
                    lifecycle.cleanup_proof(value, CANCELLED, self.selected)

    @patch("scripts.kubernetes_acceptance.lifecycle.cluster.run")
    def test_restart_deletes_exact_uid_rv_and_never_changes_template_or_selects_broadly(self, run):
        held = reservation()
        self.database.reservation.return_value = held
        before = {
            "metadata": {
                "name": "control-plane-abcdef-abcde",
                "uid": POD_UID,
                "resourceVersion": "123",
            }
        }
        after = {"metadata": {"uid": NS_UID}}
        self.lifecycle.controller = Mock(side_effect=[before, after])
        with redirect_stdout(io.StringIO()):
            result = self.lifecycle.restart(CANCELLED, held)
        self.assertEqual(result, {"beforeUid": POD_UID, "afterUid": NS_UID})
        deletion = run.call_args_list[0]
        self.assertIn("--raw", deletion.args[0])
        self.assertNotIn("-l", deletion.args[0])
        self.assertNotIn("--force", str(run.call_args_list))
        self.assertNotIn("patch", str(run.call_args_list))
        options = json.loads(deletion.kwargs["input_data"])
        self.assertEqual(options["preconditions"], {"uid": POD_UID, "resourceVersion": "123"})
        self.assertNotIn("gracePeriodSeconds", options)
        self.assertIn("--for=delete", run.call_args_list[1].args[0])

    @patch("scripts.kubernetes_acceptance.lifecycle.cluster.run")
    def test_changed_hold_or_unrelated_run_refuses_restart_before_deletion(self, run):
        held = reservation()
        self.lifecycle.controller = Mock(return_value={})
        self.database.reservation.return_value = {**held, "namespaceUid": POD_UID}
        with self.assertRaises(ValueError):
            self.lifecycle.restart(CANCELLED, held)
        run.assert_not_called()
        self.database.exclusive.side_effect = ValueError("another Run")
        with self.assertRaises(ValueError):
            self.lifecycle.restart(CANCELLED, held)
        run.assert_not_called()

    @patch("scripts.kubernetes_acceptance.lifecycle.control_plane_health.validate_pods")
    @patch("scripts.kubernetes_acceptance.lifecycle.control_plane_health.validate_deployment")
    @patch("scripts.kubernetes_acceptance.lifecycle.cluster.run")
    def test_restart_owner_chain_is_verified_not_just_a_matching_label(
        self, run, validate_deployment, validate_pods
    ):
        pod = {
            "metadata": {
                "uid": POD_UID,
                "name": "control-plane-abcdef-abcde",
                "resourceVersion": "123",
                "ownerReferences": [
                    {
                        "controller": True,
                        "kind": "ReplicaSet",
                        "name": "control-plane-abcdef",
                        "uid": "rs-uid",
                    }
                ],
            }
        }
        run.return_value = json.dumps({"items": [pod]})
        self.lifecycle.get = Mock(
            side_effect=[
                {"metadata": {"uid": "deployment-uid"}},
                {
                    "metadata": {
                        "uid": "rs-uid",
                        "ownerReferences": [
                            {
                                "controller": True,
                                "kind": "Deployment",
                                "uid": "foreign",
                                "name": "control-plane",
                            }
                        ],
                    }
                },
            ]
        )
        with self.assertRaises(ValueError):
            self.lifecycle.controller()
        self.assertEqual(run.call_count, 1)
