"""Compose bounded route YAML as source-marked nodes without constructing objects."""

from __future__ import annotations

from pathlib import Path

import yaml
from yaml.events import AliasEvent, NodeEvent
from yaml.nodes import Node
from yaml.reader import ReaderError

MAX_YAML_DEPTH = 32
MAX_YAML_NODES = 8192


class UnsupportedYamlRoute(ValueError):
    def __init__(self, path: Path, line: int, reason: str) -> None:
        super().__init__(reason)
        self.location = f"{path.as_posix()}:{line}"


class RouteLoader(yaml.BaseLoader):
    def __init__(
        self, path: Path, text: str, label: str, error_type: type[UnsupportedYamlRoute]
    ) -> None:
        super().__init__(text)
        self.path = path
        self.label = label
        self.error_type = error_type
        self.depth = 0
        self.nodes = 0

    def compose_node(self, parent: Node | None, index: int) -> Node | None:
        event = self.peek_event()
        self.nodes += 1
        if self.depth == MAX_YAML_DEPTH or self.nodes > MAX_YAML_NODES:
            raise self.error_type(
                self.path, event.start_mark.line + 1, f"{self.label} YAML exceeds scan limits"
            )
        if isinstance(event, AliasEvent) or (
            isinstance(event, NodeEvent) and (event.anchor is not None or event.tag is not None)
        ):
            raise self.error_type(
                self.path,
                event.start_mark.line + 1,
                "YAML anchors, aliases and tags are unsupported",
            )
        self.depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1


def read_document(
    path: Path, source: bytes, label: str, error_type: type[UnsupportedYamlRoute]
) -> Node:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as error:
        line = source.count(b"\n", 0, error.start) + 1
        raise error_type(path, line, f"{label} must be UTF-8") from error

    try:
        loader = RouteLoader(path, text, label, error_type)
        try:
            document = loader.get_single_node()
        finally:
            loader.dispose()
    except ReaderError as error:
        line = text.count("\n", 0, error.position) + 1
        raise error_type(path, line, f"invalid {label} YAML") from error
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        raise error_type(path, mark.line + 1 if mark else 1, f"invalid {label} YAML") from error
    if document is None:
        raise error_type(path, 1, f"expected a {label} mapping")
    return document
