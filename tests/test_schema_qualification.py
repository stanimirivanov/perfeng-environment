"""Offline source-byte, read-only, native wiring and credential refusal boundaries."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import yaml
from test_release_workflows_integration import BASH

from scripts import validate_cluster

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"


class SchemaQualificationTests(unittest.TestCase):
    def setUp(self):
        self.parent = yaml.safe_load(
            (DIRECTORY / "release-schema-qualification-v1.yaml").read_text()
        )
        self.leaf = yaml.safe_load((DIRECTORY / "schema-qualification-job-v1.yaml").read_text())
        self.config = yaml.safe_load((DIRECTORY / "schema-qualification-sql-v1.yaml").read_text())
        self.job = yaml.safe_load(self.leaf["spec"]["templates"][0]["resource"]["manifest"])
        self.sql = self.config["data"]["schema-qualification.sql"]
        self.inventory = json.loads(self.config["data"]["migration-inventory.json"])
        self.selected = json.loads((ROOT / "deployments/local-control-plane.json").read_text())[
            "controlPlane"
        ]

    def test_parent_reuses_complete_admission_chain_and_only_qualifies(self):
        grants = yaml.safe_load((DIRECTORY / "release-runtime-grants-v2.yaml").read_text())["spec"]
        spec = self.parent["spec"]
        for key in ("arguments", "serviceAccountName", "synchronization", "activeDeadlineSeconds"):
            self.assertEqual(spec[key], grants[key])
        self.assertEqual(spec["entrypoint"], "qualify")
        self.assertEqual(len(spec["templates"]), 1)
        flow = spec["templates"][0]
        self.assertEqual(flow["steps"][:-1], grants["templates"][0]["steps"][:-1])
        self.assertEqual(
            flow["steps"][-1],
            [
                {
                    "name": "qualify",
                    "templateRef": {
                        "name": "perfeng-schema-qualification-job-v1",
                        "template": "run",
                    },
                }
            ],
        )
        self.assertEqual(
            flow["outputs"]["parameters"][-1]["valueFrom"]["parameter"],
            "{{steps.qualify.outputs.parameters.job-name}}",
        )
        for forbidden in ("retryStrategy", "continueOn", "hooks", "onExit", '"when"'):
            self.assertNotIn(forbidden, json.dumps(self.parent))

    def test_inventory_and_sql_match_exact_selected_upstream_git_bytes(self):
        upstream = ROOT.parent / "perfeng-control-plane"
        self.assertTrue(
            (upstream / ".git").exists(),
            "Pinned perfeng-control-plane checkout required for source qualification",
        )
        git = ["git", "-c", "safe.directory=" + upstream.as_posix(), "-C", str(upstream)]
        revision = self.selected["sourceRevision"]
        paths = subprocess.run(
            [*git, "ls-tree", "-r", "--name-only", revision, "internal/postgres/migrations"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.splitlines()
        self.assertEqual(len(paths), 18)
        expected = {}
        for path in paths:
            self.assertRegex(path, r"^internal/postgres/migrations/[0-9]{4}_[a-z0-9_]+\.sql$")
            raw = subprocess.run(
                [*git, "show", revision + ":" + path], check=True, capture_output=True, timeout=20
            ).stdout
            expected[Path(path).name] = hashlib.sha256(raw).hexdigest()
        self.assertEqual(
            self.inventory, {"schemaVersion": 1, **self.selected, "migrations": expected}
        )
        pairs = re.findall(r"\('([0-9]{4}_[a-z0-9_]+\.sql)', '([0-9a-f]{64})'\)", self.sql)
        self.assertEqual(len(pairs), 18)
        self.assertEqual(dict(pairs), expected)
        self.assertEqual(sorted(int(name[:4]) for name in expected), list(range(1, 19)))
        for key, field in (
            ("perfeng.io/source-revision", "sourceRevision"),
            ("perfeng.io/selected-image", "image"),
        ):
            self.assertEqual(self.config["metadata"]["annotations"][key], self.selected[field])

    def test_sql_is_read_only_and_has_explicit_refusal_guards(self):
        self.assertTrue(
            self.sql.splitlines()[1].startswith("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;")
        )
        self.assertTrue(self.sql.endswith("COMMIT;\n"))
        self.assertIn("current_setting('transaction_read_only') <> 'on'", self.sql)
        self.assertIn("current_setting('transaction_isolation') <> 'repeatable read'", self.sql)
        for guard in (
            "current_user <> 'perfeng_migration' OR session_user <> 'perfeng_migration'",
            "rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls",
            "pg_get_userbyid(nspowner)=current_user",
            "c.relkind<>'r' OR pg_get_userbyid(c.relowner)<>current_user",
            "count(*) FROM actual)=18",
            'count(DISTINCT version COLLATE "C") FROM actual)=18',
            'FULL OUTER JOIN actual a ON a.version COLLATE "C"=e.version COLLATE "C"',
            "e.version IS NULL OR a.version IS NULL",
            'a.sha256 COLLATE "C" IS DISTINCT FROM e.sha256 COLLATE "C"',
            "c.relrowsecurity OR c.relforcerowsecurity",
            "a.applied_at IS NULL OR NOT isfinite(a.applied_at)",
            "IF ledger_matches IS DISTINCT FROM true THEN",
        ):
            self.assertIn(guard, self.sql)
        for timeout in (
            "lock_timeout = '5s'",
            "statement_timeout = '15s'",
            "idle_in_transaction_session_timeout = '15s'",
        ):
            self.assertIn(timeout, self.sql)
        # This is not a SQL engine proof; live transaction/catalog behavior is deferred.
        code = "\n".join(line for line in self.sql.splitlines() if not line.startswith("--"))
        self.assertIsNone(
            re.search(
                r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY|EXECUTE)\b",
                code,
                re.I,
            )
        )
        self.assertNotIn("pg_advisory", code)

    def test_job_is_fixed_hardened_bounded_and_has_no_api_or_admin_access(self):
        template = self.leaf["spec"]["templates"][0]
        resource = template["resource"]
        self.assertEqual(template["activeDeadlineSeconds"], 240)
        self.assertEqual(resource["action"], "create")
        self.assertFalse(resource["setOwnerReference"])
        self.assertEqual(resource["flags"], ["--request-timeout=10s"])
        self.assertEqual(resource["successCondition"], "status.succeeded > 0")
        self.assertEqual(resource["failureCondition"], "status.failed > 0")
        self.assertEqual(self.job["metadata"]["name"], "perfeng-schema-qualify-{{workflow.uid}}")
        expanded = self.job["metadata"]["name"].replace(
            "{{workflow.uid}}", "11111111-1111-1111-1111-111111111111"
        )
        self.assertLessEqual(len(expanded), 63)
        self.assertRegex(expanded, r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
        self.assertEqual(self.job["metadata"]["namespace"], "perf-platform")
        self.assertEqual(self.job["spec"]["backoffLimit"], 0)
        self.assertEqual(self.job["spec"]["activeDeadlineSeconds"], 180)
        self.assertNotIn("ttlSecondsAfterFinished", self.job["spec"])
        pod = self.job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["restartPolicy"], "Never")
        self.assertEqual(pod["serviceAccountName"], "default")
        self.assertEqual(pod["securityContext"]["runAsUser"], 999)
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        grant = yaml.safe_load(
            yaml.safe_load((DIRECTORY / "runtime-grants-job-v1.yaml").read_text())["spec"][
                "templates"
            ][0]["resource"]["manifest"]
        )
        container = pod["containers"][0]
        self.assertEqual(
            container["image"], grant["spec"]["template"]["spec"]["containers"][0]["image"]
        )
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"], {"drop": ["ALL"]})
        refs = [e["valueFrom"]["secretKeyRef"] for e in container["env"] if "valueFrom" in e]
        self.assertEqual(refs, [{"name": "perfeng-control-plane-database", "key": "migration-url"}])
        self.assertIn(
            "export PGOPTIONS='-c default_transaction_read_only=on'", container["args"][0]
        )
        self.assertEqual(pod["volumes"][0]["configMap"]["name"], self.config["metadata"]["name"])
        self.assertEqual(pod["volumes"][1]["emptyDir"]["sizeLimit"], "16Mi")

    def test_staged_config_is_immutable_required_and_outside_active_root(self):
        bootstrap = ROOT / "gitops/release-workflows-bootstrap"
        project = yaml.safe_load((bootstrap / "project.yaml").read_text())
        app = yaml.safe_load((bootstrap / "application.yaml").read_text())
        self.assertTrue(self.config["immutable"])
        validate_cluster.validate_release_project(
            project, app, [self.parent, self.leaf, self.config]
        )
        broken = deepcopy(self.config)
        broken["data"].pop("schema-qualification.sql")
        with self.assertRaisesRegex(ValueError, "Job ConfigMap"):
            validate_cluster.validate_release_project(
                project, app, [self.parent, self.leaf, broken]
            )
        paths = yaml.safe_load((DIRECTORY / "kustomization.yaml").read_text())["resources"]
        for filename in (
            "release-schema-qualification-v1.yaml",
            "schema-qualification-job-v1.yaml",
            "schema-qualification-sql-v1.yaml",
        ):
            self.assertIn(filename, paths)
        self.assertNotIn(
            "release-workflows", (ROOT / "gitops/environments/local/kustomization.yaml").read_text()
        )

    @unittest.skipUnless(BASH, "Bash required")
    def test_actual_worker_refuses_bad_inputs_and_redacts_sql_failure(self):
        assert BASH
        script = self.job["spec"]["template"]["spec"]["containers"][0]["args"][0].replace(
            "/tmp/qualification.private", '"$PRIVATE"'
        )
        setup = r"""psql() {
 printf called > "$MARKER"
 [[ $PGOPTIONS == '-c default_transaction_read_only=on' && ${MIGRATION_URL+x} != x && ${PGSERVICE+x} != x && ${PGHOSTADDR+x} != x ]] || return 91
 [[ $PGUSER == perfeng_migration && $PGDATABASE == perfeng && $PGHOST == postgres.perf-platform.svc.cluster.local ]] || return 92
 [[ ${#PGPASSWORD} == 43 ]] || return 93
 [[ $* == '-X -w -qAt -v ON_ERROR_STOP=1 -f /sql/schema-qualification.sql' ]] || return 94
 printf 'private-vendor-output\n' >&2
 return "$PSQL_EXIT"
}
"""
        valid = (
            "postgresql://perfeng_migration:"
            + "s" * 43
            + "@postgres.perf-platform.svc.cluster.local:5432/perfeng?sslmode=disable"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, url, psql_code, expected in (
                ("valid", valid, 0, 0),
                ("sql-refusal", valid, 3, 1),
                ("wrong-owner", valid.replace("perfeng_migration", "postgres"), 0, 64),
                (
                    "wrong-host",
                    valid.replace("postgres.perf-platform", "foreign.perf-platform"),
                    0,
                    64,
                ),
                ("extra-options", valid + "&options=-crole=postgres", 0, 64),
                ("bad-password", valid.replace("s" * 43, "s" * 42), 0, 64),
            ):
                marker = root / label
                result = subprocess.run(
                    [BASH, "-eu", "-c", setup + script],
                    env={
                        **os.environ,
                        "BASH_ENV": "",
                        "MIGRATION_URL": url,
                        "PRIVATE": (root / "private").as_posix(),
                        "MARKER": marker.as_posix(),
                        "PSQL_EXIT": str(psql_code),
                        "PGOPTIONS": "unsafe",
                        "PGSERVICE": "unsafe",
                        "PGHOSTADDR": "unsafe",
                    },
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                with self.subTest(label=label):
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual(marker.exists(), label in {"valid", "sql-refusal"})
                    self.assertNotIn("private-vendor-output", result.stdout + result.stderr)
                    self.assertNotIn("s" * 43, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
