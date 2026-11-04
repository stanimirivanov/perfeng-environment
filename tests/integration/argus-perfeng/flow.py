"""Verify signed Argus change ingress and the checkout smoke Run."""

import hashlib
import importlib
import json
import os
import re
import secrets
from datetime import UTC, datetime

from scripts import control_plane_smoke

serving = importlib.import_module("tests.integration.argus-perfeng.serving")

GITHUB_HOST = "fixture-github.perf-platform.svc.cluster.local"
OPENAPI = """openapi: 3.1.0
info:
  title: Checkout
  version: "1"
paths:
  /api/v1/checkout:
    post:
      operationId: checkout
      x-argus-capabilities:
        - checkout
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                quantity:
                  type: {}
      responses:
        "201":
          description: Checkout accepted
"""


def change_receipt(expected, impact):
    if (
        impact.get("status") != "complete"
        or impact["baseRevision"]["digest"] != expected["base"]
        or impact["headRevision"]["digest"] != expected["head"]
        or impact["trigger"]["deliveryId"] != expected["delivery"]
    ):
        raise ValueError("retained change identity differs from signed delivery")
    capabilities = {
        name
        for document in impact["documents"]
        for operation in document["operations"]
        for name in operation["capabilities"]
    }
    if capabilities != {"checkout"}:
        raise ValueError("retained impact does not identify the checkout capability")
    return dict(expected)


