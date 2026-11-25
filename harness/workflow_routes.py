"""Read GitHub Actions command routes without evaluating expressions or running steps."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Set
from dataclasses import dataclass
from pathlib import Path

from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

if __package__:
    from .route_yaml import UnsupportedYamlRoute, read_document
else:
    from route_yaml import UnsupportedYamlRoute, read_document

WORKFLOW_FIELDS = {
    "name",
    "run-name",
    "on",
    "permissions",
    "env",
    "defaults",
    "concurrency",
    "jobs",
}
JOB_FIELDS = {
    "name",
    "permissions",
    "needs",
    "if",
    "runs-on",
    "environment",
    "concurrency",
    "outputs",
    "env",
    "defaults",
    "timeout-minutes",
    "strategy",
    "continue-on-error",
    "container",
    "services",
    "steps",
}
STEP_FIELDS = {
    "id",
    "if",
    "name",
    "uses",
    "run",
    "working-directory",
    "shell",
    "with",
    "env",
    "continue-on-error",
    "timeout-minutes",
}
RUN_DEFAULTS = {"shell", "working-directory"}
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
WORKFLOW_NAME_REFERENCE = re.compile(
    r"""\bgithub\s*(?:\.\s*workflow\b|\[\s*['"]workflow['"]\s*\])""", re.I
)
ACTION_REFERENCE = re.compile(r"(?:\./\S+|docker://\S+|[\w.-]+/[\w./-]+@[^@\s]+)")
WORKSPACE_DIRECTORY = re.compile(r"\$\{\{\s*github\.workspace\s*\}\}(?:/(.*))?")
LITERAL_DIRECTORY = re.compile(r"[\w./ -]+")

type Fields = dict[str, tuple[ScalarNode, Node]]


@dataclass(frozen=True)
class WorkflowStep:
    identity: str
    line: int
    definition: bytes
    command: tuple[Path, str] | None


@dataclass(frozen=True)
class ExecutionContext:
    digest: str
    reads_workflow_name: bool

    @classmethod
    def from_settings(cls, settings: dict[str, object]) -> ExecutionContext:
        encoded = json.dumps(settings, sort_keys=True)
        return cls(
            hashlib.sha256(encoded.encode()).hexdigest(),
            bool(WORKFLOW_NAME_REFERENCE.search(encoded.replace(r"\"", '"'))),
        )


class UnsupportedWorkflow(UnsupportedYamlRoute):
    pass


def is_workflow(path: Path) -> bool:
    return path.parent == Path(".github/workflows") and path.suffix in {".yml", ".yaml"}


class Workflow:
    def __init__(self, path: Path) -> None:
        self.path = path

    def invalid(self, node: Node, reason: str) -> UnsupportedWorkflow:
        return UnsupportedWorkflow(self.path, node.start_mark.line + 1, reason)

    def fields(self, node: Node, allowed: set[str] | None = None) -> Fields:
        if not isinstance(node, MappingNode):
            raise self.invalid(node, "expected a YAML mapping")
        fields = {}
        for key, value in node.value:
            if not isinstance(key, ScalarNode) or not key.value or key.value == "<<":
                raise self.invalid(key, "expected a literal YAML key; merge keys are unsupported")
            if key.value in fields:
                raise self.invalid(key, f"duplicate YAML key {key.value!r}")
            if allowed is not None and key.value not in allowed:
                raise self.invalid(key, f"unsupported workflow field {key.value!r}")
            fields[key.value] = key, value
        return fields

    def scalar(self, node: Node) -> str:
        if not isinstance(node, ScalarNode):
            raise self.invalid(node, "expected a YAML scalar")
        return node.value

    def data(self, node: Node) -> object:
        if isinstance(node, ScalarNode):
            return [node.style is None, node.value]
        if isinstance(node, SequenceNode):
            return [self.data(item) for item in node.value]
        return self.settings(self.fields(node))

    def settings(self, fields: Fields, excluded: Set[str] = frozenset()) -> dict[str, object]:
        return {name: self.data(node) for name, (_, node) in fields.items() if name not in excluded}

    def triggers(self, node: Node) -> object:
        if isinstance(node, SequenceNode):
            return [self.scalar(event) for event in node.value]
        if isinstance(node, ScalarNode):
            return self.scalar(node)
        events = {}
        for event, (_, options) in self.fields(node).items():
            if (
                isinstance(options, ScalarNode)
                and options.style is None
                and options.value
                in {
                    "",
                    "~",
                    "null",
                    "Null",
                    "NULL",
                }
            ):
                events[event] = None
                continue
            fields = self.fields(options)
            event_data = self.settings(
                fields, {"inputs"} if event == "workflow_dispatch" else set()
            )
            if event == "workflow_dispatch" and "inputs" in fields:
                event_data["inputs"] = {
                    name: self.settings(self.fields(value), {"description"})
                    for name, (_, value) in self.fields(fields["inputs"][1]).items()
                }
            events[event] = event_data
        return events

    def defaults(self, fields: Fields) -> dict[str, str]:
        if "defaults" not in fields:
            return {}
        defaults = self.fields(fields["defaults"][1], {"run"})
        if "run" not in defaults:
            return {}
        return {
            name: self.scalar(node)
            for name, (_, node) in self.fields(defaults["run"][1], RUN_DEFAULTS).items()
        }

    def environment(self, fields: Fields) -> dict[str, str]:
        if "env" not in fields:
            return {}
        return {
            name: self.scalar(node) for name, (_, node) in self.fields(fields["env"][1]).items()
        }

    def steps(self, document: Node, workflow: Fields, jobs: Fields) -> tuple[WorkflowStep, ...]:
        if "on" not in workflow:
            raise self.invalid(document, "workflow requires a trigger declaration")
        workflow_context = self.settings(
            workflow, {"name", "run-name", "jobs", "on", "defaults", "permissions", "env"}
        )
        workflow_context["on"] = self.triggers(workflow["on"][1])
        workflow_context["env"] = self.environment(workflow)
        workflow_context = ExecutionContext.from_settings(workflow_context)
        workflow_defaults = self.defaults(workflow)
        display_name = self.scalar(workflow["name"][1]) if "name" in workflow else None
        routes = []
        for job_position, (job_id, (key, job)) in enumerate(jobs.items(), start=1):
            if not IDENTIFIER.fullmatch(job_id):
                raise self.invalid(key, "expected a literal workflow job ID")
            job_fields = self.fields(job, JOB_FIELDS)
            steps = job_fields["steps"][1] if "steps" in job_fields else job
            if "steps" not in job_fields or not isinstance(steps, SequenceNode) or not steps.value:
                raise self.invalid(steps, "expected a nonempty workflow steps sequence")
            permissions = (
                self.data(job_fields["permissions"][1])
                if "permissions" in job_fields
                else self.data(workflow["permissions"][1])
                if "permissions" in workflow
                else None
            )
            job_context = ExecutionContext.from_settings(
                {
                    "permissions": permissions,
                    "position": job_position,
                    "env": self.environment(job_fields),
                    "settings": self.settings(
                        job_fields, {"name", "steps", "defaults", "permissions", "env"}
                    ),
                }
            )
            job_defaults = workflow_defaults | self.defaults(job_fields)
            seen_ids = set()
            for position, step in enumerate(steps.value, start=1):
                fields = self.fields(step, STEP_FIELDS)
                step_id = f"step-{position}"
                if "id" in fields:
                    id_node = fields["id"][1]
                    identifier = self.scalar(id_node)
                    if not IDENTIFIER.fullmatch(identifier) or identifier.casefold() in seen_ids:
                        raise self.invalid(id_node, "expected a unique literal workflow step ID")
                    step_id = f"id:{identifier}"
                    seen_ids.add(identifier.casefold())
                routes.append(
                    self.step(
                        step,
                        fields,
                        f"{job_id}/{step_id}",
                        position,
                        workflow_context,
                        job_context,
                        job_defaults,
                        display_name,
                    )
                )
        return tuple(routes)

    def step(
        self,
        step: Node,
        fields: Fields,
        identity: str,
        position: int,
        workflow_context: ExecutionContext,
        job_context: ExecutionContext,
        job_defaults: dict[str, str],
        display_name: str | None,
    ) -> WorkflowStep:
        if ("run" in fields) == ("uses" in fields):
            node = fields["uses"][0] if "uses" in fields else step
            raise self.invalid(node, "workflow step requires exactly one of run or uses")
        kind = "run" if "run" in fields else "uses"
        key, node = fields[kind]
        value = self.scalar(node)
        if not value.strip() or (
            kind == "uses" and ("${{" in value or not ACTION_REFERENCE.fullmatch(value))
        ):
            raise self.invalid(node, f"expected a literal nonempty {kind} declaration")

        settings = self.settings(fields, {"name", "env", "uses", "run", *RUN_DEFAULTS})
        definition: dict[str, object] = {
            "workflow": workflow_context.digest,
            "job": job_context.digest,
            "position": position,
            "env": self.environment(fields),
            "settings": settings,
            kind: value,
        }
        command = None
        if kind == "run":
            if "with" in fields:
                raise self.invalid(fields["with"][0], "with applies to uses steps")
            defaults = job_defaults | {
                name: self.scalar(fields[name][1]) for name in RUN_DEFAULTS if name in fields
            }
            definition["defaults"] = defaults
            directory = defaults.get("working-directory", ".")
            workspace = WORKSPACE_DIRECTORY.fullmatch(directory)
            if workspace is not None:
                directory = workspace[1] or "."
            if LITERAL_DIRECTORY.fullmatch(directory) and not Path(directory).is_absolute():
                if defaults.get("shell") != "python":
                    command = Path(directory), value
        else:
            for name in sorted(RUN_DEFAULTS & fields.keys()):
                raise self.invalid(fields[name][0], f"{name} applies to run steps")

        encoded = json.dumps(definition, sort_keys=True)
        # The workflow label becomes an execution input when a declaration
        # explicitly refers to the github.workflow context.
        if (
            workflow_context.reads_workflow_name
            or job_context.reads_workflow_name
            or WORKFLOW_NAME_REFERENCE.search(encoded.replace(r"\"", '"'))
        ):
            definition["referenced-workflow-name"] = display_name
            encoded = json.dumps(definition, sort_keys=True)
        return WorkflowStep(identity, key.start_mark.line + 1, encoded.encode(), command)


def workflow_steps(path: Path, source: bytes) -> tuple[WorkflowStep, ...]:
    document = read_document(path, source, "workflow", UnsupportedWorkflow)
    parser = Workflow(path)
    workflow = parser.fields(document, WORKFLOW_FIELDS)
    if "jobs" not in workflow:
        raise parser.invalid(document, "workflow requires a jobs mapping")
    jobs = parser.fields(workflow["jobs"][1])
    if not jobs:
        raise parser.invalid(workflow["jobs"][1], "workflow requires at least one job")
    return parser.steps(document, workflow, jobs)
