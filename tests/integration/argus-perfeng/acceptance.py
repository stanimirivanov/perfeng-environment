"""Qualify a disposable checkout baseline and verify reviewed joint evidence."""

import base64
import copy
import hashlib
import importlib
import json
import math
import re
import secrets
import shlex
import statistics
import time
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit, urlunsplit

import yaml

flow = importlib.import_module("tests.integration.argus-perfeng.flow")
serving = flow.serving

BASELINE_ID = "joint-checkout-baseline"
VERSION = "1.0.0"
RUN = re.compile(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")


def encode(document):
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def catalog_reference(identity):
    return {
        "issuer": serving.ISSUER,
        "id": identity["id"],
        "revision": identity["version"],
        "sha256": identity["sha256"],
    }


def wire_reference(reference):
    return {key: reference[key] for key in ("issuer", "id", "revision", "sha256")}


def inward_scope():
    return {
        "Issuer": serving.ISSUER,
        "PrincipalIssuer": serving.ARGUS_ISSUER,
        "PrincipalID": "fixture-argus",
    }


def verify_projection(projection, run, report_sha):
    decisions = projection.get("analysis", {}).get("decisions", [])
    if (
        projection.get("run") != run
        or len(decisions) != 1
        or decisions[0].get("reportSha256") != report_sha
        or not decisions[0].get("rules")
    ):
        raise ValueError("authorized projection does not match the admitted Run and report")


def qualification(points):
    samples = [
        point["data"]["value"]
        for point in map(json.loads, points.splitlines())
        if point.get("type") == "Point" and point.get("metric") == "http_req_duration"
    ]
    if len(samples) < 10 or any(
        type(value) not in (float, int) or not math.isfinite(value) or value < 0
        for value in samples
    ):
        raise ValueError("baseline requires ten or more finite checkout HTTP timings")
    mean = statistics.fmean(samples)
    if mean <= 0:
        raise ValueError("baseline HTTP timings have no positive mean")
    return {
        "status": "PASSED",
        "reasons": [],
        "sampleCount": len(samples),
        "maximumCv": statistics.pstdev(samples) / mean,
    }


class Acceptance(flow.ChangeFlow):
    def registry_entries(self, entry, files):
        self.shorten_workload(entry, files)
        self.allocate_transfer_cpu(entry, files)
        selected = copy.deepcopy(entry)
        for key in ("catalogOwnerRoute", "integration"):
            del entry[key]
        policy = json.loads(files[selected["policy"].replace("/", "--")])
        policy["metadata"]["version"] = "1.1.0"
        rule = policy["spec"]["rules"][0]
        rule["regression"] = {
            "direction": "lower-is-better",
            "practicalDifference": {"kind": "relative", "value": 0.1},
            "reference": {"baselineId": BASELINE_ID, "version": VERSION},
        }
        selected["policy"] = "policies/checkout-joint.json"
        self.private("registry/" + selected["policy"], json.dumps(policy))
        self.reviewed_entry = selected
        return [entry, selected]

    @staticmethod
    def shorten_workload(entry, files):
        # The provider cancels an integration Run at its reviewed deadline.
        # Keep the original scenario and images, with fixture-only timing.
        configuration = encode(
            {
                "scenarios": {
                    "checkout_flow": {
                        "executor": "constant-arrival-rate",
                        "rate": 4,
                        "timeUnit": "1s",
                        "duration": "7s",
                        "preAllocatedVUs": 8,
                        "maxVUs": 20,
                        "gracefulStop": "2s",
                    }
                },
                "thresholds": {
                    "biz_checkout_duration": ["p(95)<600"],
                    "biz_transaction_error_rate": ["rate<0.02"],
                    "http_req_duration": ["p(95)<600"],
                    "http_req_failed": ["rate<0.02"],
                },
            }
        )
        original = entry["workload"]
        workload = {
            "id": "joint-checkout-smoke",
            "version": VERSION,
            "sha256": serving.digest(
                encode({"source": original, "config": configuration}).encode()
            ),
        }
        name = entry["executionTemplate"].replace("/", "--")
        job = json.loads(files[name])
        pod = job["spec"]["template"]["spec"]
        [runner] = [container for container in pod["initContainers"] if container["name"] == "k6"]
        [uploader] = [
            container for container in pod["containers"] if container["name"] == "publish-raw"
        ]
        [script] = runner["args"]
        original_config = "--config workloads/smoke/checkout.json"
        if script.count(original_config) != 1:
            raise ValueError("pinned checkout runner changed its configuration boundary")
        runner["args"] = [
            f"printf '%s' {shlex.quote(configuration)} > /tmp/joint-checkout.json\n"
            + script.replace(original_config, "--config /tmp/joint-checkout.json")
        ]
        [script] = uploader["args"]
        if script.count(original["sha256"]) != 1 or script.count(original["id"]) != 1:
            raise ValueError("pinned checkout uploader changed its workload identity boundary")
        uploader["args"] = [
            script.replace(original["sha256"], workload["sha256"]).replace(
                original["id"], workload["id"]
            )
        ]
        files[name] = encode(job)
        entry["workload"] = workload

    @staticmethod
    def allocate_transfer_cpu(entry, files):
        # The pinned AWS CLI launches a process for each S3 verification step.
        # Allow the fixture transfer containers to burst while retaining every check.
        for path in (entry["executionTemplate"], "jobs/analysis.yaml", "jobs/reporting.yaml"):
            key = path.replace("/", "--")
            job = yaml.safe_load(files[key])
            pod = job["spec"]["template"]["spec"]
            for container in (*pod.get("initContainers", []), *pod["containers"]):
                if container["image"].startswith("amazon/aws-cli:"):
                    container["resources"]["limits"]["cpu"] = "2"
            files[key] = encode(job)

    def perfeng_configuration(self):
        return {
            f"PERFENG_RECONCILIATION_{stage}_RETRY_AFTER": "1s"
            for stage in ("EXECUTION", "COLLECTION", "ANALYSIS", "REPORTING")
        }

    def provider_permissions(self):
        return super().provider_permissions() + [
            "baselines:create",
            "baselines:read",
            "baselines:transition",
        ]

    def storage(self, aws_image):
        uploader = super().storage(aws_image)
        self.secret(
            "fixture-baseline-reader",
            serving.PLATFORM,
            {
                "AWS_ACCESS_KEY_ID": uploader["access-key"],
                "AWS_SECRET_ACCESS_KEY": uploader["secret-key"],
                "AWS_DEFAULT_REGION": "us-east-1",
                "HOME": "/tmp",
            },
        )
        self.apply(
            serving.pod(
                "baseline-reader",
                serving.PLATFORM,
                aws_image,
                ["/bin/sh", "-ec", "sleep 1800"],
                secret="fixture-baseline-reader",
                paths=("store.crt",),
            )
        )
        self.wait("baseline-reader", serving.PLATFORM)
        return uploader

    def api(self, path, body=None, expected=200):
        raw = self.k(
            "-n",
            serving.SUT,
            "exec",
            "-i",
            "sample-sut",
            "--",
            "python",
            "-c",
            """
import http.client
import json
import sys

args = json.load(sys.stdin)
c = http.client.HTTPConnection("perfeng.perf-platform.svc.cluster.local", 8080, timeout=12)
headers = {"Authorization": "Bearer " + args["token"], "Content-Type": "application/json"}
body = args["body"]
c.request("GET" if body is None else "POST", args["path"],
          None if body is None else json.dumps(body), headers)
r = c.getresponse()
print(json.dumps({"status": r.status, "body": json.loads(r.read(1048576))}))
""",
            data=json.dumps(
                {
                    "path": path,
                    "body": body,
                    "token": (self.root / "fixture-provider-token").read_text(),
                }
            ),
        )
        result = json.loads(raw)
        if result["status"] == 404 and expected == (200, 404):
            return None
        if result["status"] != (200 if expected == (200, 404) else expected):
            raise ValueError(
                f"fixture Perfeng API returned HTTP {result['status']}, expected {expected}"
            )
        return result["body"]

    def artifact(self, reference, run_id):
        prefix = f"s3://{serving.BUCKET}/runs/{run_id}/"
        uri = reference["uri"]
        if (
            RUN.fullmatch(run_id) is None
            or reference["runId"] != run_id
            or not uri.startswith(prefix)
            or not re.fullmatch(r"[a-z0-9-]+\.(?:json|jsonl)", uri.removeprefix(prefix))
            or not 0 < reference["sizeBytes"] <= 16 << 20
        ):
            raise ValueError("fixture artifact scope is invalid")
        raw = self.k(
            "-n",
            serving.PLATFORM,
            "exec",
            "baseline-reader",
            "--",
            "/bin/sh",
            "-ec",
            """
aws --endpoint-url "$1" --ca-bundle /fixture/store.crt s3api get-object   --bucket "$2" --key "$3" --range "bytes=0-$4" /tmp/fixture-object >/dev/null
base64 /tmp/fixture-object
""",
            "fixture-read",
            f"https://{serving.STORE_HOST}:8443",
            serving.BUCKET,
            uri.removeprefix(f"s3://{serving.BUCKET}/"),
            str(reference["sizeBytes"]),
        )

        data = base64.b64decode(raw)
        if (
            len(data) != reference["sizeBytes"]
            or hashlib.sha256(data).hexdigest() != reference["sha256"]
        ):
            raise ValueError("fixture artifact bytes do not match the Run reference")
        return data

    def approve_baseline(self):
        run = self.fixture.evidence["checkoutSmoke"]
        run_id = run["runId"]
        claims = self.api(f"/v1/runs/{run_id}/artifacts")["artifacts"]
        [normalized] = [item for item in claims if item["format"] == "normalized-result/v1"]
        [points] = [item for item in claims if item["format"] == "k6-json-points"]
        normalized_document = json.loads(self.artifact(normalized, run_id))
        if normalized_document.get("runId") != run_id:
            raise ValueError("normalized baseline belongs to a different Run")
        observed = qualification(self.artifact(points, run_id))
        entry = self.reviewed_entry
        source = self.api(f"/v1/runs/{run_id}")
        reason = "Disposable joint checkout fixture qualification"
        request = {
            "id": BASELINE_ID,
            "version": VERSION,
            "testId": entry["testId"],
            "sourceRunId": run_id,
            "artifact": normalized,
            "software": source["request"]["candidate"],
            "workload": entry["workload"],
            "environment": entry["environment"],
            "dataset": entry["dataset"],
            "reason": reason,
        }
        record = self.api("/v1/baselines", request, expected=201)
        for state in ("QUALIFIED", "APPROVED"):
            change = {"expectedRevision": record["revision"], "state": state, "reason": reason}
            if state == "QUALIFIED":
                change["qualification"] = observed
            record = self.api(f"/v1/baselines/{BASELINE_ID}/versions/{VERSION}/transitions", change)
            if record["state"] != state or record["sourceRunId"] != run_id:
                raise ValueError("checkout baseline changed during review")
        self.fixture.evidence["checkoutBaseline"] = {
            "id": record["id"],
            "version": record["version"],
            "sourceRunId": run_id,
        }

    def argus_configuration(self):
        env, paths = super().argus_configuration()
        self.private(
            "argus-tools/provider_relay.py",
            (serving.ROOT / "tests/integration/argus-perfeng/provider_relay.py").read_text(),
        )
        (self.root / "argus-tools/reviews").mkdir(mode=0o700)
        token = (self.root / "fixture-provider-token").read_text()
        common = {
            "URL": "http://127.0.0.1:8081/integration/v1/requests",
            "TOKEN": token,
            "PROVIDER": serving.ISSUER,
            "PRINCIPAL_ISSUER": serving.ARGUS_ISSUER,
            "PRINCIPAL_ID": "fixture-argus",
            "REQUEST_ISSUER": serving.ARGUS_ISSUER,
            "ENABLED": "true",
            "POLL_INTERVAL": "1s",
        }
        analysis = common | {
            "EXPECTED_REVIEWER": "joint-fixture-reviewer",
            "REVIEW_DIRECTORY": "/fixture/argus-tools/reviews",
            "CA_FILE": "/fixture/store.crt",
            "S3_ACCESS_KEY_ID": self.report_reader["access-key"],
            "S3_SECRET_ACCESS_KEY": self.report_reader["secret-key"],
            "S3_REGION": "us-east-1",
            "S3_BUCKET": serving.BUCKET,
            "S3_PREFIX": "runs/",
            "S3_ENDPOINT": f"https://{serving.STORE_HOST}:8443",
            "S3_PATH_STYLE": "true",
        }
        env.update({"ARGUS_PERFORMANCE_DISPATCH_" + key: val for key, val in common.items()})
        env.update({"ARGUS_PERFORMANCE_ANALYSIS_" + key: val for key, val in analysis.items()})
        env["ARGUS_PERFENG_TOKEN"] = token
        env["ARGUS_PERFORMANCE_REQUEST_APPROVAL_FILE"] = (
            "/fixture/argus-tools/request-approval.json"
        )
        return env, (
            *paths,
            "argus-tools",
            "store.crt",
            "argus-select-performance",
            "argus-performance-requests",
            "argus-performance-catalog",
            "argus-performance-evidence",
        )

    def argus_pod(self, paths):
        resource = super().argus_pod(paths)
        relay = serving.pod(
            "provider-relay",
            serving.PLATFORM,
            serving.PYTHON_IMAGE,
            ["python", "-B", "/fixture/argus-tools/provider_relay.py"],
        )
        container = relay["spec"]["containers"][0]
        script = "/fixture/argus-tools/provider_relay.py"
        resource["spec"]["volumes"].append(
            {"name": "provider-relay", "hostPath": {"path": script, "type": "File"}}
        )
        container["volumeMounts"].append(
            {"name": "provider-relay", "mountPath": script, "readOnly": True}
        )
        resource["spec"]["containers"].append(container)
        return resource

    def cli(self, command, *args, data=None, optional=False):
        return self.k_command(
            "-n",
            serving.PLATFORM,
            "exec",
            "-i",
            "argus",
            "-c",
            "argus",
            "--",
            "/fixture/argus-" + command,
            *args,
            data=data,
            optional=optional,
        )

    def approve_request(self):
        print("Publishing and reviewing the approved checkout catalog selection", flush=True)
        flags = [
            "-issuer",
            serving.ISSUER,
            "-principal-issuer",
            serving.ARGUS_ISSUER,
            "-principal-id",
            "fixture-argus",
            "-producer-version",
            VERSION,
        ]
        publication = json.loads(
            self.cli(
                "performance-catalog",
                "-publish",
                "-endpoint",
                "http://127.0.0.1:8081/v1/performance-catalog",
                *flags,
            ).stdout
        )
        [page] = publication["pages"]
        items = {item["kind"]: item for item in page["items"]}
        [target] = items["profile"]["selections"]
        scope = flags + ["-delivery-id", self.change["delivery"]]
        context = json.loads(self.cli("select-performance", "-inspect", *scope).stdout)
        source = context["change"]["sourceRepository"]
        integration = self.reviewed_entry["integration"]
        principal = {"issuer": serving.ARGUS_ISSUER, "id": "fixture-argus"}
        reviewer = {"issuer": serving.ISSUER, "id": "joint-fixture-reviewer"}
        route = self.reviewed_entry["catalogOwnerRoute"]["reference"]
        workload, profile = items["workload"]["reference"], items["profile"]["reference"]
        selected = self.reviewed_entry
        policy_bytes = (self.root / "registry" / selected["policy"]).read_bytes()
        metadata = json.loads(policy_bytes)["metadata"]
        expected_policy = catalog_reference(
            {
                "id": metadata["name"],
                "version": metadata["version"],
                "sha256": serving.digest(policy_bytes),
            }
        )
        baseline = self.api(f"/v1/baselines/{BASELINE_ID}/versions/{VERSION}")
        if (
            workload != catalog_reference(selected["workload"])
            or profile != workload
            or target["policy"] != expected_policy
            or target["environment"] != catalog_reference(selected["environment"])
            or baseline["state"] != "APPROVED"
            or target["baseline"]["id"] != BASELINE_ID
            or target["baseline"]["revision"] != f"{VERSION}:{baseline['revision']}"
        ):
            raise ValueError("published catalog differs from the approved checkout inputs")
        image = integration["candidate"]["image"].split("@sha256:")[1]
        revision = {"algorithm": "git-sha1", "digest": self.change["head"]}
        reviewed_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        expires = context["catalog"]["validUntil"]
        deployment = {
            "environment": wire_reference(target["environment"]),
            "reference": integration["deployment"],
            "service": {key: integration["service"][key] for key in ("issuer", "id")},
            "imageSha256": image,
            "sourceRevision": revision,
        }
        mapping = {
            "id": "checkout-smoke",
            "capability": "checkout",
            "workload": workload,
            "profile": profile,
            **target,
        }
        policy = {
            "apiVersion": "argus.dev/performance-mapping-policy/v1",
            "repository": {
                key: source[key] for key in ("provider", "host", "providerRepositoryId")
            },
            "headRevision": revision,
            "impactSha256": context["impact"]["sha256"],
            "catalogSha256": context["catalog"]["fingerprint"],
            "reviewId": self.name,
            "reviewedBy": reviewer["id"],
            "reviewedAt": reviewed_at,
            "expiresAt": expires,
            "headCapabilities": ["checkout"],
            "allowedProfiles": [profile],
            "deployments": [deployment],
            "mappings": [mapping],
        }
        self.private("argus-tools/mapping.json", encode(policy))
        selection = json.loads(
            self.cli(
                "select-performance",
                "-policy",
                "/fixture/argus-tools/mapping.json",
                *scope,
            ).stdout
        )
        [decision] = selection["decisions"]
        if (
            decision["outcome"] != "candidate"
            or decision["target"] != mapping
            or decision["ownerRoute"] != route
        ):
            raise ValueError(
                "Argus selection differs from the independently reviewed fixture target"
            )
        approved = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        request = {"issuer": serving.ARGUS_ISSUER, "id": self.name + "-request"}
        attempt = {"issuer": serving.ARGUS_ISSUER, "id": self.name + "-attempt"}
        request_context = {
            "request": request,
            "attempt": attempt,
            "principal": principal,
            "requestedAt": approved,
            "deadline": expires,
            "service": integration["service"],
            "capability": integration["capability"],
            "deployment": integration["deployment"],
            "environment": wire_reference(target["environment"]),
            "workload": wire_reference(workload),
            "profile": wire_reference(profile),
            "policy": wire_reference(target["policy"]),
            "baseline": wire_reference(target["baseline"]),
            "evidence": integration["evidence"],
            "candidate": {"source": revision, "imageDigest": "sha256:" + image},
        }
        message = {
            "specversion": "1.0",
            "id": self.name + "-event",
            "source": serving.ARGUS_ISSUER,
            "type": "dev.perfeng.integration.requested.v1",
            "time": approved,
            "datacontenttype": "application/json",
            "dataschema": "https://perfeng.dev/schemas/performance-integration/v1/performance-integration.schema.json",
            "data": {"context": request_context},
        }
        review = {
            "Selection": selection,
            "Reviewer": reviewer,
            "ApprovedAt": approved,
            "ExpiresAt": expires,
            "Bindings": [{"MappingID": mapping["id"], "Message": message}],
        }
        review_bytes = encode(review)
        review_sha = serving.digest(review_bytes.encode())
        self.private("argus-tools/request-review.json", review_bytes)
        self.private(
            "argus-tools/request-approval.json",
            encode(
                {
                    "Authority": {
                        "ReviewSHA256": review_sha,
                        "RequestIssuer": serving.ARGUS_ISSUER,
                        "Reviewer": reviewer,
                        "Principal": principal,
                        "OwnerRoutes": [route],
                    },
                    "Expected": inward_scope()
                    | {"ProducerID": "perfeng-control-plane", "ProducerVersion": VERSION},
                }
            ),
        )
        created = json.loads(
            self.cli(
                "performance-requests",
                "-create",
                "-delivery-id",
                self.change["delivery"],
                "-policy",
                "/fixture/argus-tools/mapping.json",
                "-review",
                "/fixture/argus-tools/request-review.json",
            ).stdout
        )
        if not created["created"] or created["reviewSha256"] != review_sha:
            raise ValueError("Argus did not retain the independently approved request")
        return request_context, created["fingerprint"], serving.digest(encode(message).encode())

    def db(self, sql):
        name = self.name + "-argus"
        self.fixture.inspect("container", name)
        return self.fixture.docker(
            "exec",
            "-i",
            name,
            "psql",
            "-XAtq",
            "-U",
            "argus_migrator",
            "-d",
            "argus",
            "-v",
            "ON_ERROR_STOP=1",
            data=sql,
        ).stdout.strip()

    def wait_for(self, read, label, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = read()
            if result:
                return result
            time.sleep(2)
        raise ValueError(f"timed out waiting for {label}")

    def admission(self, request_context, request_sha, intent):
        print("Waiting for Argus dispatch and Perfeng admission", flush=True)
        sql = """
SELECT json_build_object('position', p.position, 'requestSha256', p.payload_sha256,
    'intentFingerprint', i.fingerprint, 'fingerprint', o.fingerprint,
    'kind', o.kind, 'event', convert_from(o.payload, 'UTF8')::json)
FROM argus_catalog.performance_pending_dispatches p
JOIN argus_catalog.performance_request_intents i USING (intent_uuid)
JOIN argus_catalog.performance_dispatch_outcomes o USING (position);
"""
        admitted = json.loads(self.wait_for(lambda: self.db(sql), "the one reviewed admission", 45))
        if (
            admitted["kind"] not in {"accepted", "lifecycle"}
            or admitted["requestSha256"] != request_sha
            or admitted["intentFingerprint"] != intent
            or admitted["event"]["data"]["context"] != request_context
        ):
            raise ValueError("Argus retained a foreign or rejected admission")
        return admitted

    def before_review(self, publication, admitted):
        pass

    def consume(self, request_context, admitted):
        print("Waiting for the admitted k6 Run and protected analysis publication", flush=True)
        request = request_context["request"]
        endpoint = f"/integration/v1/requests/{quote(request['id'], safe='')}/analysis?issuer={quote(request['issuer'], safe='')}"
        admitted_run = admitted["event"]["data"]["run"]
        if RUN.fullmatch(admitted_run["id"]) is None:
            raise ValueError("invalid admitted fixture Run")

        def publication_ready():
            result = self.api(endpoint, expected=(200, 404))
            if result is None:
                state = self.api(f"/v1/runs/{admitted_run['id']}")["state"]
                if state in {
                    "CANCELLING",
                    "ABORTED",
                    "INVALID",
                    "INFRASTRUCTURE_FAILURE",
                    "TEST_FAILURE",
                }:
                    raise ValueError(f"reviewed checkout Run ended in {state}")
            return result

        publication = self.wait_for(publication_ready, "analysis publication", 100)
        data = publication["data"]
        native = data["native"]
        run_id = native["runId"]
        if RUN.fullmatch(run_id) is None:
            raise ValueError("invalid native Run identity")
        run = self.api(f"/v1/runs/{run_id}")
        if (
            data["context"] != request_context
            or data["run"] != admitted["event"]["data"]["run"]
            or run["id"] != run_id
            or run["state"] != "COMPLETED"
            or run["request"]["candidate"] != self.reviewed_entry["integration"]["candidate"]
            or native["workload"] != self.reviewed_entry["workload"]
            or native["policy"] != run["request"]["policy"]
        ):
            raise ValueError("published analysis does not match the reviewed admitted Run")
        upload = self.k(
            "-n",
            serving.GENERATORS,
            "logs",
            f"job/{run_id}",
            "-c",
            "publish-raw",
            "--tail=20",
        )
        if f"Publishing k6 evidence for Run {run_id} (exit 0)" not in upload.splitlines():
            raise ValueError("reviewed Run lacks successful k6 exit evidence")
        reference = data["artifact"]
        report = self.artifact(reference, run_id)
        if json.loads(report)["runId"] != run_id:
            raise ValueError("analysis bytes identify a different Run")
        expected_baseline = self.api(f"/v1/baselines/{BASELINE_ID}/versions/{VERSION}")
        if native["baseline"]["artifact"] != expected_baseline["artifact"]:
            raise ValueError("published analysis uses a different baseline artifact")
        policy = (self.root / "registry" / self.reviewed_entry["policy"]).read_bytes()
        review = {
            "apiVersion": "argus.dev/performance-native-review/v1",
            "reviewId": self.name + "-native",
            "reviewedBy": "joint-fixture-reviewer",
            "provider": serving.ISSUER,
            "principal": request_context["principal"],
            "requestSha256": admitted["requestSha256"],
            "admissionSha256": admitted["fingerprint"],
            "native": native,
            "policyBytes": base64.b64encode(policy).decode(),
            "producer": self.reviewed_entry["reportProducer"],
            "contractsVersion": self.reviewed_entry["contractsVersion"],
        }
        self.before_review(publication, admitted)
        self.private("argus-tools/reviews/" + admitted["requestSha256"] + ".json", encode(review))
        sql = """SELECT json_build_object('position', position, 'event', event_id,
            'reportSha256', encode(sha256(report), 'hex'))
            FROM argus_catalog.performance_analysis_events;"""
        retained = json.loads(self.wait_for(lambda: self.db(sql), "retained Argus analysis", 45))
        if retained != {
            "position": admitted["position"],
            "event": publication["id"],
            "reportSha256": reference["sha256"],
        }:
            raise ValueError("Argus retained a different analysis publication")
        return data["run"], run_id, reference["sha256"]

    def export(self, admitted, run, report_sha):
        print("Exporting the consumed evidence with a restricted read-only login", flush=True)
        password = secrets.token_hex(24)
        self.db(f"""
CREATE ROLE fixture_exporter LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEROLE NOCREATEDB;
GRANT CONNECT ON DATABASE argus TO fixture_exporter;
GRANT USAGE ON SCHEMA argus_catalog TO fixture_exporter;
GRANT SELECT ON argus_catalog.performance_request_intents,
  argus_catalog.performance_pending_dispatches, argus_catalog.performance_dispatch_outcomes,
  argus_catalog.performance_lifecycle_events, argus_catalog.performance_analysis_events TO fixture_exporter;
""")
        stored = urlsplit(json.loads((self.root / "argus-credentials.json").read_text())["runtime"])
        owned = self.fixture.inspect("container", self.name + "-argus")
        ip = owned["NetworkSettings"]["Networks"][self.network]["IPAddress"]
        dsn = urlunsplit(stored._replace(netloc=f"fixture_exporter:{password}@{ip}:5432"))
        self.private("argus-tools/export-dsn", dsn)
        position = admitted["position"]
        self.private(
            "argus-tools/export-approval.json",
            encode(
                {
                    "scope": inward_scope(),
                    "position": position,
                    "intentFingerprint": admitted["intentFingerprint"],
                    "requestSha256": admitted["requestSha256"],
                    "expectedReviewer": "joint-fixture-reviewer",
                }
            ),
        )
        denied = self.read_projection(position + 1, optional=True)
        if not denied.returncode or denied.stdout:
            raise ValueError("out-of-scope export was not refused")
        raw = self.read_projection(position).stdout
        projection = json.loads(raw)
        verify_projection(projection, run, report_sha)
        if (
            projection["request"]["payloadSha256"] != admitted["requestSha256"]
            or projection["provenance"]["intentFingerprint"] != admitted["intentFingerprint"]
            or projection["target"]["source"]["digest"] != self.change["head"]
        ):
            raise ValueError("authorized projection does not match the reviewed request provenance")
        self.private("authorized-projection.json", raw)
        return serving.digest(raw.encode())

    def read_projection(self, position, *, optional=False):
        command = [
            "-n",
            serving.PLATFORM,
            "exec",
            "argus",
            "-c",
            "argus",
            "--",
            "/bin/sh",
            "-ec",
            """
export ARGUS_PERFORMANCE_EVIDENCE_DATABASE_URL="$(cat /fixture/argus-tools/export-dsn)"
export ARGUS_PERFORMANCE_EVIDENCE_APPROVAL_FILE=/fixture/argus-tools/export-approval.json
exec /fixture/argus-performance-evidence "$@"
""",
            "export",
            "-issuer",
            serving.ISSUER,
            "-principal-issuer",
            serving.ARGUS_ISSUER,
            "-principal-id",
            "fixture-argus",
            "-position",
        ]
        return self.k_command(*command, str(position), optional=optional)

    def after_ready(self):
        super().after_ready()
        print("Qualifying the checkout reference through Perfeng's baseline API", flush=True)
        self.approve_baseline()
        context, intent, request_sha = self.approve_request()
        admission = self.admission(context, request_sha, intent)
        run, run_id, report = self.consume(context, admission)
        projection = self.export(admission, run, report)
        self.fixture.evidence["jointAcceptance"] = {
            "runId": run_id,
            "reportSha256": report,
            "projectionSha256": projection,
            "requestSha256": request_sha,
            "intentFingerprint": intent,
        }
        self.fixture.evidence["productAcceptance"] = "passed"


if __name__ == "__main__":
    serving.main(Acceptance, description=__doc__)