class ChangeFlow(serving.Serving):
    def git(self, *args):
        return self.fixture.command(
            ["git", "-C", str(self.root / "source"), "-c", "core.hooksPath=/dev/null", *args],
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
        ).stdout

    def candidate_revision(self, _source, target):
        source = self.root / "source"
        (source / "api").mkdir(parents=True)
        self.git("init", "--quiet", "--object-format=sha1")
        (source / "server.py").write_text(target)
        revisions = []
        for field_type in ("string", "integer"):
            document = OPENAPI.format(field_type)
            (source / "api/openapi.yaml").write_text(document)
            self.git("add", "api/openapi.yaml", "server.py")
            self.git(
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgSign=false",
                "commit",
                "--quiet",
                "-m",
                field_type,
            )
            revision = self.git("rev-parse", "HEAD").strip()
            revisions.append(revision)
            self.private(f"change/{revision}", self.git("show", f"{revision}:api/openapi.yaml"))
        base, head = revisions
        if self.git("show", f"{head}:server.py") != target:
            raise ValueError("checkout target differs from the fixture Git head")
        self.change = {"base": base, "head": head, "delivery": self.name}
        pull = {
            "number": 1,
            "changed_files": 1,
            "base": {"sha": base, "repo": {"id": 1}},
            "head": {"sha": head},
        }
        diff = self.git("diff", "--no-ext-diff", base, head, "--", "api/openapi.yaml")
        patch = diff[diff.index("@@ ") :]
        self.private(
            "change/provider.json",
            json.dumps(
                {
                    "pull": pull,
                    "files": [
                        {
                            "filename": "api/openapi.yaml",
                            "status": "modified",
                            "additions": 1,
                            "deletions": 1,
                            "patch": patch,
                        }
                    ],
                }
            ),
        )
        return head

    def argus_configuration(self):
        self.private(
            "change/github_provider.py",
            (serving.ROOT / "tests/integration/argus-perfeng/github_provider.py").read_text(),
        )
        self.fixture.command(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                f"/CN={GITHUB_HOST}",
                "-addext",
                f"subjectAltName=DNS:{GITHUB_HOST}",
                "-keyout",
                str(self.root / "change/github.key"),
                "-out",
                str(self.root / "change/github.crt"),
            ]
        )
        token = secrets.token_urlsafe(48)
        self.webhook_secret = secrets.token_urlsafe(48)
        self.secret("fixture-github", serving.PLATFORM, {"FIXTURE_GITHUB_TOKEN": token})
        self.apply(
            serving.pod(
                "fixture-github",
                serving.PLATFORM,
                serving.PYTHON_IMAGE,
                ["python", "-B", "/fixture/change/github_provider.py"],
                secret="fixture-github",
                paths=("change",),
                probe={"tcpSocket": {"port": 8443}},
            )
        )
        self.service("fixture-github", serving.PLATFORM, 8443)
        self.wait("fixture-github", serving.PLATFORM)
        return {
            "ARGUS_GITHUB_API_URL": f"https://{GITHUB_HOST}:8443",
            "ARGUS_GITHUB_TOKEN": token,
            "ARGUS_GITHUB_WEBHOOK_SECRET": self.webhook_secret,
            "SSL_CERT_FILE": "/fixture/change/github.crt",
        }, ("change/github.crt", "argus-catalog")

    def after_ready(self):
        print("Submitting signed checkout change to Argus", flush=True)
        webhook = {
            "action": "synchronize",
            "number": 1,
            "repository": {"id": 1, "full_name": "perfeng-fixture/checkout"},
            "pull_request": {
                "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "base": {"sha": self.change["base"]},
                "head": {"sha": self.change["head"]},
            },
        }
        result = self.k(
            "-n",
            serving.SUT,
            "exec",
            "-i",
            "sample-sut",
            "--",
            "python",
            "-c",
            """
import hmac
import json
import sys
from urllib.request import Request, urlopen

submission = json.load(sys.stdin)
body = json.dumps(submission["webhook"]).encode()
signature = hmac.digest(submission["secret"].encode(), body, "sha256").hex()
request = Request("http://argus.perf-platform.svc.cluster.local:8080/webhooks/github",
                  data=body, headers={"Content-Type": "application/json",
                  "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": submission["delivery"],
                  "X-Hub-Signature-256": "sha256=" + signature})
with urlopen(request, timeout=12) as response:
    if response.status != 201:
        sys.exit(1)
""",
            data=json.dumps(
                {
                    "webhook": webhook,
                    "secret": self.webhook_secret,
                    "delivery": self.change["delivery"],
                }
            ),
        )
        if result.strip():
            raise ValueError("unexpected webhook probe output")
        impact = self.k(
            "-n",
            serving.PLATFORM,
            "exec",
            "argus",
            "--",
            "/fixture/argus-catalog",
            "get-change-impact",
            "-delivery-id",
            self.change["delivery"],
        )
        receipt = change_receipt(self.change, json.loads(impact))
        receipt["impactSha256"] = hashlib.sha256(impact.encode()).hexdigest()
        self.fixture.evidence["changeIngress"] = receipt
        self.checkout_smoke()

    def checkout_smoke(self):
        print("Running the checkout smoke workload for the retained Git head", flush=True)
        [entry] = json.loads((self.root / "registry/registry.json").read_text())["entries"]
        lock = serving.release.load_lock(serving.ROOT / "deployments/local-control-plane.json")
        request = control_plane_smoke.smoke_request(
            lock, self.change["head"], serving.ROOT.parent / "perfeng-k8s"
        )
        request["environment"] = {
            key: entry["environment"][key] for key in ("id", "version", "sha256")
        }
        runner = (
            """
import json
import os
import sys

os.environ.update(json.load(sys.stdin))
"""
            + control_plane_smoke.CLIENT_PROGRAM
        )
        result = self.k_command(
            "--request-timeout=430s",
            "-n",
            serving.SUT,
            "exec",
            "-i",
            "sample-sut",
            "--",
            "python",
            "-c",
            runner,
            data=json.dumps(
                {
                    "PERFENG_API": "http://perfeng.perf-platform.svc.cluster.local:8080",
                    "PERFENG_OPERATOR_TOKEN": (self.root / "fixture-provider-token").read_text(),
                    "PERFENG_IDEMPOTENCY_KEY": self.name,
                    "PERFENG_SMOKE_REQUEST": json.dumps(request),
                    "PERFENG_EXPECTED_FORMATS": json.dumps(control_plane_smoke.EXPECTED_FORMATS),
                }
            ),
            optional=True,
            timeout=420,
        )
        if result.returncode:
            raise ValueError("checkout smoke Run failed")
        receipt = json.loads(result.stdout)
        run_id = receipt["runId"]
        if re.fullmatch(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}", run_id) is None:
            raise ValueError("checkout smoke Run returned an invalid identity")
        uploader_log = self.k(
            "-n",
            serving.GENERATORS,
            "logs",
            f"job/{run_id}",
            "-c",
            "publish-raw",
            "--tail=20",
        )
        if f"Publishing k6 evidence for Run {run_id} (exit 0)" not in uploader_log.splitlines():
            raise ValueError("checkout smoke Run did not verify a successful k6 exit")
        receipt["k6ExitCode"] = 0
        receipt["candidateGitSha"] = self.change["head"]
        self.fixture.evidence["checkoutSmoke"] = receipt


if __name__ == "__main__":
    serving.main(ChangeFlow, description=__doc__)
