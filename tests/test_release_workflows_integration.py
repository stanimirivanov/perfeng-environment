"""Keep the live CI fixture isolated from offline checks and retained environments."""

import ast
import base64
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SCRIPT = FIXTURE / "run.sh"
GIT_ROOT = Path(shutil.which("git") or ".").parent.parent
BASH = next(
    (
        str(path)
        for path in (
            GIT_ROOT / "bin/bash.exe",
            GIT_ROOT / "usr/bin/bash.exe",
            Path(shutil.which("bash") or "."),
        )
        if path.is_file()
    ),
    None,
)


class ReleaseWorkflowsIntegrationTests(unittest.TestCase):
    def test_ci_authority_and_artifact_scope_are_bounded(self):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-workflows-kind.yml").read_text()
        )
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(set(events), {"pull_request", "workflow_dispatch"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["maintenance-verifier"]
        self.assertEqual(job["permissions"], {"contents": "read", "packages": "read"})
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertLessEqual(job["timeout-minutes"], 30)
        self.assertIn("head.repo.full_name == github.repository", job["if"])
        checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout"))
        self.assertFalse(checkout["with"]["persist-credentials"])
        run = next(s for s in job["steps"] if s.get("run") == "make release-workflows-integration")
        self.assertEqual(run["env"], {"GITHUB_TOKEN": "${{ github.token }}"})
        upload = next(
            s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact")
        )
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/release-workflows-results/")
        self.assertLessEqual(upload["with"]["retention-days"], 7)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("validate: verify render-check supply-chain", makefile)
        self.assertIn(
            "release-workflows-integration:\n\tbash tests/integration/release-workflows/run.sh",
            makefile,
        )

    def test_fixture_selects_the_existing_restricted_child_and_pinned_tools(self):
        workflow = yaml.safe_load((FIXTURE / "workflow.yaml").read_text())
        child = yaml.safe_load(
            (ROOT / "gitops/release-workflows/verify-release-quiescence-v2.yaml").read_text()
        )
        self.assertEqual(workflow["spec"]["serviceAccountName"], "release-maintenance-checkpoint")
        self.assertEqual(workflow["spec"]["activeDeadlineSeconds"], 300)
        self.assertEqual(
            workflow["spec"]["templates"][0]["steps"][0][0]["templateRef"],
            {"name": child["metadata"]["name"], "template": "run"},
        )
        deployment = yaml.safe_load((FIXTURE / "deployment.yaml").read_text())
        self.assertEqual(deployment["spec"]["replicas"], 0)
        self.assertFalse(deployment["spec"]["template"]["spec"]["automountServiceAccountToken"])
        toolchain = json.loads((FIXTURE / "toolchain.json").read_text())
        self.assertEqual(set(toolchain), {"kind", "kubectl", "argo", "workflows"})
        self.assertEqual(toolchain["argo"]["version"], toolchain["workflows"]["version"])
        for tool in toolchain.values():
            self.assertIn(tool["version"], tool["url"])
            digest = tool.get("sha256") or (ROOT / tool["checksumFile"]).read_text().strip()
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertTrue(tool["url"].startswith(("https://github.com/", "https://dl.k8s.io/")))
        self.assertEqual(toolchain["workflows"]["version"], "v4.1.5")
        self.assertNotIn(b"\r", SCRIPT.read_bytes())
        cases = re.findall(r"^run_case (\S+) (success|failure) ", SCRIPT.read_text(), re.M)
        self.assertEqual(
            cases,
            [
                ("first", "success"),
                ("repeat", "success"),
                ("wrong-uid", "failure"),
                ("unpaused", "failure"),
                ("matching-pod", "failure"),
                ("missing-read-grant", "failure"),
                ("recovered", "success"),
            ],
        )

    def test_ledger_fixture_reuses_native_operations_without_adding_writes(self):
        workflow = yaml.safe_load((FIXTURE / "ledger-workflow.yaml").read_text())
        production = yaml.safe_load(
            (ROOT / "gitops/release-workflows/adoption-records.yaml").read_text()
        )
        operations = {t["name"]: t for t in production["spec"]["templates"]}
        self.assertEqual(workflow["spec"]["serviceAccountName"], "release-adoption")
        self.assertEqual(workflow["metadata"]["labels"], {"perfeng.io/fixture": "ledger"})
        self.assertLessEqual(workflow["spec"]["activeDeadlineSeconds"], 240)
        seen = set()
        for template in workflow["spec"]["templates"]:
            if template["name"] == "interruption-barrier":
                self.assertEqual(template, {"name": "interruption-barrier", "suspend": {}})
                continue
            self.assertEqual(set(template) - {"name", "steps", "outputs"}, set())
            for group in template["steps"]:
                for step in group:
                    if "template" in step:
                        self.assertEqual(template["name"], "receipt-interrupted")
                        self.assertIn(step["template"], {"receipt-create", "interruption-barrier"})
                        continue
                    ref = step["templateRef"]
                    self.assertEqual(ref["name"], production["metadata"]["name"])
                    operation = operations[ref["template"]]
                    self.assertIn(operation["resource"]["action"], {"get", "create"})
                    required = {
                        p["name"] for p in operation.get("inputs", {}).get("parameters", [])
                    }
                    supplied = {p["name"] for p in step.get("arguments", {}).get("parameters", [])}
                    self.assertEqual(required, supplied)
                    seen.add(ref["template"])
        self.assertEqual(seen, {"root", "receipt", "pointer", "create-receipt", "create-pointer"})
        for script in (
            "ledger.sh",
            "ledger-response.sh",
            "recovery.sh",
            "database.sh",
            "external-state-fixtures.sh",
            "postgres-chart.sh",
            "seaweedfs-chart.sh",
            "bucket.sh",
            "registry.sh",
            "readiness.sh",
            "observer.sh",
            "preflight.sh",
            "migration-image.sh",
            "runtime-grants.sh",
            "server.sh",
            "argocd.sh",
            "readiness-parent.sh",
        ):
            source = (FIXTURE / script).read_text()
            for index, python in enumerate(re.findall(r"<<'PY'\n(.*?)\nPY", source, re.S)):
                ast.parse(python, filename=f"{script} fixture Python {index}")
        self.assertIn('source "$source_dir/ledger.sh"', SCRIPT.read_text())
        source = (FIXTURE / "ledger.sh").read_text()
        self.assertIn('source "$source_dir/recovery.sh"', source)
        self.assertIn('a terminate "$interrupted"', source)
        self.assertIn("barrier_deadline=$((SECONDS + 120))", source)

    def test_database_fixture_uses_the_published_child_and_an_isolated_server(self):
        workflow = yaml.safe_load((FIXTURE / "database-workflow.yaml").read_text())
        production = yaml.safe_load(
            (ROOT / "gitops/release-workflows/prepare-database-roles.yaml").read_text()
        )
        spec = workflow["spec"]
        self.assertEqual(spec["serviceAccountName"], "release-external-state")
        self.assertLessEqual(spec["activeDeadlineSeconds"], 300)
        self.assertEqual(
            spec["templates"][0]["steps"],
            [
                [
                    {
                        "name": "roles",
                        "templateRef": {"name": production["metadata"]["name"], "template": "run"},
                    }
                ]
            ],
        )
        self.assertEqual(set(spec["templates"][0]), {"name", "steps", "outputs"})
        job = yaml.safe_load(production["spec"]["templates"][0]["resource"]["manifest"])
        pod = yaml.safe_load((FIXTURE / "postgres-pod.yaml").read_text())
        self.assertEqual(
            pod["spec"]["containers"][0]["image"],
            job["spec"]["template"]["spec"]["containers"][0]["image"],
        )
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertTrue(pod["spec"]["securityContext"]["runAsNonRoot"])
        self.assertTrue(pod["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"])
        self.assertTrue(all("emptyDir" in v or "secret" in v for v in pod["spec"]["volumes"]))
        service = yaml.safe_load((FIXTURE / "postgres-service.yaml").read_text())
        self.assertEqual(service["metadata"], {"name": "postgres", "namespace": "perf-platform"})
        self.assertEqual(service["spec"]["selector"], pod["metadata"]["labels"])
        self.assertIn('source "$source_dir/database.sh"', SCRIPT.read_text())

    def test_database_credentials_are_private_generated_secret_inputs(self):
        source = (FIXTURE / "external-state-fixtures.sh").read_text()
        code = re.findall(r"<<'PY'\n(.*?)\nPY", source, re.S)[0]
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-", temporary],
                input=code,
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            directory = Path(temporary)
            secrets = json.loads((directory / "database-secrets.json").read_text())["items"]
            self.assertEqual(
                {s["metadata"]["name"] for s in secrets},
                {"perfeng-postgres-auth", "perfeng-control-plane-database"},
            )
            for secret in secrets:
                self.assertTrue(secret["immutable"])
                self.assertEqual(secret["metadata"]["namespace"], "perf-platform")
                self.assertEqual(
                    secret["metadata"]["labels"]["app.kubernetes.io/managed-by"],
                    "perfeng-environment",
                )
            urls = secrets[1]["data"]
            for key, role in (
                ("migration-url", "perfeng_migration"),
                ("runtime-url", "perfeng_runtime"),
            ):
                value = base64.b64decode(urls[key]).decode()
                self.assertRegex(
                    value,
                    rf"^postgresql://{role}:[A-Za-z0-9_-]{{43}}@postgres\.perf-platform\.svc\.cluster\.local:5432/perfeng\?sslmode=disable$",
                )
            runtime = (
                base64.b64decode(urls["runtime-url"]).decode().split(":", 2)[2].split("@", 1)[0]
            )
            restore = (directory / "database-restore-password.sql").read_text()
            self.assertIn(runtime, restore)
            self.assertNotIn(runtime, (directory / "database-break-password.sql").read_text())

    def test_bucket_fixture_reuses_the_native_child_and_pinned_s3_images(self):
        workflow = yaml.safe_load((FIXTURE / "bucket-workflow.yaml").read_text())
        production = yaml.safe_load(
            (ROOT / "gitops/release-workflows/prepare-bucket.yaml").read_text()
        )
        spec = workflow["spec"]
        self.assertEqual(spec["serviceAccountName"], "release-external-state")
        self.assertLessEqual(spec["activeDeadlineSeconds"], 240)
        self.assertEqual(
            spec["templates"][0]["steps"][0][0]["templateRef"],
            {"name": production["metadata"]["name"], "template": "run"},
        )
        self.assertEqual(set(spec["templates"][0]), {"name", "steps", "outputs"})
        job = yaml.safe_load(production["spec"]["templates"][0]["resource"]["manifest"])
        server = yaml.safe_load((FIXTURE / "seaweedfs-pod.yaml").read_text())
        observer = yaml.safe_load((FIXTURE / "bucket-observer.yaml").read_text())
        values = yaml.safe_load((ROOT / "charts/seaweedfs/values.yaml").read_text())
        self.assertEqual(server["spec"]["containers"][0]["image"], values["image"])
        self.assertEqual(
            observer["spec"]["containers"][0]["image"],
            job["spec"]["template"]["spec"]["containers"][0]["image"],
        )
        for pod in (server, observer):
            self.assertFalse(pod["spec"]["automountServiceAccountToken"])
            self.assertTrue(pod["spec"]["securityContext"]["runAsNonRoot"])
            self.assertTrue(
                pod["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"]
            )
            self.assertTrue(
                all("emptyDir" in volume or "secret" in volume for volume in pod["spec"]["volumes"])
            )
        service = yaml.safe_load((FIXTURE / "seaweedfs-service.yaml").read_text())
        self.assertEqual(service["metadata"], {"name": "seaweedfs", "namespace": "perf-platform"})
        self.assertEqual(service["spec"]["selector"], server["metadata"]["labels"])
        self.assertEqual(service["spec"]["ports"][0]["port"], 8333)
        self.assertIn('source "$source_dir/bucket.sh"', SCRIPT.read_text())

    def test_bucket_fixture_capacity_covers_metadata_and_artifact_volume_growth(self):
        fixture = yaml.safe_load((FIXTURE / "seaweedfs-pod.yaml").read_text())
        command = fixture["spec"]["containers"][0]["args"][0]
        production = (ROOT / "charts/seaweedfs/templates/statefulset.yaml").read_text()
        capacity = re.search(r"-volume\.max=(\d+)", command)
        production_capacity = re.search(r"-volume\.max=(\d+)", production)
        self.assertIsNotNone(capacity)
        self.assertIsNotNone(production_capacity)
        assert capacity is not None and production_capacity is not None
        # Pinned SeaweedFS 4.45 grows seven single-copy volumes per collection.
        self.assertGreaterEqual(int(capacity[1]), 2 * 7)
        self.assertEqual(capacity[1], production_capacity[1])

    @unittest.skipUnless(BASH, "Bash is unavailable; Linux CI checks the live fixture shell")
    def test_bucket_allocation_diagnostics_exclude_raw_server_log_values(self):
        source = (FIXTURE / "external-state-fixtures.sh").read_text()
        function = re.search(r"^bucket_allocation_diagnostics\(\) \{\n.*?^\}", source, re.M | re.S)
        assert function is not None and BASH is not None
        with tempfile.TemporaryDirectory() as temporary:
            code = """set -euo pipefail
results=$1
k() { printf '%s\\n' 'private-key-do-not-publish: No writable volumes for secret-collection' 'create 7 volume, created 0: No matching data node found' 'unrelated-private-log'; }
"""
            code += function[0] + "\nbucket_allocation_diagnostics\n"
            result = subprocess.run(
                [BASH, "-s", "--", Path(temporary).as_posix()],
                input=code,
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            report = (Path(temporary) / "bucket-allocation.txt").read_text()
            self.assertEqual(
                set(report.splitlines()),
                {
                    "No writable volumes",
                    "No matching data node found",
                    "create 7 volume, created 0",
                },
            )

    def test_bucket_profiles_match_private_client_secrets_and_only_approved_server_identity(self):
        source = (FIXTURE / "external-state-fixtures.sh").read_text()
        code = re.findall(r"<<'PY'\n(.*?)\nPY", source, re.S)[1]
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-", temporary],
                input=code,
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            directory = Path(temporary)
            documents = {
                label: json.loads((directory / f"bucket-{label}.json").read_text())
                for label in ("server-auth", "client-approved", "client-denied")
            }
            for document in documents.values():
                self.assertTrue(document["immutable"])
                self.assertEqual(document["metadata"]["namespace"], "perf-platform")
                self.assertEqual(
                    document["metadata"]["labels"]["app.kubernetes.io/managed-by"],
                    "perfeng-environment",
                )
            server_data = documents["server-auth"]["data"]
            profiles = configparser.ConfigParser()
            profiles.read_string(base64.b64decode(server_data["credentials"]).decode())
            credentials = []
            for label, profile in (("approved", "default"), ("denied", "denied")):
                client = documents[f"client-{label}"]
                self.assertEqual(client["metadata"]["name"], "perfeng-s3-auth")
                data = {
                    key: base64.b64decode(value).decode() for key, value in client["data"].items()
                }
                self.assertRegex(data["access-key"], r"^[0-9a-f]{32}$")
                self.assertRegex(data["secret-key"], r"^[0-9a-f]{64}$")
                self.assertEqual(profiles[profile]["aws_access_key_id"], data["access-key"])
                self.assertEqual(profiles[profile]["aws_secret_access_key"], data["secret-key"])
                credentials.append(
                    {"accessKey": data["access-key"], "secretKey": data["secret-key"]}
                )
                self.assertEqual(
                    json.loads(data["s3.json"])["identities"][0]["credentials"], [credentials[-1]]
                )
            identities = json.loads(base64.b64decode(server_data["s3.json"]))["identities"]
            self.assertEqual(len(identities), 1)
            self.assertEqual(identities[0]["credentials"], [credentials[0]])
            self.assertEqual(identities[0]["actions"], ["Admin", "Read", "Write", "List"])
            self.assertNotEqual(credentials[0], credentials[1])

    def test_registry_fixture_reuses_the_child_with_private_credential_lifecycle(self):
        workflow = yaml.safe_load((FIXTURE / "registry-workflow.yaml").read_text())
        production = yaml.safe_load(
            (ROOT / "gitops/release-workflows/prepare-execution-registry.yaml").read_text()
        )
        spec = workflow["spec"]
        self.assertEqual(spec["serviceAccountName"], "release-external-state")
        self.assertLessEqual(spec["activeDeadlineSeconds"], 300)
        self.assertEqual(
            spec["templates"][0]["steps"][0][0]["templateRef"],
            {"name": production["metadata"]["name"], "template": "run"},
        )
        self.assertEqual(set(spec["templates"][0]), {"name", "steps", "outputs"})
        source = SCRIPT.read_text()
        self.assertLess(source.index("registry_credentials\n"), source.index("unset GITHUB_TOKEN"))
        self.assertLess(
            source.index('source "$source_dir/bucket.sh"'), source.index("registry_cases\n")
        )
        self.assertIn(
            '"$fixture/registry-approved.secret.json"',
            source[source.index("finish()") : source.index("trap finish EXIT")],
        )

    def test_registry_credentials_use_only_the_private_token_and_actions_actor(self):
        source = (FIXTURE / "registry.sh").read_text()
        code = re.findall(r"<<'PY'\n(.*?)\nPY", source, re.S)[0]
        token, actor = "fixture-ephemeral-token-no-real-authority", "fixture-ci-actor"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "token").write_text(token)
            result = subprocess.run(
                [sys.executable, "-", temporary],
                input=code,
                text=True,
                capture_output=True,
                timeout=20,
                cwd=ROOT,
                env={**os.environ, "GITHUB_ACTOR": actor},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout + result.stderr, "")
            passwords = []
            for label in ("approved", "denied"):
                value = json.loads((directory / f"registry-{label}.secret.json").read_text())
                self.assertTrue(value["immutable"])
                self.assertEqual(value["type"], "kubernetes.io/dockerconfigjson")
                self.assertEqual(
                    value["metadata"],
                    {
                        "name": "perfeng-execution-registry",
                        "namespace": "perf-generators",
                        "labels": {
                            "app.kubernetes.io/managed-by": "perfeng-environment",
                            "perfeng.io/purpose": "execution-image-pull",
                        },
                    },
                )
                config = json.loads(base64.b64decode(value["data"][".dockerconfigjson"]))
                self.assertEqual(set(config["auths"]), {"ghcr.io"})
                credential = config["auths"]["ghcr.io"]
                self.assertEqual(credential["username"], actor)
                self.assertEqual(
                    base64.b64decode(credential["auth"]).decode(),
                    actor + ":" + credential["password"],
                )
                passwords.append(credential["password"])
            self.assertEqual(passwords[0], token)
            self.assertNotEqual(passwords[1], token)
            expected = json.loads((directory / "registry-expected.json").read_text())
            self.assertEqual(set(expected), {"k6", "kube-burner", "analysis"})
            self.assertTrue(all(re.fullmatch(r"sha256:[0-9a-f]{64}", d) for d in expected.values()))
            (directory / "token").write_text("private-token-with-newline\n")
            refused = subprocess.run(
                [sys.executable, "-", temporary],
                input=code,
                text=True,
                capture_output=True,
                timeout=20,
                cwd=ROOT,
                env={**os.environ, "GITHUB_ACTOR": actor},
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertNotIn("private-token-with-newline", refused.stdout + refused.stderr)

    def test_registry_diagnostics_classify_errors_without_publishing_context(self):
        source = (FIXTURE / "registry.sh").read_text()
        code = re.findall(r"<<'PY'\n(.*?)\nPY", source, re.S)[1]
        cases = {
            "DENIED: installation not allowed to Read organization package": "authorization",
            "denied: permission_denied: read_package": "authorization",
            "unexpected status code 403": "authorization",
            "MANIFEST_UNKNOWN: missing": "manifest-missing",
            "x509: certificate signed by unknown authority": "tls",
            "dial tcp: lookup ghcr.io: no such host": "network",
            'unknown command "digest"': "command",
            "unexpected registry failure": "unclassified",
            "sha256:" + "a" * 64: "unclassified",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "worker.log"
            for message, expected in cases.items():
                with self.subTest(message=message):
                    path.write_text("https://private-token@example.test/private-path " + message)
                    result = subprocess.run(
                        [sys.executable, "-", str(path)],
                        input=code,
                        text=True,
                        capture_output=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected + "\n")
                    self.assertEqual(result.stderr, "")
        # Unknown or infrastructure errors must never satisfy the negative auth case.
        self.assertIn(
            'grep -qx authorization "$results/registry-$registry_label-kube-burner.signals.txt"',
            source,
        )

    def test_readiness_fixture_reuses_the_published_child_and_pinned_runtime(self):
        workflow = yaml.safe_load((FIXTURE / "readiness-workflow.yaml").read_text())
        child = yaml.safe_load(
            (ROOT / "gitops/release-workflows/verify-release-readiness-v2.yaml").read_text()
        )
        template = child["spec"]["templates"][0]
        spec = workflow["spec"]
        self.assertEqual(spec["serviceAccountName"], "release-readiness")
        self.assertEqual(spec["activeDeadlineSeconds"], 300)
        step = spec["templates"][0]["steps"][0][0]
        self.assertEqual(
            step["templateRef"], {"name": child["metadata"]["name"], "template": "run"}
        )
        required = {p["name"] for p in template["inputs"]["parameters"]}
        self.assertEqual({p["name"] for p in step["arguments"]["parameters"]}, required)
        self.assertEqual(
            {p["name"] for p in spec["arguments"]["parameters"]}, required | {"selection-revision"}
        )
        pod = yaml.safe_load((FIXTURE / "readiness-pod.yaml").read_text())
        self.assertEqual(pod["spec"]["containers"][0]["image"], template["container"]["image"])
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertTrue(pod["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"])
        service = yaml.safe_load((FIXTURE / "readiness-service.yaml").read_text())
        self.assertEqual(service["metadata"]["name"], "control-plane")
        self.assertEqual(service["metadata"]["namespace"], "perf-platform")
        self.assertEqual(service["spec"]["selector"], pod["metadata"]["labels"])
        self.assertEqual(service["spec"]["ports"][0]["port"], 8080)
        self.assertIn('source "$source_dir/readiness.sh"', SCRIPT.read_text())

    def test_observer_fixture_reuses_every_native_operation_without_copying_resources(self):
        workflow = yaml.safe_load((FIXTURE / "observer-workflow.yaml").read_text())
        child = yaml.safe_load(
            (ROOT / "gitops/release-workflows/observe-release-readiness.yaml").read_text()
        )
        operations = {t["name"]: t for t in child["spec"]["templates"]}
        spec = workflow["spec"]
        self.assertEqual(spec["serviceAccountName"], "release-readiness")
        self.assertEqual(spec["activeDeadlineSeconds"], 600)
        seen = []
        for template in spec["templates"]:
            self.assertEqual(set(template), {"name", "steps", "outputs"})
            for group in template["steps"]:
                for step in group:
                    ref = step["templateRef"]
                    self.assertEqual(ref["name"], child["metadata"]["name"])
                    operation = operations[ref["template"]]
                    self.assertEqual(operation["resource"]["action"], "get")
                    required = {
                        p["name"] for p in operation.get("inputs", {}).get("parameters", [])
                    }
                    arguments = step.get("arguments", {}).get("parameters", [])
                    self.assertEqual({p["name"] for p in arguments}, required)
                    seen.append((step["name"], ref["template"]))
        self.assertEqual(
            seen,
            [
                ("postgres", "statefulset"),
                ("seaweedfs", "statefulset"),
                ("application", "application"),
                ("deployment", "deployment"),
                ("service", "service"),
                ("application", "application"),
                ("deployment", "deployment"),
                ("postgres", "statefulset"),
                ("seaweedfs", "statefulset"),
            ],
        )
        source = SCRIPT.read_text()
        self.assertLess(
            source.index('source "$source_dir/readiness.sh"'),
            source.index('source "$source_dir/observer.sh"'),
        )

    def test_observer_statefulset_adapter_keeps_synthetic_workloads_distinct_and_pinned(self):
        code = re.findall(r"<<'PY'\n(.*?)\nPY", (FIXTURE / "observer.sh").read_text(), re.S)[0]
        expected = yaml.safe_load((FIXTURE / "deployment.yaml").read_text())["spec"]["template"][
            "spec"
        ]["containers"][0]["image"]
        for name in ("postgres", "seaweedfs", "foreign"):
            result = subprocess.run(
                [sys.executable, "-", str(FIXTURE / "observer-statefulset.yaml"), name],
                input=code,
                text=True,
                capture_output=True,
                timeout=20,
            )
            if name == "foreign":
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                continue
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["metadata"]["name"], name)
            self.assertEqual(value["spec"]["serviceName"], name)
            pod = value["spec"]["template"]
            self.assertEqual(pod["metadata"]["labels"], {"perfeng.io/observer-workload": name})
            self.assertEqual(value["spec"]["selector"]["matchLabels"], pod["metadata"]["labels"])
            self.assertEqual(pod["spec"]["containers"][0]["image"], expected)
            self.assertFalse(pod["spec"]["automountServiceAccountToken"])
            self.assertTrue(pod["spec"]["securityContext"]["runAsNonRoot"])
            self.assertTrue(
                pod["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"]
            )
            self.assertNotIn("volumeClaimTemplates", value["spec"])

    @unittest.skipUnless(BASH, "Bash is unavailable; Linux CI checks pending-condition evidence")
    def test_observer_pending_gate_requires_an_evaluated_false_native_condition(self):
        source = (FIXTURE / "observer.sh").read_text()
        function = re.search(r"^observer_pending_seen\(\) \{\n.*?^\}", source, re.M | re.S)
        assert function is not None and BASH is not None
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "resource.log"
            for log, expected in (
                (
                    'msg="success condition evaluated" condition="spec.replicas = 1" matched=false',
                    0,
                ),
                ('{"matched":false,"msg":"success condition evaluated"}', 0),
                ('msg="success condition evaluated" matched=true', 1),
                ('msg="Waiting for conditions" unrelated=false', 1),
                ('msg="other event" matched=false', 1),
            ):
                with self.subTest(log=log):
                    path.write_text(log)
                    result = subprocess.run(
                        [BASH, "-s", "--", path.as_posix()],
                        input=function[0] + '\nobserver_pending_seen "$1"\n',
                        text=True,
                        capture_output=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)

    def test_preflight_wrapper_uses_unchanged_children_with_explicit_synthetic_uid(self):
        workflow = yaml.safe_load((FIXTURE / "preflight-workflow.yaml").read_text())
        production = yaml.safe_load(
            (ROOT / "gitops/release-workflows/release-preflight-v2.yaml").read_text()
        )
        self.assertEqual(
            workflow["spec"]["serviceAccountName"], production["spec"]["serviceAccountName"]
        )
        self.assertEqual(workflow["spec"]["activeDeadlineSeconds"], 600)
        self.assertEqual(
            workflow["spec"]["arguments"],
            {
                "parameters": [
                    {"name": p["name"]} for p in production["spec"]["arguments"]["parameters"]
                ]
            },
        )
        steps = workflow["spec"]["templates"][0]["steps"]
        self.assertEqual(
            [g[0]["name"] for g in steps], ["validate-schema", "cluster-uid", "verify-identities"]
        )
        for group in steps:
            step = group[0]
            self.assertEqual(
                step["templateRef"],
                {"name": production["metadata"]["name"], "template": step["name"]},
            )
        self.assertEqual(
            steps[2][0]["arguments"]["parameters"],
            [{"name": "observed-cluster-uid", "value": "00000000-0000-0000-0000-000000000000"}],
        )
        # The production parent must continue binding the verifier to the native UID.
        self.assertEqual(
            production["spec"]["templates"][0]["steps"][2][0]["arguments"]["parameters"][0][
                "value"
            ],
            "{{steps.cluster-uid.outputs.parameters.uid}}",
        )
        self.assertIn('source "$source_dir/preflight.sh"', SCRIPT.read_text())

    def test_preflight_ci_request_and_parameter_variants_preserve_exact_bytes(self):
        from jsonschema import Draft202012Validator

        path = ROOT / "deployments/release-requests/ci-preflight-fixture-v1.json"
        raw = path.read_bytes()
        self.assertNotIn(b"\r", raw)
        request = json.loads(raw)
        Draft202012Validator(
            json.loads((ROOT / "deployments/release-request-v1.schema.json").read_text())
        ).validate(request)
        self.assertEqual(
            request["environment"]["clusterUID"], "00000000-0000-0000-0000-000000000000"
        )
        self.assertEqual(
            request["selection"]["revision"], "4133113e481de32eae314c904778c6148790b80c"
        )
        code = re.findall(r"<<'PY'\n(.*?)\nPY", (FIXTURE / "preflight.sh").read_text(), re.S)[0]
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-", temporary, "f" * 40, "actual-fixture-cluster"],
                input=code,
                text=True,
                capture_output=True,
                timeout=20,
                cwd=ROOT,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout + result.stderr, "")
            directory = Path(temporary)
            original = json.loads((directory / "preflight-parameters.json").read_text())
            self.assertEqual(
                original["request-sha256"], "sha256:" + hashlib.sha256(raw).hexdigest()
            )
            self.assertEqual(original["selection-revision"], request["selection"]["revision"])
            self.assertNotEqual(original["request-revision"], original["selection-revision"])
            self.assertEqual(
                json.loads((directory / "preflight-wrong-digest.json").read_text()),
                {**original, "request-sha256": "sha256:" + "0" * 64},
            )
            self.assertEqual(
                json.loads((directory / "preflight-unsafe-path.json").read_text()),
                {
                    **original,
                    "request-path": "deployments/release-requests/../local-control-plane.json",
                },
            )
            refused = subprocess.run(
                [sys.executable, "-", temporary, "f" * 40, request["environment"]["clusterUID"]],
                input=code,
                text=True,
                capture_output=True,
                timeout=20,
                cwd=ROOT,
            )
            self.assertNotEqual(refused.returncode, 0)

    @unittest.skipUnless(BASH, "Bash unavailable; Linux CI executes the schema path guard")
    def test_preflight_schema_guard_refuses_unsafe_names_before_file_access(self):
        production = yaml.safe_load(
            (ROOT / "gitops/release-workflows/release-preflight-v2.yaml").read_text()
        )
        script = next(t for t in production["spec"]["templates"] if t["name"] == "validate-schema")[
            "container"
        ]["args"][0]
        assert BASH is not None
        for path, message in (
            ("/tmp/request.json", "Invalid request path"),
            ("deployments/release-requests/../local-control-plane.json", "Invalid request name"),
        ):
            with tempfile.TemporaryDirectory() as temporary:
                result = subprocess.run(
                    [BASH, "-ec", script],
                    env={**os.environ, "REQUEST_PATH": path},
                    cwd=temporary,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, message + "\n")
                self.assertEqual(list(Path(temporary).iterdir()), [])

    @unittest.skipIf(
        os.name == "nt" and shutil.which("jq") is None,
        "jq unavailable on Windows; Linux CI checks the native predicate",
    )
    def test_readiness_refusal_allows_unresolved_declaration_but_rejects_any_result_value(self):
        jq = shutil.which("jq")
        self.assertIsNotNone(jq, "Linux CI requires jq for its native Workflow status checks")
        assert jq is not None
        source = (FIXTURE / "readiness.sh").read_text()
        predicate = next(
            expression
            for expression in re.findall(r"jq -e '([^']+)'", source)
            if ".status.nodes" in expression and ".outputs.parameters" in expression
        )
        fixture = json.loads((ROOT / "tests/fixtures/readiness-refused-workflow.json").read_text())
        variants = [(fixture, 0)]
        for node in ("readiness-ci-refused", "readiness-ci-refused-child"):
            for value in (None, "", "{}", '{"status":"readiness-verified"}'):
                changed = json.loads(json.dumps(fixture))
                changed["status"]["nodes"][node]["outputs"] = {
                    "parameters": [{"name": "result", "value": value}]
                }
                variants.append((changed, 1))
        for workflow, expected in variants:
            with self.subTest(workflow=workflow):
                result = subprocess.run(
                    [jq, "-e", predicate],
                    input=json.dumps(workflow),
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, expected, result.stderr)

    @unittest.skipUnless(BASH, "Bash is unavailable; Linux CI checks the live fixture shell")
    def test_shell_syntax_and_unsupported_runners_refuse_before_side_effects(self):
        assert BASH is not None
        for script in (
            SCRIPT,
            FIXTURE / "ledger.sh",
            FIXTURE / "recovery.sh",
            FIXTURE / "database.sh",
            FIXTURE / "external-state-fixtures.sh",
            FIXTURE / "postgres-chart.sh",
            FIXTURE / "seaweedfs-chart.sh",
            FIXTURE / "bucket.sh",
            FIXTURE / "registry.sh",
            FIXTURE / "readiness.sh",
            FIXTURE / "observer.sh",
            FIXTURE / "preflight.sh",
            FIXTURE / "preflight-parent.sh",
            FIXTURE / "maintenance-parent.sh",
            FIXTURE / "external-state-parent.sh",
            FIXTURE / "migration-image.sh",
            FIXTURE / "runtime-grants.sh",
            FIXTURE / "server.sh",
            FIXTURE / "argocd.sh",
            FIXTURE / "root.sh",
            FIXTURE / "adoption-parent.sh",
            FIXTURE / "readiness-parent.sh",
        ):
            syntax = subprocess.run(
                [BASH, "-n", script.as_posix()], capture_output=True, timeout=20
            )
            self.assertEqual(syntax.returncode, 0, syntax.stderr)
            self.assertNotIn(b"\r", script.read_bytes())
        for script in (
            "ledger.sh",
            "recovery.sh",
            "database.sh",
            "external-state-fixtures.sh",
            "postgres-chart.sh",
            "seaweedfs-chart.sh",
            "bucket.sh",
            "registry.sh",
            "readiness.sh",
            "observer.sh",
            "preflight.sh",
        ):
            standalone = subprocess.run(
                [BASH, (FIXTURE / script).as_posix()], capture_output=True, timeout=20
            )
            self.assertEqual(standalone.returncode, 2, standalone.stderr)
        for authority in (
            {},
            {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_OS": "Linux"},
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Windows",
            },
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                "GITHUB_TOKEN": "fixture-token-must-not-be-logged",
                "DOCKER_HOST": "tcp://retained.example:2376",
            },
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                "GITHUB_TOKEN": "fixture-token-must-not-be-logged",
                "DOCKER_CONTEXT": "retained-context",
            },
        ):
            with self.subTest(authority=authority), tempfile.TemporaryDirectory() as temp:
                environment = dict(os.environ)
                for key in (
                    "GITHUB_ACTIONS",
                    "RUNNER_ENVIRONMENT",
                    "RUNNER_OS",
                    "RUNNER_TEMP",
                    "GITHUB_TOKEN",
                    "DOCKER_HOST",
                    "DOCKER_CONTEXT",
                ):
                    environment.pop(key, None)
                environment.update(authority)
                environment["RUNNER_TEMP"] = Path(temp).as_posix()
                result = subprocess.run(
                    [BASH, SCRIPT.as_posix()],
                    cwd=temp,
                    env=environment,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(b"Refusing", result.stderr)
                self.assertNotIn(b"fixture-token-must-not-be-logged", result.stderr + result.stdout)
                self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
