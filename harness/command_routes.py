"""Check the reviewed inventory of callable repository scripts without running them."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import stat
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

BASELINE = "harness/command-routes.toml"
GUIDE = "docs/development/harness.md#reviewing-command-routes"
DEFINITION = re.compile(r"sha256:[0-9a-f]{64}")
SHELL_SUFFIXES = {".sh", ".bash", ".zsh", ".ksh", ".fish"}
SCRIPT_ARGUMENT = r"""['"]?(?P<target>(?:\$\{?source_dir\}?/)?[\w./-]+)['"]?(?=$|[\s;&|()])"""
INTERPRETER_CALL = re.compile(
    r"(?<![\w.-])(?P<program>python(?:3(?:\.\d+)?)?|bash|sh|zsh|ksh|fish|node)"
    r"(?P<flags>(?:[ \t]+-[\w-]+)*)[ \t]+" + SCRIPT_ARGUMENT
)
SHELL_SOURCE = re.compile(
    r"(?:^\s*(?:run_stage\s+\S+\s+)?|\bthen\s+|[;&]\s*)(?:source|\.)[ \t]+" + SCRIPT_ARGUMENT
)
JQ_FILTER = re.compile(r"(?<![\w.-])jq[ \t]+(?:[^;|]*?[ \t]+)?-f[ \t]+" + SCRIPT_ARGUMENT)
NODE_MAIN = re.compile(r"^[ \t]*if\s*\([^\n]*process\.argv\[1\][^\n]*import\.meta\.url", re.M)
INTERPRETER_SUFFIXES = {
    "bash": SHELL_SUFFIXES,
    "sh": SHELL_SUFFIXES,
    "zsh": SHELL_SUFFIXES,
    "ksh": SHELL_SUFFIXES,
    "fish": SHELL_SUFFIXES,
    "node": {".js", ".cjs", ".mjs"},
}


@dataclass(frozen=True)
class Route:
    location: str
    definition: str


def working_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=30,
    )
    paths = {root / os.fsdecode(name) for name in result.stdout.split(b"\0") if name}
    return sorted(path for path in paths if path.is_file() and path.resolve() == path)


def digest(definition: bytes) -> str:
    return "sha256:" + hashlib.sha256(definition).hexdigest()


def literal_path(caller: Path, target: str) -> Path:
    for prefix in ("$source_dir/", "${source_dir}/"):
        if target.startswith(prefix):
            return caller.parent / target.removeprefix(prefix)
    return Path(target)


