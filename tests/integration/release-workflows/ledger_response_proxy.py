"""Disposable CI transport fault: commit one exact receipt, then drop its response."""

from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import json
import socket
import ssl
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROUTE = "/api/v1/namespaces/perfeng-release-ledger/configmaps"
CREATE_ROUTE = ROUTE + "?fieldManager=kubectl-create&fieldValidation=Ignore"
ADDRESS = "https://ledger-response-proxy.argo-workflows.svc:8443"
ACCOUNT = "system:serviceaccount:argo-workflows:release-adoption"
LIMIT = 256 * 1024


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous duplicate fields in credentials or receipt content."""
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON field")
        value[key] = item
    return value


def certificate(directory: Path, hostname: str) -> tuple[Path, Path]:
    """Generate ephemeral fixture TLS files on the runner, never in the system store."""
    # cryptography is already locked in the runner environment. The proxy Pod
    # uses only the standard library and never calls this preparation helper.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "ledger-response.crt", directory / "ledger-response.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    return cert_path, key_path


def digest(value: object) -> str:
    """Hash canonical fixture content without printing it."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def receipt_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Permit server metadata only; immutable data and origin must match exactly."""
    return (
        isinstance(actual, dict)
        and isinstance(actual.get("metadata"), dict)
        and all(
            actual.get(k) == expected.get(k)
            for k in ("apiVersion", "kind", "immutable", "binaryData")
        )
        and all(
            actual.get("metadata", {}).get(k) == expected["metadata"][k]
            for k in ("name", "namespace", "labels", "annotations")
        )
        and bool(actual.get("metadata", {}).get("uid"))
    )


def fault_workflow(
    native: dict[str, Any], ledger: dict[str, Any], parameters: dict[str, str]
) -> dict[str, Any]:
    """Copy the published receipt create, changing only fixture identity/transport."""
    template = copy.deepcopy(
        next(t for t in native["spec"]["templates"] if t["name"] == "create-receipt")
    )
    # Argo adds -o json, incompatible with kubectl --raw. Serve fixed discovery
    # locally and bypass only client OpenAPI validation; API admission still runs.
    template["resource"]["flags"] = [
        "--kubeconfig=/fault/client.json",
        "--validate=false",
        "--field-manager=kubectl-create",
    ]
    template["podSpecPatch"] = json.dumps(
        {
            # Reference discovery runs before podSpecPatch, so this mount and
            # its source must be introduced together at the Pod level.
            "volumes": [{"name": "fault", "configMap": {"name": "ledger-response-proxy"}}],
            "containers": [
                {
                    "name": "main",
                    "volumeMounts": [{"name": "fault", "mountPath": "/fault", "readOnly": True}],
                }
            ],
        }
    )
    workflow = copy.deepcopy(ledger)
    workflow["metadata"]["generateName"] = "ledger-response-ci-"
    workflow["spec"].update(
        entrypoint="create-receipt",
        suspend=True,
        templates=[template],
    )
    workflow["spec"]["arguments"]["parameters"] = [
        {"name": key, "value": value} for key, value in parameters.items()
    ]
    return workflow


class Proxy(HTTPServer):
    """One synchronous, bounded forward; the proxy has no ServiceAccount token mount."""

    def __init__(
        self,
        address: tuple[str, int],
        expected: dict[str, Any],
        upstream: str,
        context: ssl.SSLContext,
    ):
        super().__init__(address, Handler)
        self.expected = expected
        self.upstream = upstream
        self.context = context
        self.attempted = False
        self.events: list[dict[str, Any]] = []
        self.tls: ssl.SSLContext | None = None

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        connection.settimeout(15)
        if self.tls is not None:
            try:
                connection = self.tls.wrap_socket(connection, server_side=True)
            except OSError:
                connection.close()
                raise
        return connection, address

    def event(self, name: str, **fields: object) -> None:
        record = {"event": name, **fields}
        self.events.append(record)
        print(json.dumps(record, sort_keys=True), flush=True)

    def handle_error(self, request: object, client_address: object) -> None:
        self.event("handler-failed")


