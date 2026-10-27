"""Exercise exact dry-run payloads and actual kubectl authorization requests."""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import test_release_maintenance_pause as guards
from test_release_workflows_integration import BASH, FIXTURE

SPEC = importlib.util.spec_from_file_location(
    "maintenance_authority_evidence", FIXTURE / "maintenance_authority_evidence.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
KUBECTL = shutil.which("kubectl")


def forbidden(
    verb="patch",
    resource="deployments",
    group="apps",
    namespace="perf-platform",
    name="ci-maintenance-denied",
    named=True,
):
    qualified = resource.split("/")[0] + ("." + group if group else "")
    target = f'{qualified} "{name}"' if named else qualified
    return f'{target} is forbidden: User "{MODULE.SUBJECT}" cannot {verb} resource "{resource}" in API group "{group}" in the namespace "{namespace}"'


class MaintenanceAuthorityTests(unittest.TestCase):
    def test_read_only_refresh_requires_demonstrated_stale_version_and_same_identity(self):
        fixtures = guards.MaintenancePauseTests()
        fixtures.setUp()
        message = (
            "The request is invalid: the server rejected our request due to an error in our request"
        )
        for original in fixtures.resources.values():
            current = deepcopy(original)
            current["metadata"]["resourceVersion"] = "fresh"
            MODULE.refresh(original, current, 1, message)
            for change in (
                "version",
                "uid",
                "spec",
                "owner",
                "label",
                "code",
                "forbidden",
                "transport",
            ):
                fresh = deepcopy(current)
                if change == "version":
                    fresh["metadata"]["resourceVersion"] = original["metadata"]["resourceVersion"]
                elif change == "uid":
                    fresh["metadata"]["uid"] = "replaced"
                elif change == "spec":
                    fresh["spec"]["different"] = True
                elif change == "owner":
                    fresh["metadata"]["ownerReferences"] = [{"uid": "other"}]
                elif change == "label":
                    fresh["metadata"].setdefault("labels", {})["other"] = "owner"
                with self.subTest(change=change), self.assertRaises(AssertionError):
                    MODULE.refresh(
                        original,
                        fresh,
                        0 if change == "code" else 1,
                        "Forbidden"
                        if change == "forbidden"
                        else "TLS timeout"
                        if change == "transport"
                        else message,
                    )

    def test_plan_covers_named_and_namespace_boundaries_without_secret_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            receipt = "receipt." + "a" * 32 + "." + "b" * 32
            records = [
                {"metadata": {"name": receipt}, "immutable": True},
                {"metadata": {"name": "perfeng-local-current"}},
            ]
            (directory / "maintenance-before.records.json").write_text(json.dumps(records))
            rows = MODULE.plan(directory)
            self.assertEqual(len(rows), 20)
            self.assertEqual(len({r[0] for r in rows}), len(rows))
            self.assertTrue(
                {
                    "other-app",
                    "app-namespace",
                    "other-deployment",
                    "deployment-namespace",
                    "postgres-patch",
                    "seaweedfs-patch",
                    "service-patch",
                    "git-secret-patch",
                    "app-create",
                    "deployment-create",
                    "receipt-delete",
                    "pointer-patch",
                }
                <= {r[0] for r in rows}
            )
            for label, verb, plural, group, namespace, name, source in rows:
                self.assertIn(verb, {"patch", "delete", "create"})
                self.assertTrue(group)
                if source == "handle":
                    body = json.loads(
                        (directory / f"maintenance-authority-{label}.handle.json").read_text()
                    )
                    self.assertEqual(body["metadata"], {"name": name, "namespace": namespace})
                    self.assertEqual(set(body), {"apiVersion", "kind", "metadata"})
                else:
                    self.assertIn(source, {"configmap", "secret", "job"})
                self.assertIn(
                    plural,
                    {
                        "applications",
                        "deployments",
                        "statefulsets",
                        "services",
                        "secrets",
                        "configmaps",
                        "jobs",
                    },
                )

    @unittest.skipUnless(BASH, "Bash required for dry-run and failure-order protocol")
    def test_shell_keeps_every_mutation_dry_run_and_blocks_any_failed_gate(self):
        assert BASH
        shell = r"""
set -euo pipefail
fixture=$1 results=$1/results source_dir=$2 mode=preflight-parent scenario=initial
KUBECONFIG=$1/kubeconfig cluster_uid=owned current_stage=maintenance-pause CASE=$3 positive_calls=0
GITHUB_ACTIONS=true RUNNER_ENVIRONMENT=github-hosted RUNNER_OS=Linux
mkdir -p "$results"; touch "$fixture/owned"
start_stage() { current_stage=$1; }
complete_stage() { :; }
maintenance_snapshot() { :; }; root_state() { :; }; root_retained() { :; }
cmp() { [[ $CASE != retention ]]; }
k() {
  printf '%s\0' "$@" >> "$fixture/calls"; printf '\n' >> "$fixture/calls"
  if [[ $* == *'get namespace kube-system'* ]]; then
    if [[ $CASE == foreign ]]; then echo foreign; else echo owned; fi; return 0
  fi
  if [[ $* == *'auth can-i'* ]]; then
    if [[ $CASE == exec ]]; then echo yes; return 0; else echo no; return 1; fi
  fi
  if [[ $* == *'--type=json'* ]]; then
    positive_calls=$((positive_calls + 1))
    if [[ $CASE == stale-all || ( $CASE == stale-first && $positive_calls == 1 ) ]]; then return 1; fi
    [[ $CASE != positive ]] || return 1
    echo '{}'; return 0
  fi
  if [[ $* == *'--as='* ]]; then
    echo private-sentinel >&2
    [[ $CASE == overprivileged ]] && return 0
    return 1
  fi
  echo '{}'
}
uv() {
  case "$*" in
    *' plan '*) printf 'other-app\tpatch\tapplications\targoproj.io\targocd\tdenied\thandle\nroot-delete\tdelete\tapplications\targoproj.io\targocd\troot\thandle\njob-create\tcreate\tjobs\tbatch\tperf-platform\tci-adoption-denied\tjob\n' ;;
    *' allowed '*) [[ ${@: -1} == 0 ]] ;;
    *' refresh '*) [[ $CASE == stale-first || $CASE == stale-all ]] ;;
    *' denied '*) [[ ${@: -1} == 1 && $CASE != mismatch ]] ;;
  esac
}
source "$2/maintenance-authority.sh"
echo continued
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = directory / "authority.sh"
            script.write_text(shell, encoding="utf-8", newline="\n")
            for case in (
                "success",
                "stale-first",
                "stale-all",
                "positive",
                "mismatch",
                "overprivileged",
                "exec",
                "retention",
                "foreign",
            ):
                fixture = directory / case
                fixture.mkdir()
                result = subprocess.run(
                    [BASH, script.as_posix(), fixture.as_posix(), FIXTURE.as_posix(), case],
                    env={**os.environ, "BASH_ENV": ""},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                with self.subTest(case=case):
                    self.assertEqual(
                        result.returncode == 0, case in {"success", "stale-first"}, result.stderr
                    )
                    self.assertNotIn("private-sentinel", result.stdout + result.stderr)
                    report = fixture / "results/maintenance-authority.tsv"
                    self.assertEqual(
                        report.exists() and "suite\tverified" in report.read_text(),
                        case in {"success", "stale-first"},
                    )
                    calls = [
                        line.split(b"\0")[:-1]
                        for line in (fixture / "calls").read_bytes().splitlines()
                    ]
                    for command in calls:
                        if (
                            any(v in command for v in (b"patch", b"delete", b"create"))
                            and b"auth" not in command
                        ):
                            self.assertIn(b"--dry-run=server", command)
                            self.assertIn(b"--as=" + MODULE.SUBJECT.encode(), command)
                            self.assertIn(b"--as-group=system:serviceaccounts", command)
                            self.assertIn(
                                b"--as-group=system:serviceaccounts:argo-workflows", command
                            )
                            self.assertIn(b"--as-group=system:authenticated", command)
                            self.assertIn(b"-f", command)
                    if case == "foreign":
                        self.assertEqual(len(calls), 1)
                    if case in {"stale-all", "positive"}:
                        self.assertEqual(
                            sum(b"--type=json" in call for call in calls),
                            3 if case == "stale-all" else 1,
                        )

    @unittest.skipUnless(KUBECTL, "kubectl required for published native JSON Patch replay")
    def test_exact_published_patches_and_positive_response_validation(self):
        assert KUBECTL
        fixture = guards.MaintenancePauseTests()
        fixture.setUp()
        with tempfile.TemporaryDirectory() as temporary:
            obj, payload = Path(temporary) / "object.json", Path(temporary) / "patch.json"
            for resource, original in fixture.resources.items():
                document, expected = MODULE.prepare(resource, original)
                obj.write_text(json.dumps(original))
                payload.write_text(json.dumps(document))
                result = subprocess.run(
                    [
                        KUBECTL,
                        "patch",
                        "--local",
                        "--type=json",
                        "-f",
                        str(obj),
                        "--patch-file",
                        str(payload),
                        "-o",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                response = json.loads(result.stdout)
                self.assertEqual(response["spec"], expected)
                MODULE.allowed(resource, original, response, 0)
                for change in ("spec", "uid", "annotation", "error"):
                    bad = deepcopy(response)
                    if change == "spec":
                        bad["spec"]["extra"] = True
                    elif change == "uid":
                        bad["metadata"]["uid"] = "replacement"
                    elif change == "annotation":
                        bad["metadata"].setdefault("annotations", {})["foreign"] = "value"
                    with self.assertRaises(AssertionError):
                        MODULE.allowed(resource, original, bad, 1 if change == "error" else 0)

    def test_forbidden_matcher_requires_full_operation_and_never_echoes_diagnostics(self):
        args: list[Any] = [
            "patch",
            "deployments",
            "apps",
            "perf-platform",
            "ci-maintenance-denied",
            1,
            "Error from server (Forbidden): " + forbidden(),
        ]
        MODULE.denied(*args)
        MODULE.denied(
            "create",
            "configmaps",
            "",
            "argo-workflows",
            "ci-adoption-denied",
            1,
            "Error from server (Forbidden): "
            + forbidden("create", "configmaps", "", "argo-workflows", "ci-adoption-denied", False),
        )
        for change in (
            "exit",
            "success",
            "wrapper",
            "user",
            "verb",
            "resource",
            "group",
            "namespace",
            "name",
            "notfound",
            "transport",
        ):
            values = deepcopy(args)
            if change == "exit":
                values[5] = 124
            elif change == "success":
                values[5] = 0
            elif change == "wrapper":
                values[6] = values[6].replace("Error from server (Forbidden):", "error:")
            elif change == "user":
                values[6] = values[6].replace(MODULE.SUBJECT, "other-user")
            elif change == "verb":
                values[6] = values[6].replace("cannot patch", "cannot get")
            elif change == "resource":
                values[6] = values[6].replace('resource "deployments"', 'resource "pods"')
            elif change == "group":
                values[6] = values[6].replace('group "apps"', 'group "other"')
            elif change == "namespace":
                values[6] = values[6].replace("perf-platform", "other")
            elif change == "name":
                values[6] = values[6].replace("ci-maintenance-denied", "other")
            elif change == "notfound":
                values[6] = "Error from server (NotFound): private-sentinel"
            elif change == "transport":
                values[6] = "TLS timeout: private-sentinel"
            with self.subTest(change=change), self.assertRaises(AssertionError) as raised:
                MODULE.denied(*values)
            self.assertNotIn("private-sentinel", str(raised.exception))

    @unittest.skipUnless(KUBECTL, "kubectl required for actual PATCH/DELETE protocol regression")
    def test_actual_client_uses_direct_mutation_and_server_dry_run_with_full_impersonation(self):
        assert KUBECTL
        calls = []
        original = guards.MaintenancePauseTests()
        original.setUp()
        deployment = original.resources["deployment"]
        payload, _ = MODULE.prepare("deployment", deployment)
        invalid_response = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def send(self, code, value):
                raw = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/api":
                    value = {
                        "kind": "APIVersions",
                        "versions": ["v1"],
                        "serverAddressByClientCIDRs": [],
                    }
                elif path == "/apis":
                    value = {
                        "kind": "APIGroupList",
                        "groups": [
                            {
                                "name": "apps",
                                "versions": [{"groupVersion": "apps/v1", "version": "v1"}],
                                "preferredVersion": {"groupVersion": "apps/v1", "version": "v1"},
                            }
                        ],
                    }
                elif path in {"/api/v1", "/apis/apps/v1"}:
                    value = {
                        "kind": "APIResourceList",
                        "groupVersion": "apps/v1" if "apps" in path else "v1",
                        "resources": [
                            {
                                "name": "deployments",
                                "singularName": "deployment",
                                "namespaced": True,
                                "kind": "Deployment",
                                "verbs": ["get", "patch", "delete"],
                            }
                        ]
                        if "apps" in path
                        else [],
                    }
                else:
                    calls.append(("GET", path, {}, {}, None))
                    self.send(500, {"message": "Unexpected preliminary resource GET"})
                    return
                self.send(200, value)

            def mutate(self, verb):
                parsed = urlsplit(self.path)
                data = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
                )
                headers = {
                    "user": self.headers.get("Impersonate-User"),
                    "groups": self.headers.get_all("Impersonate-Group"),
                }
                calls.append((verb.upper(), parsed.path, parse_qs(parsed.query), headers, data))
                name = parsed.path.rsplit("/", 1)[-1]
                if invalid_response.is_set():
                    invalid_response.clear()
                    self.send(
                        422,
                        {
                            "apiVersion": "v1",
                            "kind": "Status",
                            "status": "Failure",
                            "reason": "Invalid",
                            "code": 422,
                            "details": {},
                            "message": "the server rejected our request due to an error in our request",
                        },
                    )
                elif verb == "patch" and name == "control-plane":
                    response = deepcopy(deployment)
                    response["spec"]["replicas"] = 0
                    self.send(200, response)
                else:
                    self.send(
                        403,
                        {
                            "apiVersion": "v1",
                            "kind": "Status",
                            "status": "Failure",
                            "reason": "Forbidden",
                            "code": 403,
                            "message": forbidden(verb, name=name),
                        },
                    )

            def do_PATCH(self):
                self.mutate("patch")

            def do_DELETE(self):
                self.mutate("delete")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                config, obj, patch_file = (
                    directory / "client.json",
                    directory / "handle.json",
                    directory / "patch.json",
                )
                config.write_text(
                    json.dumps(
                        {
                            "apiVersion": "v1",
                            "kind": "Config",
                            "clusters": [
                                {
                                    "name": "fixture",
                                    "cluster": {"server": f"http://127.0.0.1:{server.server_port}"},
                                }
                            ],
                            "users": [{"name": "fixture", "user": {}}],
                            "contexts": [
                                {
                                    "name": "fixture",
                                    "context": {"cluster": "fixture", "user": "fixture"},
                                }
                            ],
                            "current-context": "fixture",
                        }
                    )
                )
                patch_file.write_text(json.dumps(payload))
                for verb, name, invalid in (
                    ("patch", "control-plane", True),
                    ("patch", "control-plane", False),
                    ("patch", "ci-maintenance-denied", False),
                    ("delete", "ci-maintenance-denied", False),
                ):
                    obj.write_text(
                        json.dumps(MODULE.handle("apps/v1", "Deployment", "perf-platform", name))
                    )
                    command = [
                        KUBECTL,
                        "--kubeconfig",
                        str(config),
                        "--cache-dir",
                        str(directory / "cache"),
                        "--as=" + MODULE.SUBJECT,
                    ]
                    command += [
                        "--as-group=" + group
                        for group in (
                            "system:serviceaccounts",
                            "system:serviceaccounts:argo-workflows",
                            "system:authenticated",
                        )
                    ]
                    command += [
                        "--request-timeout=10s",
                        "-n",
                        "perf-platform",
                        verb,
                        "-f",
                        str(obj),
                        "--dry-run=server",
                        "-o",
                        "json" if verb == "patch" else "name",
                    ]
                    command += (
                        ["--type=json", "--patch-file", str(patch_file)]
                        if verb == "patch"
                        else ["--wait=false"]
                    )
                    if invalid:
                        invalid_response.set()
                    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
                    if invalid:
                        fresh = deepcopy(deployment)
                        fresh["metadata"]["resourceVersion"] = "fresh"
                        MODULE.refresh(deployment, fresh, result.returncode, result.stderr)
                    elif name == "control-plane":
                        MODULE.allowed(
                            "deployment", deployment, json.loads(result.stdout), result.returncode
                        )
                    else:
                        self.assertEqual(
                            result.returncode, 1, f"{verb}: {result.stderr}; requests={calls}"
                        )
                        self.assertIn(
                            "Error from server (Forbidden):",
                            result.stderr,
                            f"{verb}: {result.stderr}; requests={calls}",
                        )
                        MODULE.denied(
                            verb,
                            "deployments",
                            "apps",
                            "perf-platform",
                            name,
                            result.returncode,
                            result.stderr,
                        )
                self.assertEqual([call[0] for call in calls], ["PATCH", "PATCH", "PATCH", "DELETE"])
                for verb, path, query, headers, data in calls:
                    self.assertTrue(
                        path.startswith("/apis/apps/v1/namespaces/perf-platform/deployments/")
                    )
                    self.assertEqual(
                        query.get("dryRun", data.get("dryRun") if isinstance(data, dict) else None),
                        ["All"],
                    )
                    self.assertEqual(headers["user"], MODULE.SUBJECT)
                    self.assertEqual(
                        set(headers["groups"]),
                        {
                            "system:serviceaccounts",
                            "system:serviceaccounts:argo-workflows",
                            "system:authenticated",
                        },
                    )
                    if verb == "PATCH":
                        self.assertEqual(data, payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
