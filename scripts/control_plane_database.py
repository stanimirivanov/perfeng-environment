"""Provision isolated local control-plane PostgreSQL identities."""

import argparse
import base64
import json
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

if __package__:
    from . import cluster, postgres
else:
    import cluster
    import postgres

SECRET = "perfeng-control-plane-database"
MIGRATION_ROLE = "perfeng_migration"
RUNTIME_ROLE = "perfeng_runtime"
DATABASE = "perfeng"
HOST = "postgres.perf-platform.svc.cluster.local"
PASSWORD = re.compile(r"^[A-Za-z0-9_-]{43}$")


def database_url(role: str, password: str) -> str:
    """Build one fixed local PostgreSQL URL."""
    if role not in {MIGRATION_ROLE, RUNTIME_ROLE} or PASSWORD.fullmatch(password) is None:
        raise ValueError("Invalid control-plane database credential")
    return f"postgresql://{role}:{password}@{HOST}:5432/{DATABASE}?sslmode=disable"


def secret_document(migration_password: str, runtime_password: str) -> dict[str, Any]:
    """Build the immutable environment-owned database credential Secret."""
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": SECRET,
            "namespace": postgres.NAMESPACE,
            "labels": {
                "app.kubernetes.io/managed-by": postgres.OWNER,
                "perfeng.io/purpose": "control-plane-database",
            },
        },
        "type": "Opaque",
        "immutable": True,
        "stringData": {
            "migration-url": database_url(MIGRATION_ROLE, migration_password),
            "runtime-url": database_url(RUNTIME_ROLE, runtime_password),
        },
    }


