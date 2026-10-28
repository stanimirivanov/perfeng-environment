"""Disposable TLS fault for one configured, named maintenance PATCH."""

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
TARGETS = {
    "deployment": ("apps/v1", "Deployment", "perf-platform", "control-plane", ROUTE),
    "root": (
        "argoproj.io/v1alpha1",
        "Application",
        "argocd",
        "perfeng-local-root",
        "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/perfeng-local-root",
    ),
    "child": (
        "argoproj.io/v1alpha1",
        "Application",
        "argocd",
        "perfeng-local-control-plane",
        "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/perfeng-local-control-plane",
    ),
}


def target(expected: dict[str, Any]) -> str:
    before = expected["before"]
    for resource, (api, kind, namespace, name, _) in TARGETS.items():
        if (
            before.get("apiVersion"),
            before.get("kind"),
            before["metadata"].get("namespace"),
            before["metadata"].get("name"),
        ) == (api, kind, namespace, name):
            if (
                resource != "deployment"
                and before["spec"].get("syncPolicy", {}).get("automated", {}).get("enabled")
                is not False
            ):
                raise ValueError("Application fault requires an already disabled baseline")
            return resource
    raise ValueError("Fault target is outside the approved named resources")


def named_request(path: str, *, patch: bool, resource: str = "deployment") -> bool:
    """Accept only the named route and the copied client's fixed query semantics."""
    parsed = urlsplit(path)
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    allowed = {"timeout", "fieldManager", "fieldValidation"} if patch else {"timeout"}
    return (
        not (parsed.scheme or parsed.netloc or parsed.fragment)
        and resource in TARGETS
        and parsed.path == TARGETS[resource][4]
        and not query.keys() - allowed
        # --request-timeout=10s is preserved in the native copy. client-go puts
        # it on both the preliminary GET and the PATCH, not just the socket.
        and ("timeout" not in query or query["timeout"] == ["10s"])
        and ("fieldManager" not in query or query["fieldManager"] == ["kubectl-patch"])
        and (
            "fieldValidation" not in query
            or query["fieldValidation"] in (["Ignore"], ["Warn"], ["Strict"])
        )
    )


def committed(expected: dict[str, Any], obj: dict[str, Any], *, observed: bool = False) -> bool:
    if not isinstance(obj, dict) or not isinstance(obj.get("metadata"), dict):
        return False
    before = expected["before"]
    resource = target(expected)
    if resource != "deployment":
        generation = obj["metadata"].get("generation")
        return (
            obj.get("apiVersion") == before["apiVersion"]
            and obj.get("kind") == "Application"
            and bool(obj["metadata"].get("resourceVersion"))
            and not obj.get("operation")
            and not obj["metadata"].get("deletionTimestamp")
            and obj.get("spec") == before["spec"]
            and {
                k: v
                for k, v in obj["metadata"].items()
                if k not in {"resourceVersion", "managedFields", "generation"}
            }
            == {
                k: v
                for k, v in before["metadata"].items()
                if k not in {"resourceVersion", "managedFields", "generation"}
            }
            and (
                generation == before["metadata"].get("generation")
                or (observed and resource == "root" and type(generation) is int and generation > 0)
            )
        )
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
        self.resource = target(expected)
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
        api, kind, _, _, _ = TARGETS[self.server.resource]
        group_name, version = api.split("/")
        plural = "deployments" if kind == "Deployment" else "applications"
        group = {
            "name": group_name,
            "versions": [{"groupVersion": api, "version": version}],
            "preferredVersion": {"groupVersion": api, "version": version},
        }
        documents = {
            "/api": {
                "kind": "APIVersions",
                "apiVersion": "v1",
                "versions": ["v1"],
                "serverAddressByClientCIDRs": [],
            },
            "/apis": {"kind": "APIGroupList", "apiVersion": "v1", "groups": [group]},
            "/apis/" + group_name: {"kind": "APIGroup", "apiVersion": "v1", **group},
            "/api/v1": {
                "kind": "APIResourceList",
                "apiVersion": "v1",
                "groupVersion": "v1",
                "resources": [],
            },
            "/apis/" + api: {
                "kind": "APIResourceList",
                "apiVersion": "v1",
                "groupVersion": api,
                "resources": [
                    {
                        "name": plural,
                        "singularName": plural[:-1],
                        "namespaced": True,
                        "kind": kind,
                        "verbs": ["get", "patch"],
                    }
                ],
            },
        }
        parsed = urlsplit(self.path)
        if parsed.path in documents:
            body = json.dumps(documents[parsed.path]).encode()
        elif (
            named_request(self.path, patch=False, resource=self.server.resource)
            and not self.server.attempted
        ):
            connection = http.client.HTTPSConnection(
                self.server.upstream, context=self.server.context, timeout=10
            )
            try:
                authorization = self.authorization()
                connection.request("GET", self.path, headers={"Authorization": authorization})
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
            if not named_request(self.path, patch=True, resource=self.server.resource):
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
                self.server.resource + "-committed-response-dropped",
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
