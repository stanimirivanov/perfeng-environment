"""Read the repository's supported Make declarations without expanding or running them."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
NAME = r"[A-Za-z0-9_][A-Za-z0-9_./-]*"
VARIABLE = r"[A-Za-z_][A-Za-z0-9_]*"
LITERAL_NAME = re.compile(NAME)
CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
RULE = re.compile(rf"(?P<target>\.PHONY|{NAME})[ \t]*:[ \t]*(?P<prerequisites>.*)")
ASSIGNMENT = re.compile(rf"(?P<name>{VARIABLE})[ \t]*(?P<operator>\?=|:=|=)[ \t]*(?P<value>.*)")
CONDITION = re.compile(rf"ifneq \(\$\(strip \$\((?P<name>{VARIABLE})\)\),\)")
REFERENCE = re.compile(rf"\$\(({VARIABLE})\)|\$\{{({VARIABLE})\}}|\$\$")
DISALLOWED_SETTINGS = {
    "GNUMAKEFLAGS",
    "GPATH",
    "MAKE",
    "MAKECMDGOALS",
    "MAKE_COMMAND",
    "MAKEFILE_LIST",
    "MAKEFILES",
    "MAKEFLAGS",
    "MAKELEVEL",
    "MAKEOVERRIDES",
    "MFLAGS",
    "PATH",
    "SHELL",
    "VPATH",
}
MAX_LINES = 4096
MAX_NAMES = 256


class UnsupportedMake(ValueError):
    def __init__(self, path: Path, line: int, reason: str) -> None:
        self.location = f"{path.as_posix()}:{line}"
        super().__init__(reason)


@dataclass(frozen=True)
class Variable:
    line: int
    immediate: bool
    digest: str
    references: set[str]


@dataclass(frozen=True)
class Condition:
    line: int
    name: str


@dataclass
class MakeTarget:
    line: int
    prerequisites: list[str]
    recipes: list[tuple[str, Condition | None]] = field(default_factory=list)
    references: set[str] = field(default_factory=set)


def variable_references(source: str, path: Path, line: int) -> set[str]:
    names = set()
    offset = source.find("$")
    while offset != -1:
        reference = REFERENCE.match(source, offset)
        if reference is None:
            raise UnsupportedMake(
                path, line, "unsupported Make expansion; use a plain named variable reference"
            )
        name = reference[1] or reference[2]
        if name:
            names.add(name)
        offset = source.find("$", reference.end())
    return names


def referenced_definitions(
    references: set[str], variables: dict[str, Variable], before: int
) -> list[tuple[str, str, str]]:
    definitions: set[tuple[str, str, str]] = set()
    visited: set[tuple[str, str]] = set()
    pending = [(name, "", before) for name in references]
    while pending:
        name, scope, bound = pending.pop()
        if (name, scope) in visited:
            continue
        visited.add((name, scope))
        variable = variables.get(name)
        if variable is None or variable.line >= bound:
            definitions.add((scope, name, ""))
            continue
        definitions.add((scope, name, variable.digest))
        # A simply expanded variable captures its dependencies at the assignment.
        # Keep that scope distinct from recipe-time and conditional references.
        if variable.immediate:
            scope, bound = name, variable.line
        pending.extend((dependency, scope, bound) for dependency in variable.references)
    return sorted(definitions)


def make_definitions(path: Path, source: bytes) -> Iterator[tuple[str, int, bytes]]:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as error:
        line = source.count(b"\n", 0, error.start) + 1
        raise UnsupportedMake(path, line, "Makefile must be UTF-8") from error
    lines = text.replace("\r\n", "\n").removesuffix("\n").split("\n")
    if len(lines) > MAX_LINES:
        raise UnsupportedMake(path, MAX_LINES + 1, f"Makefile exceeds {MAX_LINES} lines")

    targets: dict[str, MakeTarget] = {}
    variables: dict[str, Variable] = {}
    phony: dict[str, int] = {}
    referenced_names: set[str] = set()
    target: MakeTarget | None = None
    default_goal = None
    condition: Condition | None = None
    recipe_context = len(lines) + 1
    for number, line in enumerate(lines, start=1):
        if CONTROL.search(line):
            raise UnsupportedMake(path, number, "unsupported control character in Makefile")
        if line.startswith("\t"):
            if target is None:
                raise UnsupportedMake(path, number, "recipe has no literal target")
            recipe = line[1:]
            if recipe.endswith("\\"):
                raise UnsupportedMake(path, number, "continued Make recipes are unsupported")
            references = variable_references(recipe, path, number)
            referenced_names.update(references)
            target.references.update(references)
            target.recipes.append((recipe, condition))
        else:
            if line.endswith("\\"):
                raise UnsupportedMake(path, number, "continued Make declarations are unsupported")
            declaration = line.partition("#")[0].lstrip(" \t")
            if "\\" in declaration:
                raise UnsupportedMake(path, number, "escaped Make declarations are unsupported")
            if not declaration:
                continue
            syntax = declaration.rstrip(" \t")
            match = CONDITION.fullmatch(syntax)
            if match is not None:
                if condition is not None or target is None:
                    raise UnsupportedMake(
                        path, number, "conditional requires a literal target and no nesting"
                    )
                condition = Condition(number, match["name"])
                referenced_names.add(condition.name)
            elif syntax == "endif" and condition is not None:
                condition = None
            elif condition is not None:
                raise UnsupportedMake(
                    path, number, "only recipe lines are supported inside a conditional"
                )
            elif (assignment := ASSIGNMENT.fullmatch(declaration)) is not None:
                name = assignment["name"]
                if name in DISALLOWED_SETTINGS or name in variables:
                    raise UnsupportedMake(
                        path, number, f"unsupported global or repeated setting {name!r}"
                    )
                value = assignment["value"]
                references = variable_references(value, path, number)
                referenced_names.update(references)
                signature = json.dumps([assignment["operator"], value]).encode()
                variables[name] = Variable(
                    number,
                    assignment["operator"] == ":=",
                    hashlib.sha256(signature).hexdigest(),
                    references,
                )
                target = None
            else:
                rule = RULE.fullmatch(syntax)
                if rule is None:
                    if "$" in syntax:
                        raise UnsupportedMake(
                            path, number, "unsupported Make expansion in a declaration"
                        )
                    raise UnsupportedMake(
                        path, number, "expected a literal target rule or simple variable assignment"
                    )
                prerequisites = (
                    re.split(r"[ \t]+", rule["prerequisites"]) if rule["prerequisites"] else []
                )
                if any(not LITERAL_NAME.fullmatch(name) for name in prerequisites):
                    raise UnsupportedMake(
                        path, number, "expected literal prerequisites without modifiers"
                    )
                name = rule["target"]
                if name == ".PHONY":
                    for prerequisite in prerequisites:
                        phony.setdefault(prerequisite, number)
                    target = None
                else:
                    if name in targets:
                        raise UnsupportedMake(path, number, f"duplicate target {name!r}")
                    if default_goal is None:
                        default_goal = name
                    target = targets[name] = MakeTarget(number, prerequisites)

        if (
            len(variables) > MAX_NAMES
            or len(referenced_names) > MAX_NAMES
            or len(targets.keys() | phony.keys()) > MAX_NAMES
        ):
            raise UnsupportedMake(
                path, number, f"Makefile exceeds {MAX_NAMES} targets or variable names"
            )

    if condition is not None:
        raise UnsupportedMake(path, condition.line, "unterminated Make conditional")

    for name, line in phony.items():
        targets.setdefault(name, MakeTarget(line, []))
    condition_definitions: dict[Condition, tuple[str, str]] = {}
    for name, target in targets.items():
        recipes = []
        for recipe, guard in target.recipes:
            if guard is not None and guard not in condition_definitions:
                bindings = referenced_definitions({guard.name}, variables, guard.line)
                condition_definitions[guard] = (
                    guard.name,
                    hashlib.sha256(json.dumps(bindings).encode()).hexdigest(),
                )
            recipes.append((recipe, condition_definitions[guard] if guard is not None else None))
        definition = {
            "default": name == default_goal,
            "phony": name in phony,
            "prerequisites": target.prerequisites,
            "recipes": recipes,
            "variables": referenced_definitions(target.references, variables, recipe_context),
        }
        yield name, target.line, json.dumps(definition).encode()
