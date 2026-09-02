"""Offline local chart/config checks; does not contact Kubernetes."""

import hashlib
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def validate_object_store() -> None:
    chart = ROOT / "charts/seaweedfs"
    subprocess.run(["helm", "lint", str(chart), "--strict"], check=True)
    rendered = subprocess.run(
        ["helm", "template", "seaweedfs", str(chart), "--namespace", "perf-platform"],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    objects = list(yaml.safe_load_all(rendered))
    assert len(objects) == 3
    assert {obj["kind"] for obj in objects} == {"StatefulSet", "Service", "ConfigMap"}
    assert all(obj["metadata"]["namespace"] == "perf-platform" for obj in objects)
    sts = next(obj for obj in objects if obj["kind"] == "StatefulSet")
    service = next(obj for obj in objects if obj["kind"] == "Service")
    assert sts["spec"]["replicas"] == 1
    assert sts["spec"]["persistentVolumeClaimRetentionPolicy"] == {
        "whenDeleted": "Retain",
        "whenScaled": "Retain",
    }
    pod = sts["spec"]["template"]
    assert service["spec"]["selector"] == pod["metadata"]["labels"]
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"] == [{"name": "s3", "port": 8333, "targetPort": "s3"}]
    assert pod["spec"]["nodeSelector"] == {"workload": "control-plane"}
    assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
    assert pod["spec"]["automountServiceAccountToken"] is False
    container = pod["spec"]["containers"][0]
    assert re.fullmatch(r"chrislusf/seaweedfs:4\.45@sha256:[0-9a-f]{64}", container["image"])
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    script = container["args"][0]
    for flag in [
        "-ip=127.0.0.1",
        "-ip.bind=127.0.0.1",
        "-s3.ip.bind=127.0.0.1",
        "-s3.port=9000",
        "-master.dir=/data/master",
        "-dir=/data/volumes",
        "-s3.config=/run/s3-auth/s3.json",
        "-master.telemetry=false",
        "-s3.iam=false",
        "-s3.autoCreateBucket=false",
        "-s3.allowDeleteBucketNotEmpty=false",
        "-s3.port.iceberg=0",
        "-s3.port.lance=0",
    ]:
        assert flag in script
    secret = next(volume["secret"] for volume in pod["spec"]["volumes"] if "secret" in volume)
    proxy = pod["spec"]["containers"][1]
    assert re.fullmatch(r"haproxy:[0-9.]+-alpine@sha256:[0-9a-f]{64}", proxy["image"])
    assert proxy["ports"] == [{"name": "s3", "containerPort": 8333}]
    proxy_config = next(obj for obj in objects if obj["kind"] == "ConfigMap")
    assert "server local 127.0.0.1:9000 check" in proxy_config["data"]["haproxy.cfg"]
    assert secret["secretName"] == "perfeng-s3-server-auth-v2"
    claim = sts["spec"]["volumeClaimTemplates"][0]
    assert claim["spec"]["storageClassName"] == "standard"
    assert claim["spec"]["resources"]["requests"]["storage"] == "5Gi"
    for override in [
        "image=chrislusf/seaweedfs:latest",
        "clientImage=amazon/aws-cli:latest",
        "proxyImage=haproxy:latest",
        "password=forbidden",
    ]:
        result = subprocess.run(
            ["helm", "template", "seaweedfs", str(chart), "--set-string", override],
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
    print(
        "Validated SeaweedFS storage, authentication references, and private listeners (offline)."
    )


def validate_postgres() -> None:
    chart = ROOT / "charts/postgres"
    subprocess.run(["helm", "lint", str(chart), "--strict"], check=True)
    for override in ["image=postgres:latest", "password=forbidden"]:
        rejected = subprocess.run(
            ["helm", "template", "postgres", str(chart), "--set-string", override],
            check=False,
            capture_output=True,
        )
        assert rejected.returncode != 0, "Chart must reject unpinned images and password values"
    rendered = subprocess.run(
        ["helm", "template", "postgres", str(chart), "--namespace", "perf-platform"],
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    objects = list(yaml.safe_load_all(rendered))
    assert {obj["kind"] for obj in objects} == {"StatefulSet", "Service"}
    assert len(objects) == 2  # No Helm-managed Secret or migration ConfigMap.
    assert all(obj["metadata"]["namespace"] == "perf-platform" for obj in objects)
    sts = next(obj for obj in objects if obj["kind"] == "StatefulSet")
    service = next(obj for obj in objects if obj["kind"] == "Service")
    assert sts["spec"]["replicas"] == 1
    assert sts["spec"]["serviceName"] == service["metadata"]["name"]
    assert sts["spec"]["persistentVolumeClaimRetentionPolicy"] == {
        "whenDeleted": "Retain",
        "whenScaled": "Retain",
    }
    pod = sts["spec"]["template"]
    assert service["spec"]["selector"] == pod["metadata"]["labels"]
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["clusterIP"] == "None"
    assert pod["spec"]["nodeSelector"] == {"workload": "control-plane"}
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
    assert pod["spec"]["securityContext"]["fsGroup"] == 999
    container = pod["spec"]["containers"][0]
    assert re.fullmatch(r"postgres:17\.11-bookworm@sha256:[0-9a-f]{64}", container["image"])
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    env = {entry["name"]: entry["value"] for entry in container["env"]}
    assert env["POSTGRES_PASSWORD_FILE"] == "/run/postgres-auth/password"
    assert "POSTGRES_PASSWORD" not in env
    assert env["POSTGRES_HOST_AUTH_METHOD"] == "scram-sha-256"
    assert env["PGDATA"] == "/var/lib/postgresql/data/pgdata"
    volumes = {volume["name"]: volume for volume in pod["spec"]["volumes"]}
    assert volumes["auth"]["secret"]["secretName"] == "perfeng-postgres-auth"
    for probe in ["startupProbe", "readinessProbe", "livenessProbe"]:
        assert container[probe]["exec"]["command"][0] == "pg_isready"
    claim = sts["spec"]["volumeClaimTemplates"][0]
    assert claim["spec"]["storageClassName"] == "standard"
    assert claim["spec"]["resources"]["requests"]["storage"] == "2Gi"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    print("Validated local PostgreSQL chart, Secret references, and retained PVC (offline).")


def validate() -> None:
    config = yaml.safe_load((ROOT / "local/kind/cluster-config.yaml").read_text())
    assert config["name"] == "perfeng-local"
    assert config["networking"]["apiServerAddress"] == "127.0.0.1"
    assert len(config["nodes"]) == 3
    assert [node["role"] for node in config["nodes"]] == ["control-plane", "worker", "worker"]
    assert {node["labels"]["workload"] for node in config["nodes"]} == {
        "control-plane",
        "performance-generator",
        "sut",
    }
    assert all("@sha256:" in node["image"] for node in config["nodes"])
    namespaces = list(yaml.safe_load_all((ROOT / "local/namespaces.yaml").read_text()))
    assert {item["metadata"]["name"] for item in namespaces} == {
        "perf-platform",
        "perf-generators",
        "perf-sut",
        "monitoring",
    }
    subprocess.run(["helm", "lint", str(ROOT / "charts/sample-sut"), "--strict"], check=True)
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "sample-sut",
            str(ROOT / "charts/sample-sut"),
            "--namespace",
            "perf-sut",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    objects = list(yaml.safe_load_all(rendered))
    assert {obj["kind"] for obj in objects} == {"Deployment", "Service", "ConfigMap"}
    assert all(obj["metadata"]["namespace"] == "perf-sut" for obj in objects)
    deployment = next(obj for obj in objects if obj["kind"] == "Deployment")
    service = next(obj for obj in objects if obj["kind"] == "Service")
    pod = deployment["spec"]["template"]
    assert service["spec"]["selector"] == pod["metadata"]["labels"]
    assert pod["spec"]["nodeSelector"] == {"workload": "sut"}
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
    container = pod["spec"]["containers"][0]
    assert re.fullmatch(
        r"python:3\.12\.[0-9]+-slim-bookworm@sha256:[0-9a-f]{64}", container["image"]
    )
    assert container["command"] == ["python", "-B", "-u", "/app/server.py"]
    source = (ROOT / "charts/sample-sut/files/server.py").read_bytes()
    assert pod["metadata"]["annotations"]["checksum/api"] == hashlib.sha256(source).hexdigest()
    configmap = next(obj for obj in objects if obj["kind"] == "ConfigMap")
    assert configmap["data"]["server.py"].strip() == source.decode("utf-8").strip()
    assert container["readinessProbe"]["httpGet"]["path"] == "/healthz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert {entry["name"]: entry["value"] for entry in container["env"]} == {
        "FIXTURE_HOST": "0.0.0.0",
        "FIXTURE_MODE": "ok",
    }
    for override in ["image=python:latest", "fixtureMode=typo"]:
        rejected = subprocess.run(
            [
                "helm",
                "template",
                "sample-sut",
                str(ROOT / "charts/sample-sut"),
                "--set-string",
                override,
            ],
            capture_output=True,
            check=False,
        )
        assert rejected.returncode != 0
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["readinessProbe"]["httpGet"]["port"] == "http"
    assert container["resources"]["requests"]["cpu"] == "100m"
    assert service["spec"]["ports"][0]["targetPort"] == "http"
    print("Validated kind layout, four namespaces, and rendered sample SUT chart (offline).")
    validate_postgres()
    validate_object_store()


if __name__ == "__main__":
    validate()
