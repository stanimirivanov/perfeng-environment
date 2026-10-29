"""Offline CI composition of the selected chart/package and ephemeral inputs."""

import base64
import json
import secrets
import sys
from pathlib import Path
from typing import Any

import yaml

from scripts import control_plane_release as release
from scripts import control_plane_runtime as runtime


def source_selection(source: Path) -> release.DeploymentLock:
    """Refuse a different or modified checkout before rendering its files."""
    lock = release.load_lock()
    read = release.revision_reader(source, release.DEPLOYMENT_REPOSITORY, lock.deployment_revision)
    if release.git(source, "rev-parse", "HEAD").decode().strip() != lock.deployment_revision:
        raise ValueError("CI deployment checkout differs from selection")
    if release.git(source, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("CI deployment checkout is modified")
    release.validate_deployment_source(lock, read)
    runtime.validate_package_source(lock, read)
    return lock


def pull_secret(source: dict[str, Any]) -> dict[str, Any]:
    """Clone only the existing fixture registry data under the selected fixed name."""
    assert source["type"] == "kubernetes.io/dockerconfigjson" and source["metadata"]["uid"]
    assert set(source["data"]) == {".dockerconfigjson"}
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": source["type"],
        "immutable": True,
        "metadata": {"name": "perfeng-control-plane-registry", "namespace": "perf-platform"},
        "data": source["data"],
    }


def retained_pull(original: dict[str, Any], current: dict[str, Any]) -> None:
    """Refuse replacement, credential drift or an unsafe preexisting pull Secret."""
    for obj in (original, current):
        assert obj["apiVersion"] == "v1" and obj["kind"] == "Secret"
        assert obj["metadata"]["name"] == "perfeng-control-plane-registry"
        assert obj["metadata"]["namespace"] == "perf-platform" and obj["metadata"]["uid"]
        assert obj["immutable"] is True and obj["type"] == "kubernetes.io/dockerconfigjson"
        assert set(obj["data"]) == {".dockerconfigjson"}
        assert not any(obj["metadata"].get(k) for k in ("deletionTimestamp", "ownerReferences"))
    assert {k: v for k, v in original.items() if k != "metadata"} == {
        k: v for k, v in current.items() if k != "metadata"
    }
    assert {
        k: v
        for k, v in original["metadata"].items()
        if k not in {"resourceVersion", "managedFields"}
    } == {
        k: v
        for k, v in current["metadata"].items()
        if k not in {"resourceVersion", "managedFields"}
    }


def prepare(source: Path, directory: Path) -> None:
    """Write only private fixture Secrets and non-secret reviewed Helm values."""
    lock = source_selection(source)
    application = yaml.safe_load(
        Path("gitops/environments/local/control-plane-application.yaml").read_text()
    )
    chart, package = application["spec"]["sources"]
    assert all(s["targetRevision"] == lock.deployment_revision for s in (chart, package))
    assert chart["path"] == lock.chart_path and package["path"] == lock.package_path
    values = chart["helm"]["valuesObject"]
    assert values["image"] == lock.image
    assert values["runtimeConfigMap"] == lock.runtime_config_map
    assert values["registryConfigMap"] == lock.registry_config_map
    (directory / "server-values.yaml").write_text(yaml.safe_dump(values), encoding="utf-8")
    inputs = json.loads((directory / "server-input-secrets.json").read_text())
    stored = {item["metadata"]["name"]: item for item in inputs["items"]}

    def data(name: str, key: str) -> str:
        return base64.b64decode(stored[name]["data"][key], validate=True).decode()

    credentials = {
        "PERFENG_DATABASE_URL": data("perfeng-control-plane-database", "runtime-url"),
        "AWS_ACCESS_KEY_ID": data("perfeng-s3-auth", "access-key"),
        "AWS_SECRET_ACCESS_KEY": data("perfeng-s3-auth", "secret-key"),
    }
    documents = []
    for name, content in (
        (values["runtimeSecret"], credentials),
        (values["authenticationSecret"], runtime.authentication(secrets.token_urlsafe(48))),
    ):
        documents.append(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "type": "Opaque",
                "immutable": True,
                "metadata": {"name": name, "namespace": "perf-platform"},
                "stringData": content,
            }
        )
    documents.append(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "type": "kubernetes.io/dockerconfigjson",
            "immutable": True,
            "metadata": {"name": values["imagePullSecret"], "namespace": "perf-platform"},
            "data": stored["migration-ci-registry"]["data"],
        }
    )
    if (directory / "schema-install.pull-before.json").exists():
        original = json.loads((directory / "schema-install.pull-before.json").read_text())
        current = json.loads((directory / "server-pull-current.json").read_text())
        retained_pull(original, current)
        assert documents[-1]["data"] == current["data"]
        documents.pop()  # The verified immutable Secret is retained, never created twice.
    else:
        assert not (directory / "server-pull-current.json").exists()
    (directory / "server-secrets.json").write_text(
        json.dumps({"apiVersion": "v1", "kind": "List", "items": documents}), encoding="utf-8"
    )


