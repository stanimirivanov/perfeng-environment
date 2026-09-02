"""Offline local chart/config checks; does not contact Kubernetes."""

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


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
    assert {obj["kind"] for obj in objects} == {"Deployment", "Service"}
    assert all(obj["metadata"]["namespace"] == "perf-sut" for obj in objects)
    deployment = next(obj for obj in objects if obj["kind"] == "Deployment")
    service = next(obj for obj in objects if obj["kind"] == "Service")
    pod = deployment["spec"]["template"]
    assert service["spec"]["selector"] == pod["metadata"]["labels"]
    assert pod["spec"]["nodeSelector"] == {"workload": "sut"}
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["securityContext"]["runAsNonRoot"] is True
    container = pod["spec"]["containers"][0]
    assert container["image"] == "hashicorp/http-echo:1.0.0"
    assert container["args"] == ["-text=PerfEng SUT", "-listen=:8080"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["readinessProbe"]["httpGet"]["port"] == "http"
    assert container["resources"]["requests"]["cpu"] == "100m"
    assert service["spec"]["ports"][0]["targetPort"] == "http"
    print("Validated kind layout, four namespaces, and rendered sample SUT chart (offline).")
    validate_postgres()


if __name__ == "__main__":
    validate()
