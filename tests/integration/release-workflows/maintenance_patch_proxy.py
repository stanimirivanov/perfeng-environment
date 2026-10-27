"""Disposable TLS fault for one exact guarded maintenance Deployment PATCH."""

import base64
import http.client
import json
import socket
import ssl
import sys
from http.server import HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import ledger_response_proxy as ledger

ROUTE = "/apis/apps/v1/namespaces/perf-platform/deployments/control-plane"
ACCOUNT = "system:serviceaccount:argo-workflows:release-maintenance-pause"
ADDRESS = "https://maintenance-response-proxy.argo-workflows.svc:8443"


def committed(expected: dict[str, Any], obj: dict[str, Any]) -> bool:
    if not isinstance(obj, dict) or not isinstance(obj.get("metadata"), dict):
        return False
    before = expected["before"]
    return (
        obj.get("apiVersion") == "apps/v1"
        and obj.get("kind") == "Deployment"
        and obj.get("metadata", {}).get("uid") == before["metadata"]["uid"]
        and obj["metadata"].get("namespace") == "perf-platform"
        and obj["metadata"].get("name") == "control-plane"
        and bool(obj["metadata"].get("resourceVersion"))
        and obj["metadata"]["resourceVersion"] != before["metadata"]["resourceVersion"]
        and obj["metadata"].get("generation") == before["metadata"]["generation"] + 1
        and not obj["metadata"].get("deletionTimestamp")
        and {
            k: v
            for k, v in obj["metadata"].items()
            if k not in {"resourceVersion", "generation", "managedFields"}
        }
        == {
            k: v
            for k, v in before["metadata"].items()
            if k not in {"resourceVersion", "generation", "managedFields"}
        }
        and obj.get("spec") == {**before["spec"], "replicas": 0}
    )


class Proxy(ledger.Proxy):
    def __init__(
        self,
        address: tuple[str, int],
        expected: dict[str, Any],
        upstream: str,
        context: ssl.SSLContext,
    ):
        HTTPServer.__init__(self, address, Handler)
        self.expected, self.upstream, self.context = expected, upstream, context
        self.attempted = False
        self.events = []
        self.tls = None


class Handler(ledger.Handler):
    server: Proxy

    def authorization(self) -> str:
        headers = self.headers.get_all("Authorization", [])
        if len(headers) != 1 or not headers[0].startswith("Bearer ") or len(headers[0]) > 16384:
            raise ValueError
        pieces = headers[0][7:].split(".")
        if len(pieces) != 3:
            raise ValueError
        claims = json.loads(
            base64.urlsafe_b64decode(pieces[1] + "=" * (-len(pieces[1]) % 4)),
            object_pairs_hook=ledger.unique_object,
        )
        if not isinstance(claims, dict) or claims.get("sub") != ACCOUNT:
            raise ValueError
        return headers[0]

    def do_GET(self) -> None:
        group = {
            "name": "apps",
            "versions": [{"groupVersion": "apps/v1", "version": "v1"}],
            "preferredVersion": {"groupVersion": "apps/v1", "version": "v1"},
        }
        documents = {
            "/api": {
                "kind": "APIVersions",
                "apiVersion": "v1",
                "versions": ["v1"],
                "serverAddressByClientCIDRs": [],
            },
            "/apis": {"kind": "APIGroupList", "apiVersion": "v1", "groups": [group]},
            "/apis/apps": {"kind": "APIGroup", "apiVersion": "v1", **group},
            "/api/v1": {
                "kind": "APIResourceList",
                "apiVersion": "v1",
                "groupVersion": "v1",
                "resources": [],
            },
            "/apis/apps/v1": {
                "kind": "APIResourceList",
                "apiVersion": "v1",
                "groupVersion": "apps/v1",
                "resources": [
                    {
                        "name": "deployments",
                        "singularName": "deployment",
                        "namespaced": True,
                        "kind": "Deployment",
                        "verbs": ["get", "patch"],
                    }
                ],
            },
        }
        parsed = urlsplit(self.path)
        if parsed.path in documents:
            body = json.dumps(documents[parsed.path]).encode()
        elif self.path == ROUTE and not self.server.attempted:
            connection = http.client.HTTPSConnection(
                self.server.upstream, context=self.server.context, timeout=10
            )
            try:
                authorization = self.authorization()
                connection.request("GET", ROUTE, headers={"Authorization": authorization})
                response = connection.getresponse()
                body = response.read(ledger.LIMIT + 1)
                if response.status != 200 or len(body) > ledger.LIMIT:
                    raise ValueError
                obj = json.loads(body, object_pairs_hook=ledger.unique_object)
                before = self.server.expected["before"]
                if (
                    obj["metadata"]["uid"] != before["metadata"]["uid"]
                    or obj["metadata"]["resourceVersion"] != before["metadata"]["resourceVersion"]
                    or obj["spec"] != before["spec"]
                ):
                    raise ValueError
            except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
                self.send_error(403)
                return
            finally:
                connection.close()
        else:
            self.send_error(403)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self.send_error(403)

    def do_PATCH(self) -> None:
        try:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (
                parsed.scheme
                or parsed.netloc
                or parsed.fragment
                or parsed.path != ROUTE
                or any(k not in {"fieldManager", "fieldValidation"} for k in query)
            ):
                raise ValueError
            if "fieldManager" in query and query["fieldManager"] != ["kubectl-patch"]:
                raise ValueError
            if "fieldValidation" in query and query["fieldValidation"] not in (
                ["Ignore"],
                ["Warn"],
                ["Strict"],
            ):
                raise ValueError
            lengths = self.headers.get_all("Content-Length", [])
            if (
                self.headers.get("Transfer-Encoding")
                or len(lengths) != 1
                or not 0 < int(lengths[0]) <= ledger.LIMIT
            ):
                raise ValueError
            if self.headers.get_all("Content-Type", []) != ["application/json-patch+json"]:
                raise ValueError
            authorization = self.authorization()
            body = self.rfile.read(int(lengths[0]))
            if (
                len(body) != int(lengths[0])
                or json.loads(body, object_pairs_hook=ledger.unique_object)
                != self.server.expected["patch"]
            ):
                raise ValueError
        except (ValueError, TypeError):
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
                "PATCH",
                self.path,
                body,
                {"Authorization": authorization, "Content-Type": "application/json-patch+json"},
            )
            response = connection.getresponse()
            raw = response.read(ledger.LIMIT + 1)
            if (
                response.status != 200
                or len(raw) > ledger.LIMIT
                or not committed(
                    self.server.expected, json.loads(raw, object_pairs_hook=ledger.unique_object)
                )
            ):
                raise ValueError
            obj = json.loads(raw)
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
            self.server.event(
                "deployment-committed-response-dropped",
                upstreamStatus=200,
                uid=obj["metadata"]["uid"],
                resourceVersion=obj["metadata"]["resourceVersion"],
                patchDigest=ledger.digest(self.server.expected["patch"]),
                successfulResponsesForwarded=0,
            )
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            self.server.event("upstream-failed")
            self.close_connection = True
        finally:
            connection.close()


if __name__ == "__main__":
    expected = json.loads((Path(sys.argv[1]) / "expected.json").read_text())
    server = Proxy(
        ("0.0.0.0", 8443),
        expected,
        "kubernetes.default.svc",
        ssl.create_default_context(cafile="/api/ca.crt"),
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain("/tls/tls.crt", "/tls/tls.key")
    server.tls = tls
    server.serve_forever()
