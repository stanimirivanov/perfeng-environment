"""Disposable pinned artifact/database preparation; no running-product acceptance."""

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import NotRequired, TypedDict

INPUTS = Path(__file__).with_name("inputs.json")
SCHEMAS = {"argus": "argus_catalog", "perfeng-control-plane": "perfeng_control"}
LABEL = "dev.perfeng.joint-fixture"


class ProductEvidence(TypedDict):
    sourceRevision: str
    artifacts: dict[str, dict[str, str]]
    database: NotRequired[str]


class Evidence(TypedDict):
    servingReady: NotRequired[bool]
    changeIngress: NotRequired[dict[str, str]]
    checkoutSmoke: NotRequired[dict[str, object]]
    checkoutBaseline: NotRequired[dict[str, str]]
    jointAcceptance: NotRequired[dict[str, str]]
    servingClusterUID: NotRequired[str]
    servingCleanup: NotRequired[str]
    schemaVersion: int
    products: dict[str, ProductEvidence]
    preparationReady: bool
    productAcceptance: str
    cleanup: str
    resources: NotRequired[list[dict[str, str]]]


def inputs(path=INPUTS):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate fixture input")
        return result

    value = json.loads(path.read_text(), object_pairs_hook=unique)
    if (
        not isinstance(value, dict)
        or set(value) != {"schemaVersion", "postgres", "products", "go"}
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or not isinstance(value["postgres"], str)
        or not re.fullmatch(r"postgres@sha256:[a-f0-9]{64}", value["postgres"])
        or not isinstance(value["products"], dict)
        or set(value["products"]) != set(SCHEMAS)
        or any(
            not isinstance(pin, str) or not re.fullmatch(r"[a-f0-9]{40}", pin)
            for pin in value["products"].values()
        )
        or not isinstance(value["go"], str)
        or not re.fullmatch(r"go1\.\d+\.\d+", value["go"])
    ):
        raise ValueError("invalid immutable fixture inputs")
    return value


