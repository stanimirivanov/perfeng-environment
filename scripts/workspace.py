"""Safe sibling checkout discovery and missing-only bootstrap."""

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "workspace.json"


@dataclass(frozen=True)
class Repository:
    name: str
    path: Path
    url: str
    branch: str


@dataclass(frozen=True)
class State:
    repository: Repository
    state: str
    branch: str = "-"
    commit: str = "-"
    dirty: bool = False


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "--no-optional-locks", *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        # Do not print a remote's potential embedded credentials or command output.
        raise ValueError(
            "Git command failed; check repository access and Git ownership permissions"
        )
    return result.stdout.strip()


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate manifest key: {key}")
        value[key] = item
    return value


def load_manifest(path: Path) -> list[Repository]:
    path = path.resolve(strict=True)
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(data, dict) or set(data) != {"schemaVersion", "repositories"}:
        raise ValueError("Invalid workspace manifest fields")
    if type(data["schemaVersion"]) is not int or data["schemaVersion"] != 1:
        raise ValueError("Unsupported workspace manifest version")
    entries = data["repositories"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("Expected a nonempty repository list")
    repositories = []
    names, paths = set(), set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "path", "url", "source"}:
            raise ValueError("Invalid repository fields")
        name = entry["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]+", name):
            raise ValueError("Invalid repository name")
        source = entry["source"]
        if not isinstance(source, dict) or set(source) != {"branch"}:
            raise ValueError("Source must specify a branch, not a deployment artifact")
        branch = source["branch"]
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch):
            raise ValueError("Invalid branch")
        git("check-ref-format", f"refs/heads/{branch}")
        url = entry["url"]
        if not isinstance(url, str) or not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/" + re.escape(name) + r"\.git", url
        ):
            raise ValueError("Expected a credential-free GitHub HTTPS URL matching repository name")
        relative = entry["path"]
        expected = "." if name == path.parent.name else f"../{name}"
        if relative != expected:
            raise ValueError("Repository paths must be the manifest directory or named siblings")
        target = path.parent if relative == "." else path.parent.parent / name
        # resolve catches links/junctions pointing elsewhere, including links to a parent.
        if target.is_symlink() or target.resolve() != target:
            raise ValueError("Linked or redirected repository paths are not supported")
        key = os.path.normcase(str(target))
        if name in names or key in paths:
            raise ValueError("Duplicate repository name or path")
        names.add(name)
        paths.add(key)
        repositories.append(Repository(name, target, url, branch))
    return repositories


def canonical_origin(url: str) -> str:
    # Accept common SSH spellings for already-cloned GitHub repositories.
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:") :]
    elif url.startswith("ssh://git@github.com/"):
        url = "https://github.com/" + url[len("ssh://git@github.com/") :]
    return url.removesuffix("/").removesuffix(".git").lower()


def inspect(repository: Repository) -> State:
    path = repository.path
    if not os.path.lexists(path):
        return State(repository, "missing")
    if not path.is_dir():
        return State(repository, "conflict")
    try:
        top = Path(git("-C", str(path), "rev-parse", "--show-toplevel")).resolve()
        if top != path:
            return State(repository, "conflict")
        origin = git("-C", str(path), "remote", "get-url", "origin")
        if canonical_origin(origin) != canonical_origin(repository.url):
            return State(repository, "wrong-origin")
        branch = git("-C", str(path), "branch", "--show-current") or "(detached)"
        # Unborn branches are valid existing repositories and must not be cloned over.
        commit_result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        commit = commit_result.stdout.strip() if commit_result.returncode == 0 else "(unborn)"
        dirty = bool(git("-C", str(path), "status", "--porcelain", "--untracked-files=normal"))
        return State(repository, "existing", branch, commit, dirty)
    except (ValueError, OSError):
        return State(repository, "inaccessible")


def display(states: list[State]) -> None:
    for state in states:
        print(
            f"{state.repository.name:24} {state.state:13} {state.branch:24} "
            f"{state.commit:14} {'dirty' if state.dirty else ''}"
        )


def bootstrap(repositories: list[Repository], *, dry_run: bool) -> int:
    states = [inspect(repository) for repository in repositories]
    display(states)
    if any(state.state not in {"existing", "missing"} for state in states):
        print("Resolve conflicts/inaccessible checkouts first; nothing was cloned.")
        return 1
    for state in states:
        if state.state != "missing":
            continue
        repository = state.repository
        print(
            f"{'Would clone' if dry_run else 'Cloning'} {repository.name} "
            f"at source branch {repository.branch}"
        )
        if dry_run:
            continue
        # Exclusively reserve the destination to close the existence-check race.
        # git clone may use our empty directory, never someone else's existing one.
        repository.path.mkdir()
        try:
            git(
                "clone",
                "--branch",
                repository.branch,
                "--single-branch",
                "--",
                repository.url,
                str(repository.path),
            )
        except (ValueError, OSError):
            print(
                f"Clone failed for {repository.name}. Its partial directory is retained; "
                "inspect it manually before retrying. Earlier clones are retained too."
            )
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "bootstrap", "validate"])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        repositories = load_manifest(args.manifest)
        if args.command == "validate":
            workspace = json.loads(
                args.manifest.resolve()
                .with_name("perfeng.code-workspace")
                .read_text(encoding="utf-8")
            )
            expected = [
                {
                    "name": r.name,
                    "path": "." if r.path == args.manifest.resolve().parent else f"../{r.name}",
                }
                for r in repositories
            ]
            if workspace.get("folders") != expected:
                raise ValueError("VS Code folders do not match workspace.json")
            print(f"Validated {len(repositories)} source repositories and VS Code folders.")
            return 0
        if args.command == "bootstrap":
            return bootstrap(repositories, dry_run=args.dry_run)
        states = [inspect(repository) for repository in repositories]
        display(states)
        return 0 if all(state.state == "existing" for state in states) else 1
    except (ValueError, OSError, TypeError) as error:
        print(f"Workspace error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