class Handler(BaseHTTPRequestHandler):
    """Serve fixed discovery and one create; never log credentials or payloads."""

    server: Proxy

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format: str, *args: object) -> None:
        pass

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self) -> None:
        # Fixed discovery does not read/forward API state. Discovery's timeout
        # query has no effect; only the expected POST can reach upstream.
        documents = {
            "/api": {
                "kind": "APIVersions",
                "apiVersion": "v1",
                "versions": ["v1"],
                "serverAddressByClientCIDRs": [],
            },
            "/apis": {"kind": "APIGroupList", "apiVersion": "v1", "groups": []},
            "/api/v1": {
                "kind": "APIResourceList",
                "apiVersion": "v1",
                "groupVersion": "v1",
                "resources": [
                    {
                        "name": "configmaps",
                        "singularName": "configmap",
                        "namespaced": True,
                        "kind": "ConfigMap",
                        "verbs": ["create"],
                    }
                ],
            },
        }
        document = documents.get(urlsplit(self.path).path)
        if document is None:
            self.send_error(403)
            return
        data = json.dumps(document).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        length = self.headers.get_all("Content-Length", [])
        authorization = self.headers.get_all("Authorization", [])
        if self.path != CREATE_ROUTE or self.headers.get("Transfer-Encoding") or len(length) != 1:
            self.send_error(403)
            return
        try:
            size = int(length[0])
            if not 0 < size <= LIMIT:
                raise ValueError
            if (
                len(authorization) != 1
                or not authorization[0].startswith("Bearer ")
                or len(authorization[0]) > 16384
            ):
                raise ValueError
            # The actual API verifies the signature/audience/expiry. Reject unrelated
            # credentials before forwarding, including GitHub tokens and other SAs.
            token = authorization[0][7:].split(".")
            if len(token) != 3:
                raise ValueError
            claims = json.loads(
                base64.urlsafe_b64decode(token[1] + "=" * (-len(token[1]) % 4)),
                object_pairs_hook=unique_object,
            )
            if claims.get("sub") != ACCOUNT:
                raise ValueError
            raw = self.rfile.read(size)
            if (
                len(raw) != size
                or json.loads(raw, object_pairs_hook=unique_object) != self.server.expected
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            self.send_error(403)
            return
        if self.server.attempted:
            self.send_error(410)
            return
        self.server.attempted = True
        connection = http.client.HTTPSConnection(
            self.server.upstream, context=self.server.context, timeout=10
        )
        try:
            connection.request(
                "POST",
                CREATE_ROUTE,
                raw,
                {"Authorization": authorization[0], "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read(LIMIT + 1)
            if response.status != 201 or len(body) > LIMIT:
                self.server.event("upstream-refused", status=response.status)
                self.send_error(502)
                return
            committed = json.loads(body, object_pairs_hook=unique_object)
            if not receipt_matches(self.server.expected, committed):
                raise ValueError
            # Never send even the HTTP status line for the committed response.
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
            self.server.event(
                "receipt-committed-response-dropped",
                upstreamStatus=201,
                uid=committed["metadata"]["uid"],
                contentDigest=digest(self.server.expected),
                successfulResponsesForwarded=0,
            )
        except (OSError, ValueError, http.client.HTTPException):
            self.server.event("upstream-failed")
            self.close_connection = True
        finally:
            connection.close()


def verify(
    workflow: dict[str, Any],
    events: list[dict[str, Any]],
    records: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Bind the dropped response to a failed native worker and a fresh direct read."""
    assert workflow["status"]["phase"] in {"Failed", "Error"}
    assert workflow["spec"]["serviceAccountName"] == "release-adoption"
    assert workflow["spec"]["entrypoint"] == "create-receipt"
    assert workflow["spec"].get("shutdown") is None
    node = workflow["status"]["nodes"][workflow["metadata"]["name"]]
    assert node["type"] == "Pod" and node["phase"] in {"Failed", "Error"}
    assert not node.get("outputs", {}).get("parameters")
    assert not workflow["status"].get("outputs", {}).get("parameters")
    assert len(events) == 1 and events[0]["event"] == "receipt-committed-response-dropped"
    receipt = records["receipt"]
    assert records["pointer"] is None and receipt_matches(expected, receipt)
    assert (
        expected["metadata"]["annotations"]["perfeng.io/first-workflow-uid"]
        == workflow["metadata"]["uid"]
    )
    proof = events[0]
    assert proof["upstreamStatus"] == 201 and proof["successfulResponsesForwarded"] == 0
    assert proof["uid"] == receipt["metadata"]["uid"] and proof["contentDigest"] == digest(expected)
    return {
        "decision": "native-create-response-loss-observed",
        "workflowUID": workflow["metadata"]["uid"],
        "receiptUID": proof["uid"],
        "contentDigest": proof["contentDigest"],
    }


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    expected = json.loads((directory / "expected.json").read_text())
    upstream_context = ssl.create_default_context(cafile="/api/ca.crt")
    server = Proxy(("0.0.0.0", 8443), expected, "kubernetes.default.svc", upstream_context)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain("/tls/tls.crt", "/tls/tls.key")
    server.tls = tls
    server.serve_forever()