class Fixture:
    def __init__(self, selection: dict, root: Path):
        if (
            root.is_symlink()
            or not root.is_dir()
            or root.stat().st_mode & 0o777 != 0o700
            or root.stat().st_uid != os.getuid()
        ):
            raise ValueError("fixture requires a private owned mode-0700 run directory")
        self.selection, self.root = selection, root
        self.run_id = "joint-" + secrets.token_hex(12)
        self.resources = []
        self.deadline = time.monotonic() + 900
        self.evidence: Evidence = {
            "schemaVersion": 1,
            "products": {},
            "preparationReady": False,
            "productAcceptance": "not-run",
            "cleanup": "pending",
        }

    def command(self, args, *, env=None, data=None, timeout=30, optional=False, cleanup=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 and not cleanup:
            raise ValueError("fixture deadline exceeded")
        try:
            result = subprocess.run(
                args,
                input=data,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=timeout if cleanup else min(timeout, remaining),
            )
        except (OSError, subprocess.SubprocessError):
            raise ValueError("fixture command unavailable or timed out") from None
        if result.returncode and not optional:
            raise ValueError("fixture command failed; vendor output withheld")
        return result

    def docker(self, *args, **kwargs):
        return self.command(["docker", *args], **kwargs)

    def create(self, kind, suffix, args, env=None):
        name = self.run_id + "-" + suffix
        self.resources.append((kind, name))  # Recover even a lost create acknowledgement.
        if kind == "network":
            self.docker(
                "network", "create", "--internal", "--label", f"{LABEL}={self.run_id}", name
            )
        else:
            self.docker(
                "run",
                "-d",
                "--name",
                name,
                "--label",
                f"{LABEL}={self.run_id}",
                "--network",
                self.run_id + "-net",
                "--cpus=1",
                "--memory=512m",
                "--pids-limit=128",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,size=16m",
                "--tmpfs",
                "/var/run/postgresql:rw,size=16m",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=256m",
                *args,
                self.selection["postgres"],
                env=env,
            )
        return name

    def inspect(self, kind, name, *, cleanup=False):
        result = self.docker("inspect", "--type", kind, name, optional=cleanup, cleanup=cleanup)
        if result.returncode:
            raise ValueError("fixture resource inspection failed")
        record = json.loads(result.stdout)
        if not isinstance(record, list) or len(record) != 1 or not isinstance(record[0], dict):
            raise ValueError("ambiguous fixture resource")
        item = record[0]
        config = item if kind == "network" else item.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        actual_name, identity = item.get("Name"), item.get("Id")
        if (
            not isinstance(labels, dict)
            or labels.get(LABEL) != self.run_id
            or not isinstance(actual_name, str)
            or actual_name.lstrip("/") != name
        ):
            raise ValueError("foreign fixture resource refused")
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("invalid fixture resource identity")
        return item

    def cleanup(self) -> bool:
        ok = True
        for kind, name in reversed(self.resources):
            try:
                item = self.inspect(kind, name, cleanup=True)
                args = (
                    ("rm", "-f", item["Id"])
                    if kind == "container"
                    else ("network", "rm", item["Id"])
                )
                removed = self.docker(*args, timeout=30, optional=True, cleanup=True)
                if removed.returncode:
                    ok = False
                # A healthy daemon must confirm absence; an inspect error is ambiguous.
                listing = ("container", "ls", "-a") if kind == "container" else ("network", "ls")
                absent = self.docker(
                    *listing,
                    "--filter",
                    "id=" + item["Id"],
                    "--format",
                    "{{.ID}}",
                    optional=True,
                    cleanup=True,
                )
                if absent.returncode or absent.stdout.strip():
                    ok = False
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                ok = False
        self.evidence["resources"] = [{"kind": kind, "name": name} for kind, name in self.resources]
        self.evidence["cleanup"] = "passed" if ok else "unresolved"
        return ok

    def artifacts(self, product: str, revision: str) -> None:
        source = self.root / product
        url = f"https://github.com/stanimirivanov/{product}.git"
        self.command(["git", "clone", "--depth=1", "--no-checkout", url, str(source)], timeout=90)
        self.command(
            ["git", "-C", str(source), "fetch", "--depth=1", "origin", revision], timeout=90
        )
        self.command(["git", "-C", str(source), "checkout", "--detach", revision])
        actual = self.command(["git", "-C", str(source), "rev-parse", "HEAD"]).stdout.strip()
        if (
            actual != revision
            or f"go {self.selection['go'][2:]}\n" not in (source / "go.mod").read_text()
        ):
            raise ValueError("source or toolchain pin mismatch")
        binaries = {}
        env = {
            "PATH": os.environ["PATH"],
            "GOTOOLCHAIN": self.selection["go"],
            "GOCACHE": str(self.root / "go-cache"),
            "GOPATH": str(self.root / "go"),
            "GOMAXPROCS": "2",
            "CGO_ENABLED": "0",
            "GOOS": "linux",
            "GOARCH": "amd64",
        }
        targets = ("migrate", "control-plane")
        if product == "argus":
            targets += (
                "catalog",
                "performance-catalog",
                "select-performance",
                "performance-requests",
                "performance-evidence",
            )
        for target in targets:
            binary = self.root / (product + "-" + target)
            self.command(
                [
                    "go",
                    "-C",
                    str(source),
                    "build",
                    "-mod=readonly",
                    "-p=2",
                    "-trimpath",
                    "-buildvcs=false",
                    "-o",
                    str(binary),
                    "./cmd/" + target,
                ],
                env=env,
                timeout=300,
            )
            if binary.stat().st_size > 128 << 20:
                raise ValueError("fixture artifact exceeds size bound")
            binaries[target] = {"sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        self.evidence["products"][product] = {"sourceRevision": revision, "artifacts": binaries}

    def database(self, product: str) -> None:
        database = "argus" if product == "argus" else "perfeng"
        password, runtime_password = secrets.token_hex(24), secrets.token_hex(24)
        env = {**os.environ, "POSTGRES_PASSWORD": password}
        name = self.create(
            "container",
            database,
            [
                "--mount",
                f"type=bind,src={self.root / (product + '-migrate')},dst=/fixture-migrate,readonly",
                "-e",
                "POSTGRES_PASSWORD",
                "-e",
                f"POSTGRES_USER={database}_migrator",
                "-e",
                f"POSTGRES_DB={database}",
            ],
            env,
        )
        for _ in range(60):
            ready = self.docker(
                "exec",
                name,
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                database + "_migrator",
                optional=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise ValueError("database readiness deadline exceeded")
        self.inspect("container", name)
        base = f"@127.0.0.1:5432/{database}?sslmode=disable"
        migration_url = f"postgres://{database}_migrator:{password}" + base
        runtime_url = f"postgres://{database}_runtime:{runtime_password}@{name}:5432/{database}?sslmode=disable"
        variable = "ARGUS_DATABASE_URL" if product == "argus" else "PERFENG_DATABASE_URL"
        self.docker(
            "exec",
            "-e",
            variable,
            name,
            "/fixture-migrate",
            env={**os.environ, variable: migration_url},
        )
        schema = SCHEMAS[product]
        sql = f"""CREATE ROLE {database}_runtime LOGIN PASSWORD '{runtime_password}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE ALL ON DATABASE {database} FROM PUBLIC;
GRANT CONNECT ON DATABASE {database} TO {database}_runtime;
GRANT USAGE ON SCHEMA {schema} TO {database}_runtime;
GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA {schema} TO {database}_runtime;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {database}_runtime;
"""
        self.docker(
            "exec",
            "-i",
            name,
            "psql",
            "-U",
            database + "_migrator",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            data=sql,
        )
        credentials = self.root / (database + "-credentials.json")
        with credentials.open("x", encoding="utf-8") as output:
            os.chmod(credentials, 0o600)
            json.dump({"migration": migration_url, "runtime": runtime_url}, output)
        result = self.docker(
            "exec",
            "-e",
            "PGPASSWORD",
            name,
            "psql",
            "-h",
            "127.0.0.1",
            "-U",
            database + "_runtime",
            "-d",
            database,
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f"SELECT current_user, current_database(), rolsuper, rolcreatedb, rolcreaterole, "
            f"has_schema_privilege(current_user, '{schema}', 'USAGE'), "
            f"has_schema_privilege(current_user, '{schema}', 'CREATE') "
            "FROM pg_roles WHERE rolname = current_user",
            env={**os.environ, "PGPASSWORD": runtime_password},
        )
        if result.stdout.strip() != f"{database}_runtime|{database}|f|f|f|t|f":
            raise ValueError("fixture runtime authority mismatch")
        self.evidence["products"][product]["database"] = "migrated-runtime-verified"

    @contextmanager
    def prepared(self):
        """Keep prepared resources available to a caller; always attempt owned cleanup."""
        try:
            for product, revision in self.selection["products"].items():
                print(f"Building pinned {product} candidates", flush=True)
                self.artifacts(product, revision)
            self.docker("pull", self.selection["postgres"], timeout=90)
            self.create("network", "net", [])
            for product in self.selection["products"]:
                print(f"Preparing isolated {product} database", flush=True)
                self.database(product)
            self.evidence["preparationReady"] = True
            yield self
        finally:
            cleaned = self.cleanup()
            (self.root / "evidence.json").write_text(json.dumps(self.evidence, indent=2) + "\n")
            if not cleaned:
                raise ValueError("fixture cleanup unresolved; inspect private run state")

    def run(self) -> None:
        with self.prepared():
            pass


def interrupt(*_):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    selection = inputs()
    if not args.execute:
        print("Pinned joint fixture inputs valid; no resources created.")
        return
    root = Path(tempfile.mkdtemp(prefix="argus-perfeng-"))
    os.chmod(root, 0o700)
    print(f"Private fixture run directory: {root}")
    signal.signal(signal.SIGTERM, interrupt)
    try:
        Fixture(selection, root).run()
    except (ValueError, OSError, KeyError, TypeError, KeyboardInterrupt) as error:
        raise SystemExit(
            "Joint fixture failed; private run state retained, no product acceptance claimed."
        ) from error
    print("Artifact/database preparation passed and resources removed; product acceptance not run.")


if __name__ == "__main__":
    main()
