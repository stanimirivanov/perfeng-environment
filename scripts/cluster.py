"""Bootstrap Perfeng namespaces on the selected Kubernetes substrate."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
NAME = "perfeng-local"
CONTEXT = f"kind-{NAME}"
PROGRESS_INTERVAL = 10
ACTIONS = ("bootstrap",)


def run(command: list[str], *, input_data: bytes | None = None) -> str:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"KUBECONFIG", "KUBERNETES_MASTER"} and not key.startswith("HELM_KUBE")
    }
    # Only show the executable, never arguments/output that may contain credentials.
    label = Path(command[0]).stem
    print(f"Starting {label} command...", flush=True)
    started = time.monotonic()
    # Capture bytes: Windows' locale decoder can crash a subprocess reader thread
    # on kind's UTF-8 progress symbols, even when the child succeeds.
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_data is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**environment, "KIND_EXPERIMENTAL_PROVIDER": "docker"},
    ) as process:
        while True:
            try:
                stdout, _ = process.communicate(input=input_data, timeout=PROGRESS_INTERVAL)
                break
            except subprocess.TimeoutExpired:
                # communicate() retains pending input; never submit credentials twice.
                input_data = None
                elapsed = int(time.monotonic() - started)
                print(f"Still running {label} command ({elapsed}s elapsed)...", flush=True)
    if process.returncode:
        # Never echo a kubeconfig or captured authentication data.
        raise ValueError(f"{label} failed (exit {process.returncode}); inspect the local cluster")
    try:
        output = stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{label} returned invalid UTF-8; output withheld") from None
    print(f"Completed {label} command.", flush=True)
    return output


def kubeconfig(root: Path) -> Path:
    """Return the dedicated local kubeconfig without accepting redirection."""
    path = root / ".local" / "perfeng-local.kubeconfig"
    if path.parent.resolve() != path.parent or path.resolve() != path or path.is_symlink():
        raise ValueError("Redirected local state is not allowed")
    return path


def verify_local_docker() -> None:
    """Require a local Docker socket rather than a remote engine."""
    override = os.environ.get("DOCKER_HOST")
    if override and not override.startswith(("unix://", "npipe://")):
        raise ValueError("Remote/TCP Docker endpoints are not allowed by local cluster tooling")
    endpoint = json.loads(
        run(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"])
    )
    if not isinstance(endpoint, str) or not endpoint.startswith(("unix://", "npipe://")):
        raise ValueError("Select a local Docker socket context before continuing")


def verify_context(root: Path) -> None:
    """Require the dedicated kubeconfig to match the named provider cluster."""
    config = kubeconfig(root)
    expected = yaml.safe_load(run(["kind", "get", "kubeconfig", "--name", NAME]))
    actual = yaml.safe_load(config.read_text(encoding="utf-8"))
    if not isinstance(expected, dict) or not expected.get("clusters") or actual != expected:
        raise ValueError("Local kubeconfig does not match the named kind cluster; refusing access")


def bootstrap_command(root: Path = ROOT) -> list[str]:
    """Build the exact consumer namespace bootstrap command."""
    return [
        "kubectl",
        "--kubeconfig",
        str(kubeconfig(root)),
        "--context",
        CONTEXT,
        "apply",
        "-f",
        str(root / "gitops/environments/local/namespaces.yaml"),
    ]


def execute(root: Path = ROOT) -> None:
    """Apply only the reviewed Perfeng namespaces to the selected substrate."""
    print("Checking kind version...", flush=True)
    version = run(["kind", "version"]).split()
    if len(version) < 2 or version[1] != "v0.31.0":
        raise ValueError("This local layout requires kind v0.31.0")
    print("Discovering local clusters...", flush=True)
    if NAME not in run(["kind", "get", "clusters"]).splitlines():
        raise ValueError("Named local cluster does not exist; run infrastructure bootstrap first")
    print("Checking local Docker endpoint and cluster credentials...", flush=True)
    verify_local_docker()
    verify_context(root)
    print("Applying declarative local namespaces...", flush=True)
    run(bootstrap_command(root))
    print("Local namespace bootstrap completed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if not args.execute:
            print(json.dumps(bootstrap_command()))
            print("Preview only. Add --execute to bootstrap the Perfeng namespaces.")
        else:
            execute()
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Local cluster error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