def invocation_evidence(sources: dict[Path, bytes]) -> tuple[set[Path], set[Path]]:
    invoked: set[Path] = set()
    sourced: set[Path] = set()
    for path, source in sources.items():
        shell = path.suffix in SHELL_SUFFIXES
        declaration = path.name in {
            "Makefile",
            "Taskfile.yml",
            "Taskfile.yaml",
            "Dockerfile",
        } or path.parts[:2] == (".github", "workflows")
        if not (shell or declaration):
            continue
        for line in source.decode("utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("#"):
                continue
            for command in INTERPRETER_CALL.finditer(line):
                target = literal_path(path, command["target"])
                program, flags = command["program"], command["flags"].split()
                if program.startswith("python"):
                    if "-m" in flags and re.fullmatch(r"\w+(?:\.\w+)*", command["target"]):
                        module = Path(*command["target"].split("."))
                        invoked.update((module.with_suffix(".py"), module / "__main__.py"))
                    elif "-c" not in flags and target.suffix == ".py":
                        invoked.add(target)
                elif program == "node" and {"--check", "--test", "-e"} & set(flags):
                    continue
                elif target.suffix in INTERPRETER_SUFFIXES[program]:
                    invoked.add(target)
            for command in JQ_FILTER.finditer(line):
                target = literal_path(path, command["target"])
                if target.suffix == ".jq":
                    invoked.add(target)
            if shell:
                for reference in SHELL_SOURCE.finditer(line):
                    target = reference["target"]
                    sourced.add(literal_path(path, target))
                    # Fragments may source a path relative to their own directory.
                    sourced.add(path.parent / target)
    return invoked, sourced


def main_guard(tree: ast.Module) -> ast.If | None:
    for statement in tree.body:
        if not isinstance(statement, ast.If):
            continue
        match statement.test:
            case (
                ast.Compare(
                    left=ast.Name(id="__name__"),
                    ops=[ast.Eq()],
                    comparators=[ast.Constant(value="__main__")],
                )
                | ast.Compare(
                    left=ast.Constant(value="__main__"),
                    ops=[ast.Eq()],
                    comparators=[ast.Name(id="__name__")],
                )
            ):
                return statement
    return None


def unittest_launcher(path: Path, guard: ast.If) -> bool:
    if path.parts[0] != "tests" or not path.name.startswith("test_") or guard.orelse:
        return False
    match guard.body:
        case [
            ast.Expr(value=ast.Call(func=ast.Attribute(value=ast.Name(id="unittest"), attr="main")))
        ]:
            return True
        case _:
            return False


def payload_routes(path: Path, tree: ast.Module, sources: dict[Path, bytes]) -> dict[str, Route]:
    """Link bounded interpreter payloads to their literal or file declaration."""
    constants: dict[str, bytes] = {}
    imports: dict[str, str] = {}
    for statement in tree.body:
        match statement:
            case ast.Assign(targets=[ast.Name(id=name)], value=ast.Constant(value=str() as text)):
                constants[name] = text.encode()
            case ast.Assign(
                targets=[ast.Name(id=name)],
                value=ast.Call(
                    func=ast.Attribute(value=ast.Constant(value=str() as text), attr="strip"),
                    args=[],
                    keywords=[],
                ),
            ):
                constants[name] = text.strip().encode()
            case ast.ImportFrom(module=str() as module, level=0):
                for imported in statement.names:
                    imports[imported.asname or imported.name] = f"{module}.{imported.name}"

    definitions: dict[str, set[bytes]] = {}
    locations: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            args = node.elts
        elif isinstance(node, ast.Call):
            args = node.args
        else:
            continue
        for program, flags, payload in zip(args, args[1:], args[2:], strict=False):
            if not (
                isinstance(program, ast.Constant)
                and isinstance(program.value, str)
                and Path(program.value).name in {"python", "python3", "sh", "bash"}
                and isinstance(flags, ast.Constant)
                and flags.value in ("-c", "-ec")
            ):
                continue
            match payload:
                case ast.Name(id=name) if name in constants:
                    identity, content = name, constants[name]
                case ast.Call(
                    func=ast.Attribute(
                        attr="read_text",
                        value=ast.Call(
                            func=ast.Name(id="Path"),
                            args=[ast.Attribute(value=ast.Name(id=name), attr="__file__")],
                            keywords=[],
                        ),
                    ),
                ) if name in imports:
                    identity = imports[name]
                    source = Path(*identity.split(".")).with_suffix(".py")
                    if source not in sources:
                        raise ValueError(
                            f"{path}:{payload.lineno}: unresolved payload file {source}"
                        )
                    content = sources[source]
                case _:
                    continue
            definition = "\0".join(map(ast.dump, (program, flags, payload))).encode()
            definitions.setdefault(identity, set()).add(definition + b"\0" + content)
            locations[identity] = min(locations.get(identity, payload.lineno), payload.lineno)
    return {
        f"payload:{path.as_posix()}#{identity}": Route(
            f"{path.as_posix()}:{locations[identity]}",
            digest("\0".join(sorted(digest(part) for part in parts)).encode()),
        )
        for identity, parts in definitions.items()
    }


def discover(root: Path) -> dict[str, Route]:
    sources: dict[Path, bytes] = {}
    executable = set()
    for path in working_files(root):
        relative = path.relative_to(root)
        sources[relative] = path.read_bytes()
        if path.stat().st_mode & stat.S_IXUSR:
            executable.add(relative)
    invoked, sourced = invocation_evidence(sources)

    routes = {}
    for path, source in sources.items():
        line = 1
        definition = source
        direct = path in invoked or path in executable
        shebang = source.startswith(b"#!") and path not in sourced
        if path.suffix == ".py":
            tree = ast.parse(source, filename=path.as_posix())
            routes.update(payload_routes(path, tree, sources))
            guard = main_guard(tree)
            if guard is not None and not unittest_launcher(path, guard):
                direct = True
                line = guard.lineno
            direct |= path.name == "__main__.py"
            definition = ast.dump(tree).encode() + (source.splitlines()[0] if shebang else b"")
        elif path.suffix in SHELL_SUFFIXES:
            direct |= path not in sourced
        elif path.suffix in INTERPRETER_SUFFIXES["node"]:
            entry = NODE_MAIN.search(source.decode("utf-8", errors="replace"))
            if entry:
                direct = True
                line = source.decode("utf-8", errors="replace").count("\n", 0, entry.start()) + 1
        if direct or shebang:
            relative = path.as_posix()
            if path in executable:
                definition = b"executable\0" + definition
            routes[f"script:{relative}"] = Route(f"{relative}:{line}", digest(definition))
    return routes


def load_baseline(root: Path) -> dict[str, str]:
    path = root / BASELINE
    if not path.is_file() or path.resolve() != path:
        raise ValueError("the baseline must be a regular repository file")
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    if data.keys() != {"routes"} or not isinstance(data["routes"], list):
        raise ValueError("expected a 'routes' array of reviewed records")
    routes: dict[str, str] = {}
    for index, entry in enumerate(data["routes"], start=1):
        if not isinstance(entry, dict) or entry.keys() != {"id", "owner", "purpose", "definition"}:
            raise ValueError(f"routes[{index}] must have id, owner, purpose, and definition")
        if any(not isinstance(value, str) or not value.strip() for value in entry.values()):
            raise ValueError(f"routes[{index}] fields must be nonblank strings")
        identity = entry["id"]
        if identity in routes:
            raise ValueError(f"routes[{index}] duplicates {identity!r}")
        if not DEFINITION.fullmatch(entry["definition"]):
            raise ValueError(f"routes[{index}].definition must be a SHA-256 digest")
        routes[identity] = entry["definition"]
    return routes


def finding(rule: str, location: str, reason: str, fix: str) -> None:
    print(f"command-routes/{rule}: {location}: {reason}; fix: {fix}; see {GUIDE}", file=sys.stderr)


def main() -> int:
    root = Path.cwd()
    try:
        reviewed = load_baseline(root)
    except (OSError, ValueError) as error:
        finding("invalid-baseline", BASELINE, str(error), "correct the reviewed baseline")
        return 1
    try:
        discovered = discover(root)
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as error:
        finding("scan-failed", ".", str(error), "run in the repository root and correct the input")
        return 1

    failed = False
    for identity in sorted(discovered.keys() | reviewed.keys()):
        route = discovered.get(identity)
        if route is None:
            finding(
                "missing-route",
                BASELINE,
                f"{identity!r} is no longer declared",
                "restore the route or remove its reviewed record",
            )
        elif identity not in reviewed:
            finding(
                "unreviewed-route",
                route.location,
                f"{identity!r} has no reviewed record",
                f"reuse an existing route or review and record owner, purpose, and {route.definition}",
            )
        elif route.definition != reviewed[identity]:
            finding(
                "changed-definition",
                route.location,
                f"{identity!r} has changed",
                f"restore the definition or review the change to {route.definition}",
            )
        else:
            continue
        failed = True
    if failed:
        return 1
    print(f"command-routes: reviewed command routes match ({len(discovered)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
