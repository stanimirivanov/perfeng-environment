"""Opt-in functional check against sibling k6 sources; loopback only, not a benchmark."""

import argparse
import importlib.util
import json
import os
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "charts/sample-sut/files/server.py"


def load_api():
    spec = importlib.util.spec_from_file_location("sample_api_fixture", SOURCE)
    if spec is None or spec.loader is None:
        raise ValueError("Sample API source could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def fixture(mode="ok"):
    with load_api().create_server("127.0.0.1", 0, mode) as server:
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=5)


def check(runner: Path) -> None:
    runner = runner.resolve()
    cases = [
        ("checkout", "ok"),
        ("search", "ok"),
        ("account", "ok"),
        ("checkout", "cart-failure"),
        ("checkout", "missing-order"),
        ("checkout", "malformed-order"),
        ("search", "search-failure"),
        ("account", "preferences-failure"),
    ]
    for scenario, _ in cases:
        if not (runner / "tests" / scenario / "scenario.js").is_file():
            raise ValueError("Runner repository is missing the expected scenarios")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("K6_")
        and key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    env.update(AUTH_TOKEN="", API_VERSION="v1", THINK_TIME="0", NO_PROXY="127.0.0.1,localhost")
    version = subprocess.run(
        ["k6", "version"], capture_output=True, check=True, timeout=10, env=env
    )
    tokens = version.stdout.split()
    if len(tokens) < 2 or tokens[1] != b"v2.2.0":
        raise ValueError("Functional compatibility check requires k6 v2.2.0")
    with tempfile.TemporaryDirectory(prefix="perfeng-api-check-") as directory:
        for scenario, mode in cases:
            with fixture(mode) as port:
                output = Path(directory) / f"{scenario}-{mode}.json"
                result = subprocess.run(
                    [
                        "k6",
                        "run",
                        "--no-usage-report",
                        "--config",
                        str(ROOT / "tests/fixtures/sample-api-k6.json"),
                        "--summary-export",
                        str(output),
                        f"tests/{scenario}/scenario.js",
                    ],
                    cwd=runner,
                    env={**env, "BASE_URL": f"http://127.0.0.1:{port}"},
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                expected = 0 if mode == "ok" else 99
                if result.returncode != expected:
                    raise ValueError(
                        f"{scenario}/{mode}: expected exit {expected}, got {result.returncode}"
                    )
                summary = json.loads(output.read_text(encoding="utf-8"))
                rate = summary["metrics"]["biz_transaction_error_rate"]["value"]
                if rate != (0 if mode == "ok" else 1):
                    raise ValueError(f"{scenario}/{mode}: unexpected transaction error rate")
                print(f"Passed {scenario}/{mode}: exit {expected}, transaction error rate {rate}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-repo", type=Path, required=True)
    args = parser.parse_args()
    try:
        check(args.runner_repo)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Sample API check failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
