"""Read literal Taskfile routes as YAML data."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Set
from dataclasses import dataclass
from pathlib import Path

from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

if __package__:
    from .route_yaml import UnsupportedYamlRoute, read_document
else:
    from route_yaml import UnsupportedYamlRoute, read_document

TASKFILES = (
    "Taskfile.yml",
    "taskfile.yml",
    "Taskfile.yaml",
    "taskfile.yaml",
    "Taskfile.dist.yml",
    "taskfile.dist.yml",
    "Taskfile.dist.yaml",
    "taskfile.dist.yaml",
)
TASK_NAME = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*(?::[a-zA-Z0-9_][a-zA-Z0-9_.-]*)*")
COMMON_SCALARS = {"method", "run"}
COMMON_BOOLEANS = {"silent", "use_gitignore"}
COMMON_LISTS = {"dotenv", "set", "shopt"}
ROOT_SCALARS = COMMON_SCALARS | {"interval", "output", "version"}
TASK_SCALARS = COMMON_SCALARS | {"desc", "dir", "if", "label", "summary"}
TASK_BOOLEANS = COMMON_BOOLEANS | {"ignore_error", "interactive", "internal", "watch"}
TASK_LISTS = COMMON_LISTS | {"generates", "platforms", "sources", "status"}
COMMAND_SCALARS = {"if", "timeout"}
COMMAND_BOOLEANS = {"ignore_error", "silent"}
COMMAND_LISTS = {"platforms", "set", "shopt"}
VARIABLE_BLOCKS = ("vars", "env")
ROOT_FIELDS = ROOT_SCALARS | COMMON_BOOLEANS | COMMON_LISTS | set(VARIABLE_BLOCKS) | {"tasks"}
TASK_FIELDS = (
    TASK_SCALARS
    | TASK_BOOLEANS
    | TASK_LISTS
    | set(VARIABLE_BLOCKS)
    | {"cmd", "cmds", "deps", "preconditions", "prompt", "requires"}
)
COMMAND_SETTINGS = COMMAND_SCALARS | COMMAND_BOOLEANS | COMMAND_LISTS | {"vars"}


@dataclass(frozen=True)
class TaskDefinition:
    name: str
    line: int
    definition: bytes
    commands: tuple[tuple[Path, str], ...]


@dataclass(frozen=True)
class TaskfileDefinitions:
    tasks: tuple[TaskDefinition, ...]
    shared_commands: tuple[str, ...]


class UnsupportedTask(UnsupportedYamlRoute):
    pass


def invalid(path: Path, node: Node, reason: str) -> UnsupportedTask:
    return UnsupportedTask(path, node.start_mark.line + 1, reason)


def mapping(path: Path, node: Node) -> dict[str, tuple[ScalarNode, Node]]:
    if not isinstance(node, MappingNode):
        raise invalid(path, node, "expected a YAML mapping")
    fields = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or not key.value or key.value == "<<":
            raise invalid(path, key, "expected a literal YAML key; merge keys are unsupported")
        if key.value in fields:
            raise invalid(path, key, f"duplicate YAML key {key.value!r}")
        fields[key.value] = key, value
    return fields


def node_data(path: Path, node: Node) -> object:
    if isinstance(node, ScalarNode):
        # Preserve the plain-scalar distinction for Task's typed values without
        # using PyYAML's YAML 1.1 implicit type conversions.
        return ["scalar", node.style is None, node.value]
    if isinstance(node, SequenceNode):
        return ["list", [node_data(path, item) for item in node.value]]
    return mapping_data(path, mapping(path, node))


def mapping_data(path: Path, fields: Fields) -> object:
    return [
        "mapping",
        [
            (key.style is None, name, node_data(path, value))
            for name, (key, value) in fields.items()
        ],
    ]


type Fields = dict[str, tuple[ScalarNode, Node]]


def check_fields(path: Path, fields: Fields, supported: Set[str], scope: str) -> None:
    for name, (key, _) in fields.items():
        if name not in supported:
            raise invalid(path, key, f"unsupported {scope} field {name!r}")


def scalar(path: Path, node: Node) -> str:
    if not isinstance(node, ScalarNode):
        raise invalid(path, node, "expected a YAML scalar")
    return node.value


def sequence(path: Path, node: Node) -> list[Node]:
    if not isinstance(node, SequenceNode):
        raise invalid(path, node, "expected a YAML sequence")
    return node.value


def boolean(path: Path, node: Node) -> None:
    if (
        not isinstance(node, ScalarNode)
        or node.style is not None
        or node.value not in {"true", "True", "TRUE", "false", "False", "FALSE"}
    ):
        raise invalid(path, node, "expected a YAML boolean")


def scalar_list(path: Path, node: Node) -> None:
    for item in sequence(path, node):
        scalar(path, item)


def check_shapes(
    path: Path, fields: Fields, *, scalars: Set[str], booleans: Set[str], lists: Set[str]
) -> None:
    for name, (_, node) in fields.items():
        if name in scalars:
            scalar(path, node)
        elif name in booleans:
            boolean(path, node)
        elif name in lists:
            scalar_list(path, node)


def check_requirements(path: Path, node: Node) -> None:
    fields = mapping(path, node)
    check_fields(path, fields, {"vars"}, "Task requires")
    if "vars" not in fields:
        raise invalid(path, node, "expected a 'vars' sequence in Task requires")
    for requirement in sequence(path, fields["vars"][1]):
        if isinstance(requirement, ScalarNode):
            continue
        variable = mapping(path, requirement)
        check_fields(path, variable, {"name", "enum"}, "Task requirement")
        if "name" not in variable:
            raise invalid(path, requirement, "expected a Task requirement name")
        scalar(path, variable["name"][1])
        if "enum" in variable:
            scalar_list(path, variable["enum"][1])


def check_call(path: Path, node: Node, tasks: Fields) -> None:
    name = scalar(path, node)
    if not TASK_NAME.fullmatch(name):
        raise invalid(path, node, f"unsupported Task call {name!r}; expected a literal name")
    if name not in tasks:
        raise invalid(path, node, f"Task call {name!r} is undeclared in this Taskfile")


def variable_commands(path: Path, fields: Fields) -> Iterator[str]:
    for name in VARIABLE_BLOCKS:
        if name not in fields:
            continue
        for _, node in mapping(path, fields[name][1]).values():
            if isinstance(node, ScalarNode):
                continue
            if name == "vars" and isinstance(node, SequenceNode):
                scalar_list(path, node)
                continue
            variable = mapping(path, node)
            declarations = {"sh", "ref", "value"}
            check_fields(path, variable, declarations | {"secret"}, "Task variable")
            if len(variable.keys() & declarations) != 1:
                raise invalid(path, node, "expected exactly one Task variable declaration")
            check_shapes(path, variable, scalars=declarations, booleans={"secret"}, lists=set())
            if "sh" in variable:
                yield scalar(path, variable["sh"][1])


def command_texts(
    path: Path, node: Node, tasks: Fields, directory: Path, *, dependency: bool = False
) -> Iterator[tuple[Path, str]]:
    if isinstance(node, ScalarNode):
        if dependency:
            check_call(path, node, tasks)
        else:
            yield directory, node.value
        return
    fields = mapping(path, node)
    actions = fields.keys() & ({"task"} if dependency else {"cmd", "task", "defer"})
    check_fields(path, fields, actions | COMMAND_SETTINGS, "Task command")
    check_shapes(
        path, fields, scalars=COMMAND_SCALARS, booleans=COMMAND_BOOLEANS, lists=COMMAND_LISTS
    )
    if len(actions) != 1:
        raise invalid(path, node, "expected exactly one Task command or call")
    action = actions.pop()
    value = fields[action][1]
    if action == "task":
        check_call(path, value, tasks)
    elif action == "defer":
        yield from command_texts(path, value, tasks, directory)
    else:
        yield directory, scalar(path, value)
    if "if" in fields:
        yield directory, scalar(path, fields["if"][1])
    yield from ((path.parent, text) for text in variable_commands(path, fields))


def precondition_fields(path: Path, node: Node) -> Fields:
    fields = mapping(path, node)
    check_fields(path, fields, {"sh", "msg"}, "Task precondition")
    if "sh" not in fields:
        raise invalid(path, node, "expected a Task precondition command")
    if "msg" in fields:
        scalar(path, fields.pop("msg")[1])
    return fields


def task_commands(
    path: Path, task: Node, fields: Fields, tasks: Fields, directory: Path
) -> Iterator[tuple[Path, str]]:
    if isinstance(task, ScalarNode):
        yield directory, task.value
    elif isinstance(task, SequenceNode):
        for command in task.value:
            yield from command_texts(path, command, tasks, directory)
    if "cmd" in fields:
        yield from command_texts(path, fields["cmd"][1], tasks, directory)
    for name in ("cmds", "deps", "status", "preconditions"):
        if name not in fields:
            continue
        for item in sequence(path, fields[name][1]):
            if name in {"cmds", "deps"}:
                yield from command_texts(path, item, tasks, directory, dependency=name == "deps")
            elif name == "preconditions" and isinstance(item, MappingNode):
                yield directory, scalar(path, precondition_fields(path, item)["sh"][1])
            else:
                yield directory, scalar(path, item)
    if "if" in fields:
        yield directory, scalar(path, fields["if"][1])
    yield from ((directory, text) for text in variable_commands(path, fields))


def fingerprint_data(path: Path, fields: Fields) -> bytes:
    data = []
    for name, (_, value) in fields.items():
        if name == "preconditions":
            preconditions = [
                mapping_data(path, precondition_fields(path, item))
                if isinstance(item, MappingNode)
                else node_data(path, item)
                for item in sequence(path, value)
            ]
            data.append((name, ["list", preconditions]))
        else:
            data.append((name, node_data(path, value)))
    return json.dumps(data, ensure_ascii=True).encode("ascii")


def taskfile_definitions(path: Path, source: bytes) -> TaskfileDefinitions:
    document = read_document(path, source, "Taskfile", UnsupportedTask)
    root = mapping(path, document)
    check_fields(path, root, ROOT_FIELDS, "Taskfile")
    check_shapes(path, root, scalars=ROOT_SCALARS, booleans=COMMON_BOOLEANS, lists=COMMON_LISTS)
    if "output" in root:
        output = root["output"][1]
        if scalar(path, output) not in {"group", "interleaved", "prefixed"}:
            raise invalid(path, output, "expected a literal Taskfile output mode")
    if "version" not in root or not re.fullmatch(
        r"3(?:\.[0-9]+){0,2}", scalar(path, root["version"][1])
    ):
        raise invalid(path, document, "expected a literal Taskfile version 3")
    if "tasks" not in root:
        raise invalid(path, document, "expected a 'tasks' mapping")
    tasks = mapping(path, root.pop("tasks")[1])
    shared = hashlib.sha256(fingerprint_data(path, root)).digest()
    definitions = []
    for name, (key, task) in tasks.items():
        if not TASK_NAME.fullmatch(name):
            raise invalid(path, key, f"unsupported Task name {name!r}")
        fields = mapping(path, task) if isinstance(task, MappingNode) else {}
        check_fields(path, fields, TASK_FIELDS, "Task")
        check_shapes(path, fields, scalars=TASK_SCALARS, booleans=TASK_BOOLEANS, lists=TASK_LISTS)
        if "prompt" in fields and isinstance(fields["prompt"][1], SequenceNode):
            scalar_list(path, fields["prompt"][1])
        elif "prompt" in fields:
            scalar(path, fields["prompt"][1])
        if "requires" in fields:
            check_requirements(path, fields["requires"][1])
        if "cmd" in fields and "cmds" in fields:
            raise invalid(path, task, "Task 'cmd' and 'cmds' may not coexist")
        for field in ("desc", "summary"):
            fields.pop(field, None)
        directory = path.parent
        if "dir" in fields:
            node = fields["dir"][1]
            value = scalar(path, node)
            if not re.fullmatch(r"[a-zA-Z0-9_./-]+", value) or Path(value).is_absolute():
                raise invalid(path, node, "Task 'dir' must be a literal relative path")
            directory /= value
        if isinstance(task, MappingNode):
            definition = fingerprint_data(path, fields)
        else:
            definition = json.dumps(node_data(path, task), ensure_ascii=True).encode("ascii")
        definitions.append(
            TaskDefinition(
                name,
                key.start_mark.line + 1,
                shared + b"\0" + definition,
                tuple(task_commands(path, task, fields, tasks, directory)),
            )
        )
    return TaskfileDefinitions(tuple(definitions), tuple(variable_commands(path, root)))
