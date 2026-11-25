"""Check reviewed repository command routes without running them."""

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
from threading import Timer

if __package__:
    from .make_routes import MAKEFILES, UnsupportedMake, make_definitions
else:
    from make_routes import MAKEFILES, UnsupportedMake, make_definitions

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
LITERAL_OUTPUT = re.compile(
    r"""^[ \t]*[@+\-]*[ \t]*(?:echo|printf)"""
    r"""(?:[ \t]+(?:'[^']*'|"(?:[^"\\$`]|\\.)*"|[^ \t'"$`\\;&|()<>]+))*[ \t]*$"""
)
INTERPRETER_SUFFIXES = {
    "bash": SHELL_SUFFIXES,
    "sh": SHELL_SUFFIXES,
    "zsh": SHELL_SUFFIXES,
    "ksh": SHELL_SUFFIXES,
    "fish": SHELL_SUFFIXES,
    "node": {".js", ".cjs", ".mjs"},
}
INVOCATION_FILES = set(MAKEFILES) | {"Taskfile.yml", "Taskfile.yaml", "Dockerfile"}
SOURCE_SUFFIXES = {".py", ".jq"} | INTERPRETER_SUFFIXES["node"]
MAX_GIT_PATH_BYTES = 1024 * 1024
MAX_GIT_PATHS = 8192
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Route:
    location: str
    definition: str


