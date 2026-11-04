"""Offline safety checks for the opt-in joint fixture."""

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "joint_fixture", Path(__file__).parent / "integration/argus-perfeng/fixture.py"
)
assert SPEC and SPEC.loader
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


def result(code=0, stdout=""):
    return subprocess.CompletedProcess([], code, stdout, "private vendor output")


class JointFixtureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.subject = fixture.Fixture(fixture.inputs(), self.root)

    def owned(self, kind, name):
        return {
            "Name": name,
            "Id": "a" * 64,
            "Labels": {fixture.LABEL: self.subject.run_id},
            "Config": {"Labels": {fixture.LABEL: self.subject.run_id}},
        }

    def test_manifest_rejects_mutable_missing_malformed_and_duplicate_pins(self):
        original = fixture.inputs()
        changes = [
            {"postgres": "postgres:17"},
            {"products": {}},
            {"go": "auto"},
            {"schemaVersion": True},
            {"products": []},
            {"postgres": None},
            {"products": {"argus": [], "perfeng-control-plane": "a" * 40}},
        ]
        path = self.root / "inputs.json"
        for change in changes:
            with self.subTest(change=change):
                path.write_text(json.dumps(original | change))
                with self.assertRaises(ValueError):
                    fixture.inputs(path)
        path.write_text('{"schemaVersion": 1, "schemaVersion": 1}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            fixture.inputs(path)

    def test_argus_artifacts_include_the_operators_needed_for_the_flow(self):
        revision = self.subject.selection["products"]["argus"]
        source = self.root / "argus"
        source.mkdir()
        (source / "go.mod").write_text(f"go {self.subject.selection['go'][2:]}\n")

        def build_artifact(args, **_options):
            if args[0] == "go":
                Path(args[args.index("-o") + 1]).write_bytes(args[-1].encode())
            return result(stdout=revision)

        with patch.object(self.subject, "command", side_effect=build_artifact):
            self.subject.artifacts("argus", revision)

        evidence = self.subject.evidence["products"]["argus"]
        self.assertEqual(evidence["sourceRevision"], revision)
        for target in (
            "catalog",
            "performance-catalog",
            "select-performance",
            "performance-requests",
            "performance-evidence",
        ):
            binary = self.root / f"argus-{target}"
            self.assertTrue(binary.is_file(), f"missing operator binary {target}")
            self.assertEqual(
                evidence["artifacts"][target]["sha256"],
                hashlib.sha256(binary.read_bytes()).hexdigest(),
            )

    def test_shared_run_directory_is_refused(self):
        shared = self.root / "shared"
        shared.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, "private owned"):
            fixture.Fixture(fixture.inputs(), shared)

    def test_malformed_inspection_is_not_deletion_authority(self):
        for value in ([None], [{"Config": None}], [{"Name": None, "Config": {"Labels": {}}}]):
            with (
                self.subTest(value=value),
                patch.object(self.subject, "docker", return_value=result(stdout=json.dumps(value))),
            ):
                with self.assertRaises(ValueError):
                    self.subject.inspect("container", "db")

    def test_default_entrypoint_never_invokes_processes(self):
        with (
            patch("sys.argv", ["fixture.py"]),
            patch.object(
                fixture.subprocess, "run", side_effect=AssertionError("unexpected external call")
            ),
        ):
            fixture.main()

    def test_foreign_resource_is_never_deleted(self):
        name = self.subject.run_id + "-argus"
        record = self.owned("container", name)
        record["Config"]["Labels"][fixture.LABEL] = "foreign"
        self.subject.resources = [("container", name)]
        with patch.object(
            self.subject, "docker", return_value=result(stdout=json.dumps([record]))
        ) as docker:
            self.assertFalse(self.subject.cleanup())
        self.assertEqual(docker.call_count, 1)
        self.assertEqual(self.subject.evidence["cleanup"], "unresolved")

    def test_cleanup_survives_deadline_and_uses_inspected_ids_in_reverse_order(self):
        self.subject.deadline = -1
        self.subject.resources = [("network", "net"), ("container", "db")]
        responses = []
        for kind, name in reversed(self.subject.resources):
            responses.extend(
                [result(stdout=json.dumps([self.owned(kind, name)])), result(), result()]
            )
        with patch.object(fixture.subprocess, "run", side_effect=responses) as command:
            self.assertTrue(self.subject.cleanup())
        calls = [call.args[0] for call in command.call_args_list]
        self.assertEqual(calls[1], ["docker", "rm", "-f", "a" * 64])
        self.assertEqual(calls[4], ["docker", "network", "rm", "a" * 64])
        self.assertTrue(all(call.kwargs["timeout"] == 30 for call in command.call_args_list))

    def test_removal_error_or_unreachable_daemon_is_not_cleanup_success(self):
        self.subject.resources = [("container", "db")]
        for responses in (
            [result(1)],
            [result(stdout=json.dumps([self.owned("container", "db")])), result(1), result(1)],
        ):
            with (
                self.subTest(responses=responses),
                patch.object(self.subject, "docker", side_effect=responses),
            ):
                self.assertFalse(self.subject.cleanup())

    def test_lost_create_acknowledgement_remains_tracked(self):
        with patch.object(self.subject, "docker", side_effect=ValueError("lost response")):
            with self.assertRaises(ValueError):
                self.subject.create("container", "argus", [])
        self.assertEqual(self.subject.resources, [("container", self.subject.run_id + "-argus")])

    def test_partial_failure_cleans_up_and_writes_safe_failed_evidence(self):
        with (
            patch.object(self.subject, "artifacts", side_effect=ValueError("secret")),
            patch.object(self.subject, "cleanup", wraps=self.subject.cleanup) as cleanup,
        ):
            with self.assertRaises(ValueError):
                self.subject.run()
        cleanup.assert_called_once()
        evidence = json.loads((self.root / "evidence.json").read_text())
        self.assertFalse(evidence["preparationReady"])
        self.assertEqual(evidence["productAcceptance"], "not-run")
        self.assertNotIn("secret", json.dumps(evidence))

    def test_database_checks_actual_runtime_authority_and_keeps_secrets_off_argv(self):
        self.subject.evidence["products"]["argus"] = {"sourceRevision": "a" * 40, "artifacts": {}}
        with (
            patch.object(self.subject, "create", return_value="owned-db"),
            patch.object(self.subject, "inspect", return_value={}),
            patch.object(
                self.subject,
                "docker",
                side_effect=[
                    result(),
                    result(),
                    result(),
                    result(stdout="argus_runtime|argus|f|f|f|t|f\n"),
                ],
            ) as docker,
        ):
            self.subject.database("argus")
        credentials = self.root / "argus-credentials.json"
        self.assertEqual(credentials.stat().st_mode & 0o777, 0o600)
        password = json.loads(credentials.read_text())["runtime"].split(":")[2].split("@")[0]
        self.assertNotIn(password, str([call.args for call in docker.call_args_list]))
        self.assertNotIn(password, json.dumps(self.subject.evidence))
        credentials.unlink()
        with (
            patch.object(self.subject, "create", return_value="owned-db"),
            patch.object(self.subject, "inspect", return_value={}),
            patch.object(
                self.subject,
                "docker",
                side_effect=[
                    result(),
                    result(),
                    result(),
                    result(stdout="argus_runtime|argus|t|f|f|t|t"),
                ],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "authority mismatch"):
                self.subject.database("argus")

    def test_flow_driver_failure_exits_prepared_context_and_cleans_up(self):
        with (
            patch.object(self.subject, "artifacts"),
            patch.object(self.subject, "database"),
            patch.object(self.subject, "create"),
            patch.object(self.subject, "docker"),
            patch.object(self.subject, "cleanup", wraps=self.subject.cleanup) as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "flow failed"):
                with self.subject.prepared() as prepared:
                    self.assertTrue(prepared.evidence["preparationReady"])
                    raise RuntimeError("flow failed")
        cleanup.assert_called_once()
        self.assertEqual(json.loads((self.root / "evidence.json").read_text())["cleanup"], "passed")

    def test_vendor_errors_and_timeouts_do_not_disclose_credentials(self):
        for response in (result(1, "postgres://secret"), subprocess.TimeoutExpired(["private"], 1)):
            with (
                self.subTest(response=response),
                patch.object(
                    fixture.subprocess,
                    "run",
                    **(
                        {"side_effect": response}
                        if isinstance(response, Exception)
                        else {"return_value": response}
                    ),
                ),
            ):
                with self.assertRaisesRegex(ValueError, "fixture command") as error:
                    self.subject.command(["test"])
                self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