def rendered(source: Path, directory: Path) -> None:
    """Check the rendered inventory and exact immutable package bytes offline."""
    lock = source_selection(source)
    objects: list[dict[str, Any]] = [
        item
        for name in ("server-chart.yaml", "server-package.yaml")
        for item in yaml.safe_load_all((directory / name).read_text())
        if item
    ]
    inventory = {(o["kind"], o["metadata"]["namespace"], o["metadata"]["name"]) for o in objects}
    assert len(objects) == len(inventory) == 7
    assert inventory == {
        ("Deployment", "perf-platform", "control-plane"),
        ("Service", "perf-platform", "control-plane"),
        ("ServiceAccount", "perf-platform", "control-plane"),
        ("Role", "perf-generators", "control-plane-job-controller"),
        ("RoleBinding", "perf-generators", "control-plane-job-controller"),
        ("ConfigMap", "perf-platform", lock.runtime_config_map),
        ("ConfigMap", "perf-platform", lock.registry_config_map),
    }
    expected = {
        lock.runtime_config_map: runtime.runtime_configuration(lock),
        lock.registry_config_map: runtime.registry_files(lock, source),
    }
    for item in objects:
        if item["kind"] == "ConfigMap":
            assert item["immutable"] is True and item["data"] == expected[item["metadata"]["name"]]
        if item["kind"] == "Deployment":
            worker = item["spec"]["template"]["spec"]["containers"]
            assert len(worker) == 1 and worker[0]["image"] == lock.image
            assert "command" not in worker[0] and "args" not in worker[0]
    (directory / "server-rendered.json").write_text(
        json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}), encoding="utf-8"
    )


def observed(workflow_path: Path, expected_uid: str) -> None:
    """Require actual native projections to match the selected healthy server."""
    w = json.loads(workflow_path.read_text())
    assert w["status"]["phase"] == "Succeeded"
    o = {
        p["name"]: json.loads(p["value"])
        for p in w["status"]["nodes"][w["metadata"]["name"]]["outputs"]["parameters"]
    }
    lock = release.load_lock()
    d, s = o["deployment"], o["service"]
    assert d["uid"] == expected_uid and d["generation"] == d["observedGeneration"]
    assert (
        all(d[k] == 1 for k in ("desired", "replicas", "ready", "updated", "available"))
        and d["unavailable"] == 0
    )
    assert d["images"] == [lock.image]
    assert d["runtimeConfigMaps"] == [lock.runtime_config_map]
    assert d["registryConfigMaps"] == [lock.registry_config_map]
    assert d["runtimeSecrets"] == ["perfeng-control-plane-runtime"]
    assert d["authenticationSecrets"] == ["perfeng-control-plane-authentication"]
    assert d["imagePullSecrets"] == ["perfeng-control-plane-registry"]
    assert s["uid"] and s["type"] == "ClusterIP"
    assert s["selector"] == {
        "app.kubernetes.io/name": "perfeng-control-plane",
        "app.kubernetes.io/instance": "control-plane",
    }
    assert (
        len(s["ports"]) == 1
        and s["ports"][0]["name"] == "http"
        and s["ports"][0]["port"] == 8080
        and s["ports"][0]["targetPort"] == "http"
    )


if __name__ == "__main__":
    mode, first, second = sys.argv[1:]
    if mode == "observed":
        observed(Path(first), second)
    else:
        {"prepare": prepare, "rendered": rendered}[mode](Path(first), Path(second))
