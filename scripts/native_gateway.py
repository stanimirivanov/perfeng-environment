"""Manage verified setup, read-only diagnosis and one bounded foreground native session."""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

from scripts import cluster
from scripts import native_connectivity as connectivity
from scripts import native_gateway_doctor as doctor
from scripts import native_gateway_host as host
from scripts import native_gateway_tool as tool
from scripts.native_gateway_processes import ProcessScope


def wait_port(scope: ProcessScope, port: int, deadline: float) -> None:
    """Wait for an owned child's endpoint without treating an open socket as semantic readiness."""
    while time.monotonic() < deadline:
        scope.check()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise tool.GatewayError(
        "STARTUP_TIMEOUT",
        f"Loopback port {port} was not ready within its deadline",
        "inspect the named stage; retry after task native:doctor",
    )


def up(root: Path = tool.ROOT) -> None:
    """Own four foreground children for one hour; always clean up partial starts and interruptions."""
    print("[1/6] Checking all native connectivity prerequisites...", flush=True)
    if not doctor.report(doctor.collect(root)):
        raise tool.GatewayError(
            "PREFLIGHT",
            "Native prerequisites failed",
            "resolve doctor FAIL entries; no children were started",
        )
    binary = tool.installed(root)
    host.private_permissions(root)
    print("[2/6] Verifying the exact image-backed target Pod...", flush=True)
    with cluster.command_deadline(45):
        pod = connectivity.verify_forward("forward-target", root)
    scope = ProcessScope()
    deadline = time.monotonic() + connectivity.SESSION_SECONDS
    try:
        print("[3/6] Starting API, S3 and exact-Pod tunnels...", flush=True)
        for action in connectivity.FORWARDS:
            command = connectivity.command(action, root)
            if action == "forward-target":
                command[-2] = f"pod/{pod}"
            scope.start(action, command, root)
            port = int(connectivity.FORWARDS[action][1].split(":")[0])
            wait_port(scope, port, min(deadline, time.monotonic() + 30))
            print(f"PASS {action} on 127.0.0.1:{port}", flush=True)
        print(
            "[4/6] Starting selected Caddy; no certificate trust will be installed...", flush=True
        )
        command = connectivity.command("gateway", root)
        command[0] = str(binary)
        scope.start("gateway", command, root)
        for port in (8443, 9443, 10443):
            wait_port(scope, port, min(deadline, time.monotonic() + 30))
        print(
            "[5/6] Verifying CA-backed TLS, readiness, exact content and anonymous S3 denial...",
            flush=True,
        )
        ready_deadline = min(deadline, time.monotonic() + 60)
        while True:
            scope.check()
            try:
                with cluster.command_deadline(30):
                    connectivity.check(host.ca_file(root), root, expected_pod=pod)
                break
            except (ValueError, OSError) as error:
                if time.monotonic() >= ready_deadline:
                    if isinstance(error, tool.GatewayError):
                        raise error from None
                    raise tool.GatewayError(
                        "ENDPOINT_READINESS",
                        "Trusted endpoint checks did not pass",
                        "inspect API/S3/target health and retained CA; no TLS bypass or automatic repair",
                    ) from None
                time.sleep(1)
        print(f"Public CA SHA-256: {host.fingerprint(root)}", flush=True)
        print(
            "[6/6] CONNECTIVITY READY; browser/Node trust and worker execution are separate gates.",
            flush=True,
        )
        print(json.dumps(connectivity.ORIGINS), flush=True)
        print(
            "Keep this terminal open. Ctrl+C stops all four owned children; maximum session is one hour. Use task native:check from another terminal.",
            flush=True,
        )
        while time.monotonic() < deadline:
            scope.check()
            time.sleep(1)
        raise tool.GatewayError(
            "SESSION_EXPIRED",
            "The one-hour native session expired",
            "rerun task native:up when needed; CA and cluster state were retained",
        )
    finally:
        scope.close()
        print(
            "Owned native children stopped; workloads, credentials, local builds and CA retained.",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    """Preview without external access; setup, doctor, permissions and up require explicit execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "doctor", "permissions", "up"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        print(
            f"Preview native {args.action}: no processes, network, files, permissions or trust were changed. Add --execute."
        )
        return 0
    try:
        if args.action == "setup":
            tool.setup()
        elif args.action == "doctor":
            return 0 if doctor.report(doctor.collect(tool.ROOT)) else 1
        elif args.action == "permissions":
            host.secure_permissions(tool.ROOT)
        else:
            up()
    except tool.GatewayError as error:
        print(f"Native gateway error: {error}", flush=True)
        return 1
    except (OSError, ValueError) as error:
        print(
            f"Native gateway error: local operation failed ({type(error).__name__}); inspect the named stage. No vendor output was printed.",
            flush=True,
        )
        return 1
    except KeyboardInterrupt:
        print("Native session stopped by the operator; retained state unchanged.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