def decode_url(value: str, role: str) -> str:
    """Validate a stored URL and return its password."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "postgresql"
        or parsed.username != role
        or parsed.hostname != HOST
        or parsed.port != 5432
        or parsed.path != f"/{DATABASE}"
        or parsed.query != "sslmode=disable"
        or parsed.fragment
        or parsed.password is None
        or database_url(role, parsed.password) != value
    ):
        raise ValueError()
    return parsed.password


def decode_secret(value: dict[str, Any]) -> tuple[str, str]:
    """Validate and decode the owned immutable credential Secret."""
    try:
        if (
            value["type"] != "Opaque"
            or value["immutable"] is not True
            or value["metadata"]["name"] != SECRET
            or value["metadata"]["namespace"] != postgres.NAMESPACE
            or value["metadata"]["labels"]
            != {
                "app.kubernetes.io/managed-by": postgres.OWNER,
                "perfeng.io/purpose": "control-plane-database",
            }
            or set(value["data"]) != {"migration-url", "runtime-url"}
        ):
            raise ValueError()
        migration = base64.b64decode(value["data"]["migration-url"], validate=True).decode()
        runtime = base64.b64decode(value["data"]["runtime-url"], validate=True).decode()
        return decode_url(migration, MIGRATION_ROLE), decode_url(runtime, RUNTIME_ROLE)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ValueError(
            f"Invalid owned immutable Secret {SECRET}; restore it, do not rotate"
        ) from None


def psql(root: Path, sql: bytes) -> str:
    """Run administrative SQL through private stdin inside the local database Pod."""
    return cluster.run(
        [
            *postgres.kubectl(root),
            "exec",
            "-i",
            "statefulset/postgres",
            "--",
            "psql",
            "-X",
            "-U",
            "postgres",
            "-d",
            DATABASE,
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input_data=sql,
    )


def role_state(root: Path) -> dict[str, str]:
    """Return the bounded security attributes for existing application roles."""
    output = psql(
        root,
        b"SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
        b"rolreplication, rolbypassrls FROM pg_roles WHERE rolname IN "
        b"('perfeng_migration','perfeng_runtime') ORDER BY rolname;\n",
    )
    result: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split("|")
        if len(fields) != 7 or fields[0] in result:
            raise ValueError("PostgreSQL returned invalid control-plane role state")
        result[fields[0]] = "|".join(fields[1:])
    if not set(result) <= {MIGRATION_ROLE, RUNTIME_ROLE}:
        raise ValueError("PostgreSQL returned unexpected control-plane roles")
    return result


def provision_roles(root: Path, passwords: tuple[str, str], existing: dict[str, str]) -> None:
    """Create only missing logins and establish the fixed database privileges."""
    expected = "t|f|f|f|f|f"
    if any(attributes != expected for attributes in existing.values()):
        raise ValueError("Existing control-plane database role has unsafe attributes")
    statements = ["BEGIN;"]
    for role, password in zip((MIGRATION_ROLE, RUNTIME_ROLE), passwords, strict=True):
        if role not in existing:
            statements.append(f"CREATE ROLE {role} LOGIN PASSWORD '{password}';")
    statements.extend(
        [
            f"REVOKE CREATE ON DATABASE {DATABASE} FROM PUBLIC;",
            f"GRANT CONNECT ON DATABASE {DATABASE} TO {MIGRATION_ROLE}, {RUNTIME_ROLE};",
            f"GRANT CREATE ON DATABASE {DATABASE} TO {MIGRATION_ROLE};",
            f"REVOKE CREATE ON DATABASE {DATABASE} FROM {RUNTIME_ROLE};",
            "COMMIT;",
        ]
    )
    psql(root, ("\n".join(statements) + "\n").encode())


def verify_login(root: Path, role: str, password: str) -> None:
    """Verify one TCP login while carrying its password only through private stdin."""
    script = (
        "set -eu\n"
        f"export PGPASSWORD='{password}'\n"
        f"exec psql -X -w -h {HOST} -U {role} -d {DATABASE} -At "
        "-v ON_ERROR_STOP=1 -c 'SELECT 1'\n"
    )
    output = cluster.run(
        [*postgres.kubectl(root), "exec", "-i", "statefulset/postgres", "--", "sh"],
        input_data=script.encode(),
    )
    if output.strip() != "1":
        raise ValueError("Control-plane database login verification failed")


def verify_roles(root: Path, roles: dict[str, str], passwords: tuple[str, str]) -> None:
    """Verify exact role attributes and effective database privileges."""
    expected = {MIGRATION_ROLE: "t|f|f|f|f|f", RUNTIME_ROLE: "t|f|f|f|f|f"}
    if roles != expected:
        raise ValueError("Control-plane database role verification failed")
    privileges = psql(
        root,
        b"SELECT has_database_privilege('perfeng_migration','perfeng','CONNECT'), "
        b"has_database_privilege('perfeng_migration','perfeng','CREATE'), "
        b"has_database_privilege('perfeng_runtime','perfeng','CONNECT'), "
        b"has_database_privilege('perfeng_runtime','perfeng','CREATE');\n",
    ).strip()
    if privileges != "t|t|t|f":
        raise ValueError("Control-plane database privileges are invalid")
    for role, password in zip((MIGRATION_ROLE, RUNTIME_ROLE), passwords, strict=True):
        verify_login(root, role, password)


def ensure_identities(root: Path, *, create: bool) -> None:
    """Validate existing identities or create a recoverable Secret-first pair."""
    kube = postgres.kubectl(root)
    current = cluster.run([*kube, "get", "secret", SECRET, "--ignore-not-found", "-o", "json"])
    existing = role_state(root)
    if current.strip():
        passwords = decode_secret(json.loads(current))
    else:
        if not create:
            raise ValueError("Control-plane database credentials are missing; run deploy first")
        if existing:
            raise ValueError(
                "Control-plane database roles exist without credentials; restore the Secret"
            )
        passwords = (secrets.token_urlsafe(32), secrets.token_urlsafe(32))
        document = secret_document(*passwords)
        cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(document).encode())
    if not create and set(existing) != {MIGRATION_ROLE, RUNTIME_ROLE}:
        raise ValueError("Control-plane database roles are incomplete; run deploy first")
    if create:
        provision_roles(root, passwords, existing)
        existing = role_state(root)
    verify_roles(root, existing, passwords)
    print("Control-plane database identities validated; credentials were not rotated.", flush=True)


def execute(action: str, root: Path = cluster.ROOT) -> None:
    """Provision or verify local control-plane database identities."""
    cluster.verify_local_docker()
    cluster.verify_context(root)
    postgres.ensure_secret(root, create=False)
    for command in postgres.commands("health", root):
        cluster.run(command)
    ensure_identities(root, create=action == "deploy")
    print(f"Local control-plane database action completed: {action}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "health"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            execute(args.action)
        else:
            print("Preview: verify PostgreSQL and provision separate migration/runtime identities.")
            print("Credentials are generated only during execution and sent through private stdin.")
            print("Preview only. Add --execute to access PostgreSQL or Kubernetes.")
    except (OSError, ValueError, TypeError, yaml.YAMLError, json.JSONDecodeError) as error:
        print(f"Control-plane database error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
