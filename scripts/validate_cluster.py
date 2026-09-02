"""Offline local chart/config checks; does not contact Kubernetes."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    validate()
