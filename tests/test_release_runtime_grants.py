"""Offline staged grant ordering, fixed privilege policy and credential boundaries."""

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


class RuntimeGrantsTests(unittest.TestCase):
    def setUp(self):
        self.parent = yaml.safe_load((DIRECTORY / "release-runtime-grants-v1.yaml").read_text())
        self.leaf = yaml.safe_load((DIRECTORY / "runtime-grants-job-v1.yaml").read_text())
        self.template = self.leaf["spec"]["templates"][0]
        self.job = yaml.safe_load(self.template["resource"]["manifest"])
        self.config = yaml.safe_load((DIRECTORY / "runtime-grants-sql-v1.yaml").read_text())
        self.sql = self.config["data"]["runtime-grants.sql"]

    def test_grants_are_last_after_pinned_inputs_shared_intent_and_fresh_quiescence(self):
        spec = self.parent["spec"]
        self.assertEqual(spec["serviceAccountName"], "release-runtime-grants")
        self.assertEqual(spec["activeDeadlineSeconds"], 1200)
        self.assertEqual(
            spec["synchronization"], {"mutexes": [{"name": "perfeng-local-external-state"}]}
        )
        self.assertEqual(
            [p["name"] for p in spec["arguments"]["parameters"]],
            ["request-revision", "request-path", "request-sha256", "selection-revision"],
        )
        steps = spec["templates"][0]["steps"]
        self.assertTrue(all(len(g) == 1 for g in steps))
        self.assertEqual(
            [g[0]["name"] for g in steps],
            ["preflight", "task-selection", "migration-intent", "quiescence", "grant"],
        )
        self.assertEqual(
            [g[0]["templateRef"] for g in steps],
            [
                {"name": "perfeng-release-preflight-v3", "template": "preflight"},
                {"name": "perfeng-release-task-selection-v3", "template": "run"},
                {"name": "perfeng-release-schema-migration-v1", "template": "migration-intent"},
                {"name": "perfeng-verify-release-quiescence-v3", "template": "run"},
                {"name": "perfeng-runtime-grants-job-v1", "template": "run"},
            ],
        )
        selected = {p["name"]: p["value"] for p in steps[1][0]["arguments"]["parameters"]}
        self.assertEqual(
            selected["expected-lock-digest"],
            "sha256:"
            + hashlib.sha256(
                (ROOT / "deployments/local-control-plane.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(selected["expected-lock-path"], "deployments/local-control-plane.json")
        self.assertEqual(selected["expected-environment"], "perfeng-local")
        for forbidden in ("retryStrategy", "continueOn", "onExit", "hooks", '"when"'):
            self.assertNotIn(forbidden, json.dumps(self.parent))

    def test_grant_transaction_preserves_reviewed_matrix_and_checks_owner_before_writes(self):
        def grants(text):
            return [
                line.strip() for line in text.splitlines() if re.match(r"\s*(GRANT|REVOKE)\b", line)
            ]

        reviewed = (ROOT / "tests/integration/release-workflows/runtime-grants.sql").read_text()
        self.assertEqual(grants(self.sql), grants(reviewed))
        self.assertTrue(self.sql.startswith("BEGIN;\n"))
        self.assertTrue(self.sql.endswith("COMMIT;\n"))
        self.assertLess(
            self.sql.index("current_user <> 'perfeng_migration'"), self.sql.index("REVOKE")
        )
        self.assertLess(
            self.sql.index("pg_get_userbyid(c.relowner)<>current_user"), self.sql.index("REVOKE")
        )
        self.assertLess(self.sql.index("a.attacl IS NOT NULL"), self.sql.index("REVOKE"))
        self.assertLess(self.sql.index("$verify$;"), self.sql.index("COMMIT;"))
        self.assertIn("pg_advisory_xact_lock(70657266656)", self.sql)
        self.assertIn("SET LOCAL lock_timeout = '5s'", self.sql)
        self.assertIn("SET LOCAL search_path = pg_catalog", self.sql)
        self.assertIn("has_table_privilege", self.sql)
        self.assertIn("WITH GRANT OPTION", self.sql)
        self.assertIn("schema_migrations", self.sql)
        self.assertIn("pg_auth_members", self.sql)
        self.assertNotRegex(self.sql, r"(?m)^\s*(CREATE|ALTER|DROP|DELETE|INSERT|UPDATE)\b")
        self.assertTrue(self.config["immutable"])

    def test_job_is_fixed_owner_only_bounded_hardened_and_retained(self):
        resource = self.template["resource"]
        self.assertEqual(resource["action"], "create")
        self.assertEqual(resource["successCondition"], "status.succeeded > 0")
        self.assertEqual(resource["failureCondition"], "status.failed > 0")
        self.assertEqual(self.template["activeDeadlineSeconds"], 240)
        self.assertEqual(self.job["metadata"]["name"], "perfeng-runtime-grants-{{workflow.uid}}")
        self.assertNotIn("setOwnerReference", resource)
        self.assertNotIn("ownerReferences", self.job["metadata"])
        self.assertEqual(self.job["spec"]["backoffLimit"], 0)
        self.assertEqual(self.job["spec"]["activeDeadlineSeconds"], 180)
        self.assertNotIn("ttlSecondsAfterFinished", self.job["spec"])
        pod = self.job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["restartPolicy"], "Never")
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        container = pod["containers"][0]
        probe = yaml.safe_load(
            (ROOT / "tests/integration/release-workflows/runtime-grants-workflow.yaml").read_text()
        )
        image = yaml.safe_load(probe["spec"]["templates"][0]["resource"]["manifest"])["spec"][
            "template"
        ]["spec"]["containers"][0]["image"]
        self.assertEqual(container["image"], image)
        self.assertEqual(
            [e["valueFrom"]["secretKeyRef"] for e in container["env"] if "valueFrom" in e],
            [{"name": "perfeng-control-plane-database", "key": "migration-url"}],
        )
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"], {"drop": ["ALL"]})
        self.assertEqual([v["name"] for v in pod["volumes"]], ["sql", "scratch"])
        self.assertNotIn("perfeng-postgres-auth", json.dumps(self.job))

    def test_staged_project_requires_sql_configmap_and_exact_key_without_broadening(self):
        bootstrap = ROOT / "gitops/release-workflows-bootstrap"
        project = yaml.safe_load((bootstrap / "project.yaml").read_text())
        application = yaml.safe_load((bootstrap / "application.yaml").read_text())
        objects = [self.config, self.leaf, self.parent]
        validate_cluster.validate_release_project(project, application, objects)
        for config in (None, {**deepcopy(self.config), "data": {"wrong.sql": "SELECT 1;"}}):
            with self.assertRaisesRegex(ValueError, "Job ConfigMap"):
                validate_cluster.validate_release_project(
                    project, application, [self.leaf, self.parent] + ([config] if config else [])
                )
        resource_paths = yaml.safe_load((DIRECTORY / "kustomization.yaml").read_text())["resources"]
        for name in (
            "release-runtime-grants-v1.yaml",
            "runtime-grants-job-v1.yaml",
            "runtime-grants-sql-v1.yaml",
        ):
            self.assertIn(name, resource_paths)
        self.assertNotIn(
            "release-workflows", (ROOT / "gitops/environments/local/kustomization.yaml").read_text()
        )

    def test_authority_is_separate_with_only_reviewed_observations_and_job_verbs(self):
        objects = [
            o
            for path in (DIRECTORY / "rbac/runtime-grants").glob("*.yaml")
            for o in yaml.safe_load_all(path.read_text())
        ]
        roles = {o["metadata"]["namespace"]: o["rules"] for o in objects if o["kind"] == "Role"}
        jobs = next(r for r in roles["perf-platform"] if r["resources"] == ["jobs"])
        self.assertEqual(
            jobs,
            {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["create", "get", "watch"]},
        )
        secrets = next(r for r in roles["argo-workflows"] if r["resources"] == ["secrets"])
        self.assertEqual(secrets["resourceNames"], ["release-git-read"])
        self.assertEqual(secrets["verbs"], ["get"])
        cluster = next(o for o in objects if o["kind"] == "ClusterRole")
        self.assertEqual(
            cluster["rules"],
            [
                {
                    "apiGroups": [""],
                    "resources": ["namespaces"],
                    "resourceNames": ["kube-system"],
                    "verbs": ["get"],
                }
            ],
        )
        for binding in (o for o in objects if o["kind"].endswith("Binding")):
            self.assertEqual(
                binding["subjects"],
                [
                    {
                        "kind": "ServiceAccount",
                        "name": "release-runtime-grants",
                        "namespace": "argo-workflows",
                    }
                ],
            )
        app = list(
            yaml.safe_load_all(
                (
                    ROOT
                    / "gitops/release-workflows-bootstrap/runtime-grants-application-access.yaml"
                ).read_text()
            )
        )
        self.assertEqual(
            app[0]["rules"],
            [
                {
                    "apiGroups": ["argoproj.io"],
                    "resources": ["applications"],
                    "resourceNames": ["perfeng-local-root", "perfeng-local-control-plane"],
                    "verbs": ["get"],
                }
            ],
        )
        self.assertEqual(app[1]["subjects"][0]["name"], "release-runtime-grants")

    @unittest.skipUnless(BASH, "Bash required")
    def test_actual_worker_refuses_url_drift_hides_vendor_output_and_uses_owner_environment(self):
        assert BASH
        script = self.job["spec"]["template"]["spec"]["containers"][0]["args"][0].replace(
            "/tmp/grants.private", '"$PRIVATE"'
        )
        setup = r"""psql() {
 printf called > "$MARKER"
 [[ ${MIGRATION_URL+x} != x && ${PGOPTIONS+x} != x && ${PGHOSTADDR+x} != x && ${PGSERVICE+x} != x ]] || return 91
 [[ $PGDATABASE == perfeng && $PGUSER == perfeng_migration && $PGHOST == postgres.perf-platform.svc.cluster.local ]] || return 92
 [[ ${#PGPASSWORD} == 43 ]] || return 93
 printf '%s\n' 'private-vendor-output' >&2
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
            for label, url, code, expected in (
                ("valid", valid, 0, 0),
                ("sql-failure", valid, 2, 1),
                ("wrong-role", valid.replace("perfeng_migration", "postgres"), 0, 64),
                (
                    "wrong-host",
                    valid.replace("postgres.perf-platform", "other.perf-platform"),
                    0,
                    64,
                ),
                ("injected-options", valid + "&options=-crole=postgres", 0, 64),
                ("bad-password", valid.replace("s" * 43, "a/" + "s" * 41), 0, 64),
            ):
                marker = root / label
                process = subprocess.run(
                    [BASH, "-eu", "-c", setup + script],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "BASH_ENV": "",
                        "MIGRATION_URL": url,
                        "PRIVATE": (root / "private").as_posix(),
                        "MARKER": marker.as_posix(),
                        "PSQL_EXIT": str(code),
                        "PGOPTIONS": "unsafe",
                        "PGHOSTADDR": "unsafe",
                        "PGSERVICE": "unsafe",
                    },
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                with self.subTest(label=label):
                    self.assertEqual(process.returncode, expected, process.stderr)
                    self.assertEqual(marker.exists(), label in {"valid", "sql-failure"})
                    self.assertNotIn("private-vendor-output", process.stdout + process.stderr)
                    self.assertNotIn("s" * 43, process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
