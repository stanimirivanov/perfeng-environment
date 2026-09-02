"""Immutable local uploader credentials and live S3 authorization checks."""

import argparse
import base64
import json
import re
import secrets
import uuid
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster
    from . import object_store as store
else:
    import cluster
    import object_store as store

UPLOADER_SECRET = "perfeng-s3-uploader-auth"
SERVER_SECRET = "perfeng-s3-server-auth-v2"
POLICY = "local-artifact-upload"


def policy() -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": [f"arn:aws:s3:::{store.BUCKET}/runs/*"],
            }
        ],
    }


def server_config(bootstrap: dict[str, str], uploader: dict[str, str]) -> dict[str, Any]:
    result = store.s3_config(bootstrap["access-key"], bootstrap["secret-key"])
    result["identities"].append(
        {
            "name": "local-artifact-uploader",
            "credentials": [
                {
                    "accessKey": uploader["access-key"],
                    "secretKey": uploader["secret-key"],
                }
            ],
            "policyNames": [POLICY],
        }
    )
    result["policies"] = [{"name": POLICY, "content": json.dumps(policy(), sort_keys=True)}]
    return result


def decode(value: dict[str, Any], name: str, keys: set[str]) -> dict[str, str]:
    try:
        if (
            value["type"] != "Opaque"
            or value["immutable"] is not True
            or value["metadata"]["name"] != name
            or value["metadata"]["namespace"] != store.NAMESPACE
            or value["metadata"]["labels"]["app.kubernetes.io/managed-by"] != store.OWNER
            or set(value["data"]) != keys
        ):
            raise ValueError()
        result = {
            key: base64.b64decode(value["data"][key], validate=True).decode("utf-8") for key in keys
        }
        for key, size in [("access-key", 32), ("secret-key", 64)]:
            if key in keys and not re.fullmatch(f"[0-9a-f]{{{size}}}", result[key]):
                raise ValueError()
        return result
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            f"Invalid owned immutable Secret {name}; restore it, do not rotate"
        ) from None


def secret_document(name: str, data: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": store.NAMESPACE,
            "labels": {"app.kubernetes.io/managed-by": store.OWNER},
        },
        "type": "Opaque",
        "immutable": True,
        "stringData": data,
    }


def uses_server_secret(text: str) -> bool:
    value = json.loads(text) if text.strip() else {}
    volumes = value.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", [])
    return any(v.get("secret", {}).get("secretName") == SERVER_SECRET for v in volumes)


def ensure_credentials(root: Path, *, create: bool) -> None:
    kube = store.kubectl(root)
    current = {}
    for name in [store.SECRET, UPLOADER_SECRET, SERVER_SECRET]:
        text = cluster.run([*kube, "get", "secret", name, "--ignore-not-found", "-o", "json"])
        current[name] = json.loads(text) if text.strip() else None
    bootstrap_secret = current[store.SECRET]
    if bootstrap_secret is None:
        raise ValueError("Bootstrap S3 credentials missing; deploy object storage first")
    store.validate_secret(bootstrap_secret)
    bootstrap = decode(bootstrap_secret, store.SECRET, {"access-key", "secret-key", "s3.json"})
    uploader = current[UPLOADER_SECRET]
    server = current[SERVER_SECRET]
    if uploader is None and server is not None:
        raise ValueError(
            "Server credentials exist without uploader Secret; restore the matching Secret"
        )
    if not create and (uploader is None or server is None):
        raise ValueError(
            "Restricted storage credentials missing; run object_store.py deploy --execute"
        )
    if uploader is None:
        active = cluster.run(
            [*kube, "get", "statefulset", "seaweedfs", "--ignore-not-found", "-o", "json"]
        )
        if uses_server_secret(active):
            raise ValueError(
                "Restricted server is deployed without credentials; restore matching Secrets"
            )
        credentials = {"access-key": secrets.token_hex(16), "secret-key": secrets.token_hex(32)}
    else:
        credentials = decode(uploader, UPLOADER_SECRET, {"access-key", "secret-key"})
    if any(credentials[key] == bootstrap[key] for key in credentials):
        raise ValueError("Uploader credentials must differ from bootstrap credentials")
    expected = server_config(bootstrap, credentials)
    if server is not None:
        data = decode(server, SERVER_SECRET, {"s3.json"})
        try:
            valid = json.loads(data["s3.json"]) == expected
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Server policy/credentials mismatch; restore matching Secrets")
    # All existing credentials are validated before writes. Retry reuses a partial pair.
    for name, existing, data in [
        (UPLOADER_SECRET, uploader, credentials),
        (SERVER_SECRET, server, {"s3.json": json.dumps(expected)}),
    ]:
        if existing is None:
            cluster.run(
                [*kube, "create", "-f", "-"],
                input_data=json.dumps(secret_document(name, data)).encode(),
            )
    if not create:
        active = cluster.run([*kube, "get", "statefulset", "seaweedfs", "-o", "json"])
        if not uses_server_secret(active):
            raise ValueError(
                "Restricted server configuration is not deployed; deploy object storage"
            )
    print("Restricted S3 credentials validated; bootstrap credentials unchanged.", flush=True)


def restrict_job(job: dict[str, Any]) -> None:
    for container in job["spec"]["template"]["spec"]["containers"]:
        for entry in container.get("env", []):
            reference = entry.get("valueFrom", {}).get("secretKeyRef")
            if reference:
                if reference["name"] != store.SECRET:
                    raise ValueError("Unexpected client credential reference")
                reference["name"] = UPLOADER_SECRET


def verification_job(probe: str, root: Path) -> dict[str, Any]:
    if not re.fullmatch("[0-9a-f]{32}", probe):
        raise ValueError("Invalid authorization probe ID")
    job = store.client_job("health", probe, f"s3-access-{probe[:16]}", root)
    restrict_job(job)
    job["spec"]["activeDeadlineSeconds"] = 300
    # Only the newly created probe is used for the destructive denial test.
    script = f"""set -eu
printf '[default]\\ns3 =\\n    addressing_style = path\\n' > /tmp/awsconfig
s3() {{ aws --endpoint-url "{store.ENDPOINT}" --cli-connect-timeout 10 --cli-read-timeout 30 s3api "$@"; }}
denied() {{
  if s3 "$@" >/tmp/result 2>/tmp/error; then echo "ERROR: forbidden operation succeeded"; exit 1; fi
  grep -Eq 'AccessDenied|Forbidden|403' /tmp/error
}}
printf 'restricted-storage-probe\\n' > /tmp/source
s3 put-object --bucket "{store.BUCKET}" --key "runs/access-check/{probe}.bin" --body /tmp/source --if-none-match "*" >/dev/null
s3 get-object --bucket "{store.BUCKET}" --key "runs/access-check/{probe}.bin" /tmp/download >/dev/null
set -- $(sha256sum /tmp/source)
expected=$1
set -- $(sha256sum /tmp/download)
test "$1" = "$expected"
denied delete-object --bucket "{store.BUCKET}" --key "runs/access-check/{probe}.bin"
denied put-object --bucket "{store.BUCKET}" --key "outside-runs/{probe}.bin" --body /tmp/source --if-none-match "*"
denied get-object --bucket "{store.BUCKET}" --key "outside-runs/{probe}.bin" /tmp/forbidden
denied list-objects-v2 --bucket "{store.BUCKET}" --max-keys 1
denied get-bucket-policy --bucket "{store.BUCKET}"
denied get-object --bucket "perfeng-access-denied" --key "runs/{probe}.bin" /tmp/other-bucket
s3 get-object --bucket "{store.BUCKET}" --key "runs/access-check/{probe}.bin" /tmp/after >/dev/null
set -- $(sha256sum /tmp/after)
test "$1" = "$expected"
echo "Allowed PUT/GET verified; DELETE, outside-prefix PUT/GET, listing and bucket-policy access denied."
"""
    job["spec"]["template"]["spec"]["containers"][0]["command"] = ["/bin/sh", "-c", script]
    return job


def execute(root: Path = cluster.ROOT) -> None:
    document = verification_job(uuid.uuid4().hex, root)
    cluster.verify_local_docker()
    cluster.verify_context(root)
    ensure_credentials(root, create=False)
    kube = store.kubectl(root)
    cluster.run([*kube, "rollout", "status", "statefulset/seaweedfs", "--timeout=300s"])
    name = document["metadata"]["name"]
    print(f"Verifying restricted storage access with Job {name}...", flush=True)
    cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(document).encode())
    try:
        cluster.run([*kube, "wait", "--for=condition=complete", f"job/{name}", "--timeout=330s"])
    except ValueError:
        raise ValueError(
            f"Access verification failed; inspect Job {name} within 10 minutes"
        ) from None
    print("Restricted PUT/GET and denial checks passed. Authorization probe retained.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            execute()
        else:
            print(json.dumps(verification_job("0" * 32, cluster.ROOT), indent=2))
            print("Preview only; --execute writes one probe and checks permissions.")
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Storage access error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