def working_files(root: Path) -> list[Path]:
    with subprocess.Popen(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as process:
        assert process.stdout is not None
        deadline = Timer(30, process.kill)
        deadline.start()
        try:
            output = process.stdout.read(MAX_GIT_PATH_BYTES + 1)
            if len(output) > MAX_GIT_PATH_BYTES or output.count(b"\0") > MAX_GIT_PATHS:
                raise ValueError(f"Git file list exceeds {MAX_GIT_PATHS} records or 1 MiB of paths")
            if process.wait() != 0:
                raise ValueError("git ls-files failed or exceeded 30 seconds")
        finally:
            deadline.cancel()
            if process.poll() is None:
                process.kill()
    paths = {root / os.fsdecode(name) for name in output.split(b"\0") if name}
    regular = []
    for path in sorted(paths):
        if path.name in MAKEFILES and (path.is_symlink() or path.resolve() != path):
            raise UnsupportedMake(
                path.relative_to(root), 1, "Makefiles must be regular repository files"
            )
        if path.is_file() and path.resolve() == path:
            regular.append(path)
    return regular


def digest(definition: bytes) -> str:
    return "sha256:" + hashlib.sha256(definition).hexdigest()


def literal_path(caller: Path, target: str) -> Path:
    for prefix in ("$source_dir/", "${source_dir}/"):
        if target.startswith(prefix):
            return caller.parent / target.removeprefix(prefix)
    return Path(target)


def invocation_scope(path: Path) -> bool:
    return (
        path.suffix in SHELL_SUFFIXES
        or path.name in INVOCATION_FILES
        or path.parts[:2] == (".github", "workflows")
    )


def invocation_evidence(sources: dict[Path, bytes]) -> tuple[set[Path], set[Path]]:
    invoked: set[Path] = set()
    sourced: set[Path] = set()
    for path, source in sources.items():
        shell = path.suffix in SHELL_SUFFIXES
        if not invocation_scope(path):
            continue
        for line in source.decode("utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("#") or LITERAL_OUTPUT.fullmatch(line):
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


def payload_argv(args: list[ast.expr], index: int) -> list[ast.expr]:
    argv = args[index : index + 4]
    match argv:
        case [
            ast.Attribute(value=ast.Name(id="sys"), attr="executable"),
            ast.Constant(value="-I" | "-IB"),
            ast.Constant(value="-c"),
            _,
        ]:
            return argv
        case [
            ast.Attribute(value=ast.Name(id="sys"), attr="executable"),
            ast.Constant(value="-c"),
            _,
            *_,
        ]:
            return argv[:3]
        case [
            ast.Constant(value=str() as program),
            ast.Constant(value="-c" | "-ec"),
            _,
            *_,
        ] if Path(program).name in {"python", "python3", "sh", "bash"}:
            return argv[:3]
        case _:
            return []


def payload_routes(path: Path, tree: ast.Module, sources: dict[Path, bytes]) -> dict[str, Route]:
    """Link bounded interpreter payloads to their literal or file declaration."""
    constants: dict[str, bytes] = {}
    imports: dict[str, str] = {}
    upload_builder = None
    upload_nodes = set()
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
            case ast.FunctionDef(name="upload_script") if path == Path(
                "scripts/artifact_upload.py"
            ):
                upload_builder = statement
            case ast.FunctionDef(name="publish") if path == Path("scripts/artifact_upload.py"):
                upload_nodes.update(ast.walk(statement))

    definitions: dict[str, set[bytes]] = {}
    locations: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            args = node.elts
        elif isinstance(node, ast.Call):
            args = node.args
        else:
            continue
        for index in range(len(args) - 2):
            argv = payload_argv(args, index)
            if not argv:
                continue
            program, payload = argv[0], argv[-1]
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
                case ast.Constant(value=str() as shell_code) if node in upload_nodes:
                    match args:
                        case [
                            ast.Starred(value=ast.Name(id="kube")),
                            ast.Constant(value="exec"),
                            ast.Constant(value="-i"),
                            ast.Name(id="pod_name"),
                            ast.Constant(value="--"),
                            ast.Constant(value="sh"),
                            ast.Constant(value="-c"),
                            _,
                        ]:
                            identity, content = "stage_source", shell_code.encode()
                        case _:
                            continue
                case ast.Call(
                    func=ast.Name(id="upload_script"),
                    args=[ast.Attribute(value=ast.Name(id="source"), attr="reference")],
                    keywords=[],
                ) if (
                    upload_builder is not None
                    and node in upload_nodes
                    and isinstance(program, ast.Constant)
                    and program.value == "sh"
                ):
                    identity, content = "upload_script", ast.dump(upload_builder).encode()
                case _:
                    continue
            definition = "\0".join(map(ast.dump, argv)).encode()
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
    total = 0
    for path in working_files(root):
        relative = path.relative_to(root)
        is_executable = bool(path.stat().st_mode & stat.S_IXUSR)
        with path.open("rb") as stream:
            header = stream.read(2)
            if not (
                is_executable
                or header == b"#!"
                or path.suffix in SOURCE_SUFFIXES
                or invocation_scope(relative)
            ):
                continue
            budget = min(MAX_SOURCE_BYTES, MAX_TOTAL_SOURCE_BYTES - total)
            source = header + stream.read(max(0, budget - len(header) + 1))
        if len(source) > MAX_SOURCE_BYTES:
            raise ValueError(f"{relative} exceeds the 2 MiB source limit")
        if len(source) > budget:
            raise ValueError(f"{relative} exceeds the 32 MiB total source limit")
        sources[relative] = source
        total += len(source)
        if is_executable:
            executable.add(relative)
    for directory in sorted({path.parent for path in sources if path.name in MAKEFILES}):
        makefiles = [directory / name for name in MAKEFILES if directory / name in sources]
        if len(makefiles) > 1:
            raise UnsupportedMake(makefiles[0], 1, f"takes precedence over {makefiles[1]}")

    invoked, sourced = invocation_evidence(sources)
    routes = {}
    for path, source in sources.items():
        if path.name in MAKEFILES:
            for name, line, definition in make_definitions(path, source):
                relative = path.as_posix()
                routes[f"make:{relative}#{name}"] = Route(f"{relative}:{line}", digest(definition))
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
        source = stream.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError("the baseline exceeds 2 MiB")
    data = tomllib.loads(source.decode("utf-8"))
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
    if sys.version_info[:2] != (3, 12):
        finding(
            "unsupported-python",
            ".python-version",
            "command route fingerprints require Python 3.12",
            "use uv run --locked --offline --python 3.12 python -m harness offline",
        )
        return 1
    root = Path.cwd()
    try:
        reviewed = load_baseline(root)
    except (OSError, ValueError) as error:
        finding("invalid-baseline", BASELINE, str(error), "correct the reviewed baseline")
        return 1
    try:
        discovered = discover(root)
    except UnsupportedMake as error:
        finding(
            "unsupported-make",
            error.location,
            str(error),
            "use a supported literal Make declaration or add narrow reviewed coverage",
        )
        return 1
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as error:
        finding("scan-failed", ".", str(error), "check the reported input and the checkout limits")
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
