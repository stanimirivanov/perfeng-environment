"""CI-only request publication and compare-and-delete of one run-owned Git ref."""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

REPOSITORY = "stanimirivanov/perfeng-environment"
URL = f"https://github.com/{REPOSITORY}.git"
REQUEST_PATH = "deployments/release-requests/ci-parent-preflight-v1.json"


def owned_ref() -> str:
    """Reject untrusted execution before contacting GitHub."""
    expected = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPOSITORY,
    }
    if any(os.environ.get(key) != value for key, value in expected.items()):
        raise ValueError("requires trusted main on GitHub-hosted Linux")
    if os.environ.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise ValueError("untrusted event")
    run, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not all(re.fullmatch(r"[1-9][0-9]{0,19}", item) for item in (run, attempt)):
        raise ValueError("invalid run identity")
    return f"refs/heads/ci/preflight/{run}-{attempt}"


def api(path: str, payload: dict | None = None) -> Any:
    """Bound requests, never retry ambiguous writes or print credential-bearing errors."""
    command = ["gh", "api", "--hostname", "github.com", f"repos/{REPOSITORY}/{path}"]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
        env={**os.environ, "GH_TOKEN": os.environ["GITHUB_TOKEN"]},
    )
    if result.returncode:
        raise ValueError("GitHub request failed; inspect owned-ref journal before retrying")
    return json.loads(result.stdout)


def sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("invalid Git object identity")
    return value


def publish(directory: Path, cluster_uid: str) -> None:
    ref = owned_ref()
    revision = sha(os.environ.get("GITHUB_SHA"))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=20
    ).stdout.strip()
    if head != revision:
        raise ValueError("checkout differs from trusted event commit")
    journal = directory / "request-ref.json"
    if journal.exists():
        raise ValueError("publication journal already exists")
    request = json.loads(
        Path("deployments/release-requests/ci-preflight-fixture-v1.json").read_text()
    )
    request["environment"]["clusterUID"] = cluster_uid
    request["selection"]["revision"] = revision
    request["selection"]["lockDigests"] = {
        path: "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for path in request["selection"]["lockDigests"]
    }
    Draft202012Validator(
        json.loads(Path("deployments/release-request-v1.schema.json").read_text())
    ).validate(request)
    raw = (json.dumps(request, indent=2) + "\n").encode()
    (directory / "parent-request.json").write_bytes(raw)
    # Git Data API avoids changing the checked-out branch, index or credentials.
    blob = sha(
        api("git/blobs", {"content": base64.b64encode(raw).decode(), "encoding": "base64"})["sha"]
    )
    base = api(f"git/commits/{revision}")
    tree = sha(
        api(
            "git/trees",
            {
                "base_tree": sha(base["tree"]["sha"]),
                "tree": [{"path": REQUEST_PATH, "mode": "100644", "type": "blob", "sha": blob}],
            },
        )["sha"]
    )
    commit = sha(
        api(
            "git/commits",
            {
                "message": "CI-only disposable preflight request",
                "tree": tree,
                "parents": [revision],
            },
        )["sha"]
    )
    # Persist intent BEFORE ref creation: a lost API response is recoverable.
    journal.write_text(json.dumps({"ref": ref, "commit": commit}) + "\n")
    api("git/refs", {"ref": ref, "sha": commit})  # create-only; conflicts fail closed
    parameters = {
        "request-revision": commit,
        "request-path": REQUEST_PATH,
        "request-sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "selection-revision": revision,
    }
    (directory / "parent-parameters.json").write_text(json.dumps(parameters) + "\n")


def cleanup(directory: Path) -> None:
    ref = owned_ref()
    journal = json.loads((directory / "request-ref.json").read_text())
    commit = sha(journal["commit"])
    if journal["ref"] != ref:
        raise ValueError("ref is not owned by this run")
    # An atomic Git receive-pack lease is essential: GET then REST DELETE has a race.
    # This is a conditional deletion, never a forced update of an existing branch.
    token = os.environ["GITHUB_TOKEN"]
    env = os.environ.copy()
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    if not 0 <= count <= 100:
        raise ValueError("invalid process Git configuration count")
    # Preserve runner-provided Git safety settings without changing global config.
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    env[f"GIT_CONFIG_KEY_{count}"] = "http.https://github.com/.extraheader"
    env[f"GIT_CONFIG_VALUE_{count}"] = (
        "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    )
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", "push", "--porcelain", f"--force-with-lease={ref}:{commit}", URL, f":{ref}"],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if result.returncode:
        # A failed/lost create response may mean no ref was ever created. Only an
        # authenticated exact-name read proving absence makes that case safe.
        listing = api("git/matching-refs/" + ref.removeprefix("refs/"))
        if not isinstance(listing, list) or listing:
            raise ValueError(
                "owned ref cleanup failed or ref moved; preserve journal for inspection"
            )
    (directory / "request-ref-cleanup.json").write_text(
        json.dumps({**journal, "status": "absent"}) + "\n"
    )


if __name__ == "__main__":
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "publish":
            publish(Path(sys.argv[2]), sys.argv[3])
        elif len(sys.argv) == 3 and sys.argv[1] == "cleanup":
            cleanup(Path(sys.argv[2]))
        else:
            raise ValueError("invalid CI helper invocation")
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        ValidationError,
        subprocess.SubprocessError,
    ) as error:
        # Never echo API responses, subprocess stderr or environment values.
        print(
            f"CI request ref refused ({type(error).__name__}); retain private fixture for diagnosis.",
            file=sys.stderr,
        )
        sys.exit(2)
