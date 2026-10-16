"""Standard-library client executed inside the Secret-referenced acceptance Pod."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

LIMIT = 1 << 20
API = "http://control-plane.perf-platform.svc.cluster.local:8080"
PATH = re.compile(
    r"/v1/(?:runs(?:/perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}(?:/artifacts|/cancel)?)?"
    r"|baselines(?:/approved-local-pod-startup-contained/versions/1\.0\.0(?:/transitions)?)?)"
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward an operator bearer token to a redirected origin."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        """Refuse every redirect, including a same-origin redirect."""
        return None


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse ambiguous duplicate fields before a response is forwarded or acted on."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Acceptance API JSON contains duplicate fields")
        result[key] = value
    return result


def call(value: dict[str, Any]) -> dict[str, Any]:
    """Perform one bounded API operation; never retry an outcome-uncertain write."""
    path, method = value["path"], value["method"]
    if not isinstance(path, str) or PATH.fullmatch(path) is None or method not in {"GET", "POST"}:
        raise ValueError("Unsupported acceptance API operation")
    headers = {"Authorization": "Bearer " + os.environ["PERFENG_OPERATOR_TOKEN"]}
    data = None
    if value.get("body") is not None:
        data = json.dumps(value["body"], allow_nan=False).encode()
        if len(data) > 65536:
            raise ValueError("Acceptance request exceeds the API body bound")
        headers["Content-Type"] = "application/json"
    if value.get("key") is not None:
        if re.fullmatch(r"m04-[a-f0-9]{32}", value["key"]) is None:
            raise ValueError("Invalid acceptance idempotency key")
        headers["Idempotency-Key"] = value["key"]
    request = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, TimeoutError):
        raise ValueError("Acceptance API unavailable; write outcome may be unknown") from None
    with response:
        content = response.read(LIMIT + 1)
        status = response.code
        if len(content) > LIMIT or response.headers.get_content_type() != "application/json":
            raise ValueError("Acceptance API returned an invalid bounded response")
    body = json.loads(content, object_pairs_hook=unique_object)
    if not isinstance(body, dict):
        raise ValueError("Acceptance API response is not an object")
    if status >= 400:
        code = body.get("code")
        if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z_]{0,63}", code) is None:
            raise ValueError("Acceptance API returned an unclassified error")
        body = {"code": code}
    return {"status": status, "body": body}


if __name__ == "__main__":
    try:
        incoming = sys.stdin.buffer.read(65537)
        if len(incoming) > 65536:
            raise ValueError("Acceptance command exceeds its input bound")
        print(
            json.dumps(call(json.loads(incoming, object_pairs_hook=unique_object)), allow_nan=False)
        )
    except (OSError, ValueError, KeyError, TypeError):
        print(
            '{"error":"Acceptance API call failed; inspect the retained session and idempotency key"}'
        )
        sys.exit(1)
