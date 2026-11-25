"""Exercise command route policy through the offline guard CLI."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "harness/command_routes.py"


class CommandRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.checkout = Path(directory.name)
        subprocess.run(["git", "init", "--quiet"], cwd=self.checkout, check=True, timeout=10)
        self.write("harness/command-routes.toml", "routes = []\n")

    def write(self, path: str, source: str) -> Path:
        target = self.checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        return target

    def guard(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR)],
            cwd=self.checkout,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def test_reports_new_workflow_routes_without_running_them_or_scanning_display_names(
        self,
    ) -> None:
        self.write("tools/check.py", "print('verification')\n")
        self.write("tools/helper.py", "def value(): return 42\n")
        self.write(
            ".github/workflows/fixture.yaml",
            """\
name: "Instructions for python tools/helper.py"
on: [pull_request]
permissions: {contents: read}
jobs:
  verify:
    name: "Instructions for python tools/helper.py"
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: tools
    steps:
      - name: "Instructions for python tools/helper.py"
        uses: actions/setup-python@0123456789012345678901234567890123456789
        with:
          python-version: "3.12"
      - id: project-check
        run: >-
          python
          check.py && touch shell-marker
""",
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(
            "unreviewed-route: .github/workflows/fixture.yaml:13: "
            "'workflow:.github/workflows/fixture.yaml#verify/step-1'",
            result.stderr,
        )
        self.assertIn(
            "unreviewed-route: .github/workflows/fixture.yaml:17: "
            "'workflow:.github/workflows/fixture.yaml#verify/id:project-check'",
            result.stderr,
        )
        self.assertIn("unreviewed-route: tools/check.py:1", result.stderr)
        self.assertNotIn("tools/helper.py", result.stderr)
        self.assertFalse((self.checkout / "tools/shell-marker").exists())

    def record_workflow_fixture(self, result: subprocess.CompletedProcess[str]) -> None:
        records = []
        for identity, definition in re.findall(
            r"'(workflow:[^']+)' has no reviewed record[^\n]*(sha256:[a-f0-9]{64})",
            result.stderr,
        ):
            records.append(
                f'[[routes]]\nid = "{identity}"\nowner = "Fixture owner"\n'
                f'purpose = "Review the exact workflow fixture declaration."\n'
                f'definition = "{definition}"\n'
            )
        self.assertEqual(len(records), 3, result.stderr)
        self.write("harness/command-routes.toml", "\n".join(records))

    def test_reviewed_workflow_preserves_setup_trust_and_inherited_settings(self) -> None:
        path = ".github/workflows/reviewed.yml"
        workflow = """\
name: Reviewed fixture
run-name: Run description
on:
  pull_request_target:
    types: [opened, synchronize]
  workflow_dispatch:
    inputs:
      fixture:
        description: Which fixture to run
        default: normal
permissions: {contents: read}
env: {MODE: preview}
defaults:
  run:
    shell: bash
jobs:
  metadata:
    name: Metadata display name
    runs-on: ubuntu-latest
    env: {LEVEL: safe}
    defaults:
      run:
        working-directory: tools
    steps:
      - name: Exact base checkout
        uses: actions/checkout@0123456789012345678901234567890123456789 # checkout
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          persist-credentials: false
      - id: verify
        name: Run only the reviewed fixture
        env: {OUTPUT: report, name: literal-input}
        run: >-
          touch
          shell-marker
      - uses: actions/upload-artifact@0123456789012345678901234567890123456789
        with: {name: report, path: report.json}
"""
        self.write(path, workflow)
        self.record_workflow_fixture(self.guard())
        accepted = self.guard()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.write(
            path,
            workflow.replace("Reviewed fixture", "Renamed fixture")
            .replace("Run description", "New run description")
            .replace("Which fixture to run", "Pick the fixture")
            .replace("Metadata display name", "Metadata new name")
            .replace("Run only the reviewed fixture", "New step title")
            .replace("# checkout", "# revised comment")
            .replace("run: >-\n          touch\n          shell-marker", "run: touch shell-marker"),
        )
        descriptive = self.guard()
        self.assertEqual(descriptive.returncode, 0, descriptive.stderr)
        for original, replacement, target in (
            ("shell-marker", "shell-marker && touch second-marker", "id:verify"),
            ("MODE: preview", "MODE: execute", "id:verify"),
            ("LEVEL: safe", "LEVEL: elevated", "id:verify"),
            ("shell: bash", "shell: sh", "id:verify"),
            ("working-directory: tools", "working-directory: other", "id:verify"),
            ("name: literal-input", "name: changed-input", "id:verify"),
            ("contents: read", "contents: write", "step-1"),
            ("pull_request_target:", "pull_request:", "step-1"),
            ("pull_request.base.sha", "pull_request.head.sha", "step-1"),
            ("persist-credentials: false", "persist-credentials: true", "step-1"),
            ("@012345", "@abcdef", "step-1"),
            ("name: report, path:", "name: changed, path:", "step-3"),
        ):
            with self.subTest(original=original):
                self.write(path, workflow.replace(original, replacement, 1))
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertRegex(
                    result.stderr,
                    rf"changed-definition: {re.escape(path)}:[0-9]+: "
                    rf"'workflow:{re.escape(path)}#metadata/{target}'",
                )
        self.assertFalse((self.checkout / "tools/shell-marker").exists())
        self.assertFalse((self.checkout / "shell-marker").exists())

    def test_reports_ambiguous_workflow_declarations_at_the_source(self) -> None:
        path = ".github/workflows/ambiguous.yml"
        source = "on: push\njobs:\n  verify:\n    runs-on: ubuntu-latest\n    steps:\n"
        for declaration, expected in (
            ("      - run: true\n        run: false\n", "7: duplicate YAML key 'run'"),
            ("      - run: true\n        uses: vendor/tool@v1\n", "7: workflow step requires"),
            ("      - run: [echo, unexpected]\n", "6: expected a YAML scalar"),
            ("      - run: &commands echo unexpected\n", "6: YAML anchors"),
            ("      - uses: '${{ inputs.action }}'\n", "6: expected a literal nonempty uses"),
            ("      - uses: './${{inputs.action}}'\n", "6: expected a literal nonempty uses"),
        ):
            with self.subTest(declaration=declaration):
                self.write(path, source + declaration)
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"unsupported-workflow: {path}:{expected}", result.stderr)
                self.assertIn("fix: use a supported literal workflow declaration", result.stderr)

    def test_reviews_versioned_hook_bytes_and_mode_without_executing_hooks(self) -> None:
        hook = self.write(".githooks/pre-commit", "touch unexpected-marker\n")
        self.write(".githooks/pre-push.sample", "touch sample-marker\n")
        local_hook = self.write(".git/hooks/pre-push", "#!/bin/sh\ntouch local-marker\n")
        local_hook.chmod(0o755)
        subprocess.run(["git", "add", ".githooks"], cwd=self.checkout, check=True, timeout=10)
        subprocess.run(
            ["git", "config", "core.fsmonitor", str(local_hook)],
            cwd=self.checkout,
            check=True,
            timeout=10,
        )
        denied = self.guard()
        self.assertEqual(denied.returncode, 1, denied.stderr)
        self.assertIn(
            "unreviewed-route: .githooks/pre-commit:1: 'hook:.githooks/pre-commit'",
            denied.stderr,
        )
        self.assertNotIn("script:.githooks/pre-commit", denied.stderr)
        self.assertNotIn("pre-push", denied.stderr)
        self.write(
            "harness/command-routes.toml",
            """\
[[routes]]
id = "hook:.githooks/pre-commit"
owner = "Fixture owner"
purpose = "Exercise a versioned hook without running it."
definition = "sha256:0705999b86cf5187492f0c7c70e4dcf4ecbb38d72d83e5aa503907277bdda284"
""",
        )
        accepted = self.guard()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        hook.chmod(0o755)
        changed_mode = self.guard()
        self.assertEqual(changed_mode.returncode, 1, changed_mode.stderr)
        self.assertIn("changed-definition: .githooks/pre-commit:1:", changed_mode.stderr)
        self.assertNotIn("script:.githooks/pre-commit", changed_mode.stderr)
        hook.chmod(0o644)
        self.write(".githooks/pre-commit", "touch changed-marker\n")
        changed_body = self.guard()
        self.assertEqual(changed_body.returncode, 1, changed_body.stderr)
        self.assertIn("changed-definition: .githooks/pre-commit:1:", changed_body.stderr)
        self.assertFalse((self.checkout / "unexpected-marker").exists())
        self.assertFalse((self.checkout / "changed-marker").exists())
        self.assertFalse((self.checkout / "local-marker").exists())

    def test_reports_a_new_task_route_without_starting_task_or_its_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tools:
            fake_task = Path(tools) / "task"
            fake_task.write_text("#!/bin/sh\ntouch task-marker\n", encoding="utf-8")
            fake_task.chmod(0o755)
            with patch.dict(environ, {"PATH": tools + ":" + environ["PATH"]}):
                self.write(
                    "Taskfile.yml",
                    'version: "3"\ntasks:\n  local:fixture:\n    cmds: [touch shell-marker]\n',
                )
                result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(
            "unreviewed-route: Taskfile.yml:3: 'task:Taskfile.yml#local:fixture'", result.stderr
        )
        self.assertIn("harness.md#reviewing-command-routes", result.stderr)
        self.assertFalse((self.checkout / "task-marker").exists())
        self.assertFalse((self.checkout / "shell-marker").exists())

    def test_reviews_task_commands_calls_prerequisites_and_execution_settings(self) -> None:
        taskfile = """\
version: '3'
env:
  MODE: preview
tasks:
  setup: {cmds: [touch setup-marker]}
  status: {cmds: [touch status-marker]}
  local:run:
    desc: Run the fixture
    summary: |
      Preview the fixture.
    deps: [setup]
    prompt: Run the fixture?
    preconditions:
      - sh: test -d .
        msg: Run from a directory
    silent: true
    cmds:
      - task: status
        vars: {MODE: preview, summary: included}
      - touch shell-marker {{.CLI_ARGS}}
"""
        self.write(
            "harness/command-routes.toml",
            """\
[[routes]]
id = "task:Taskfile.yml#local:run"
owner = "Fixture owner"
purpose = "Exercise the reviewed operator task."
definition = "sha256:77e688deee38bc54dc25a855936f249759354b19c37775175d61e641994e9726"

[[routes]]
id = "task:Taskfile.yml#setup"
owner = "Fixture owner"
purpose = "Exercise a reviewed prerequisite task."
definition = "sha256:5582388e19874f7a5f9bdee5a73ff581e153f1dc8a6eef2a280b189aaeb48e26"

[[routes]]
id = "task:Taskfile.yml#status"
owner = "Fixture owner"
purpose = "Exercise a reviewed nested task."
definition = "sha256:de67d44cc4fc550bc73aa3f47e3f33e06232354b59670a50d6c00d5a41b76a32"
""",
        )
        self.write("help_only.py", "def value():\n    return 42\n")
        self.write(
            "Taskfile.yml",
            taskfile.replace("Run the fixture\n", "Run the updated fixture\n")
            .replace("Preview the fixture.", "For context, see python help_only.py")
            .replace("Run from a directory", "Read python help_only.py"),
        )
        accepted = self.guard()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        for original, updated in (
            ("touch shell-marker {{.CLI_ARGS}}", "touch another-marker"),
            ("deps: [setup]", "deps: [status]"),
            ("task: status", "task: setup"),
            ("MODE: preview, summary: included", "MODE: apply, summary: included"),
            ("summary: included", "summary: changed"),
            ("prompt: Run the fixture?", "prompt: Continue?"),
            ("sh: test -d .", "sh: test -f ."),
            ("silent: true", "silent: false"),
            ("  MODE: preview", "  MODE: apply"),
            ("    deps: [setup]", "    dir: elsewhere\n    deps: [setup]"),
            ("    deps: [setup]", "    deps: [{task: setup, vars: {MODE: changed}}]"),
        ):
            with self.subTest(updated=updated):
                self.write("Taskfile.yml", taskfile.replace(original, updated))
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(
                    "changed-definition: Taskfile.yml:7: 'task:Taskfile.yml#local:run'",
                    result.stderr,
                )
        for marker in ("setup-marker", "status-marker", "shell-marker", "another-marker"):
            self.assertFalse((self.checkout / marker).exists())

    def test_task_command_evidence_uses_the_declared_working_directory(self) -> None:
        for path in ("helper.py", "ops/helper.py", "ops/run/helper.py", "ops/shared.py"):
            self.write(path, "raise SystemExit('the guard must not invoke this file')\n")
        self.write(
            "ops/taskfile.yaml",
            """\
version: '3'
vars:
  SHARED:
    sh: python shared.py
tasks:
  run:
    dir: run
    cmd: {cmd: python helper.py}
  call:
    cmd: {task: run}
""",
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("'task:ops/taskfile.yaml#call'", result.stderr)
        self.assertIn("'script:ops/run/helper.py'", result.stderr)
        self.assertIn("'script:ops/shared.py'", result.stderr)
        self.assertNotIn("'script:helper.py'", result.stderr)
        self.assertNotIn("'script:ops/helper.py'", result.stderr)

    def test_rejects_ambiguous_or_unmodeled_task_declarations(self) -> None:
        for declaration, line, reason in (
            ("tasks:\n  job: true\n  job: false\n", 4, "duplicate YAML key 'job'"),
            (
                "tasks:\n  job:\n    cmds: [true]\n    cmds: [false]\n",
                5,
                "duplicate YAML key 'cmds'",
            ),
            ("tasks:\n  job: &anchor {cmds: [true]}\n", 3, "YAML anchors"),
            ("tasks:\n  job: {<<: {cmds: [true]}}\n", 3, "merge keys"),
            ("tasks: [\n", 3, "invalid Taskfile YAML"),
            ("output: []\ntasks: {}\n", 2, "expected a YAML scalar"),
            ("tasks:\n  job: {silent: []}\n", 3, "expected a YAML boolean"),
            ("tasks:\n  job: {silent: 'true'}\n", 3, "expected a YAML boolean"),
            (
                "vars: {VAR: {sh: true, typo: ignored}}\ntasks: {}\n",
                2,
                "unsupported Task variable field 'typo'",
            ),
            (
                "tasks:\n  job: {env: {VAR: {sh: true, typo: ignored}}}\n",
                3,
                "unsupported Task variable field 'typo'",
            ),
            (
                "tasks:\n  job: {cmds: [{cmd: true, vars: {VAR: {typo: ignored}}}]}\n",
                3,
                "unsupported Task variable field 'typo'",
            ),
            (
                "tasks:\n  job: {preconditions: [{sh: true, msg: []}]}\n",
                3,
                "expected a YAML scalar",
            ),
            ("tasks: {}\n---\ntasks: {}\n", 3, "invalid Taskfile YAML"),
            ("includes: {ops: ops.yml}\ntasks: {}\n", 2, "unsupported Taskfile field 'includes'"),
            ("tasks:\n  'ops:*': true\n", 3, "unsupported Task name"),
            (
                "tasks:\n  job: {aliases: [alias], cmds: [true]}\n",
                3,
                "unsupported Task field 'aliases'",
            ),
            (
                "tasks:\n  job: {cmds: [{task: '{{.TARGET}}'}]}\n",
                3,
                "expected a literal name",
            ),
            ("tasks:\n  job: {deps: [missing]}\n", 3, "is undeclared in this Taskfile"),
            ("tasks:\n  job: " + "[" * 33 + "]" * 33 + "\n", 3, "YAML exceeds scan limits"),
        ):
            with self.subTest(declaration=declaration):
                self.write("Taskfile.yaml", "version: '3'\n" + declaration)
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"unsupported-task: Taskfile.yaml:{line}:", result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("unreviewed-route", result.stderr)

    def test_bounds_task_diagnostic_previews(self) -> None:
        field = "unsupported_" + "x" * 20_000
        for declaration, preview in (
            (f"? '{field}'\n: []\ntasks: {{}}\n", "unsupported Taskfile field"),
            (f"tasks:\n  job: {{cmd: {{task: '{field}!'}}}}\n", "unsupported Task call"),
        ):
            with self.subTest(preview=preview):
                self.write("Taskfile.yml", "version: '3'\n" + declaration)
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(preview, result.stderr)
                self.assertIn("unsupported_xxx", result.stderr)
                self.assertIn("...", result.stderr)
                self.assertLess(len(result.stderr), 600)

    def test_rejects_a_taskfile_that_would_shadow_another(self) -> None:
        self.write("taskfile.yml", "version: '3'\ntasks: {}\n")
        self.write("Taskfile.dist.yml", "version: '3'\ntasks: {}\n")
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(
            "unsupported-task: taskfile.yml:1: takes precedence over Taskfile.dist.yml",
            result.stderr,
        )

    def test_reports_the_location_of_nonprintable_task_yaml(self) -> None:
        self.write("Taskfile.yml", "version: '3'\ntasks: {}\n\x00\n")
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unsupported-task: Taskfile.yml:3: invalid Taskfile YAML", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_reports_a_new_make_target_without_running_the_recipe(self) -> None:
        self.write("Makefile", "# Test command surface.\nfixture:\n\ttouch unexpected-marker\n")
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unreviewed-route: Makefile:2: 'make:Makefile#fixture'", result.stderr)
        self.assertFalse((self.checkout / "unexpected-marker").exists())

    def test_reviews_make_recipe_prerequisites_and_referenced_variable_defaults(self) -> None:
        makefile = (
            "ROOT ?= local\n"
            "RUN = run --root $(ROOT)\n"
            "aggregate: fixture\n"
            ".PHONY: aggregate fixture\n"
            "fixture:\n"
            "\t$(RUN) touch unexpected-marker\n"
            "ifneq ($(strip $(EXTRA)),)\n"
            "\ttouch conditional-marker\n"
            "endif\n"
        )
        baseline = """\
[[routes]]
id = "make:Makefile#aggregate"
owner = "Fixture owner"
purpose = "Run the fixture aggregate."
definition = "sha256:12a611fc5768919845790c04e208d716c214991d79bc421bd87c6a88799cb69e"

[[routes]]
id = "make:Makefile#fixture"
owner = "Fixture owner"
purpose = "Exercise the reviewed variable and conditional recipe."
definition = "sha256:a401a442a8a6b59119dcfb18f6f4f4c05648bc7114a22a411972ef3e8c47df32"
"""
        self.write("harness/command-routes.toml", baseline)
        self.write("Makefile", makefile)
        accepted = self.guard()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        self.write("Makefile", "# Updated route description.\n" + makefile)
        self.write(
            "harness/command-routes.toml",
            baseline.replace("Run the fixture aggregate.", "Run the local fixture."),
        )
        described = self.guard()
        self.assertEqual(described.returncode, 0, described.stderr)

        for before, after, location in (
            ("touch unexpected-marker", "touch another-marker", "5: 'make:Makefile#fixture'"),
            ("aggregate: fixture", "aggregate:", "3: 'make:Makefile#aggregate'"),
            ("ROOT ?= local", "ROOT ?= remote", "5: 'make:Makefile#fixture'"),
            ("strip $(EXTRA)", "strip $(OTHER)", "5: 'make:Makefile#fixture'"),
            (".PHONY: aggregate fixture", ".PHONY: aggregate", "5: 'make:Makefile#fixture'"),
        ):
            with self.subTest(after=after):
                self.write("Makefile", makefile.replace(before, after))
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"changed-definition: Makefile:{location}", result.stderr)
        self.assertFalse((self.checkout / "unexpected-marker").exists())
        self.assertFalse((self.checkout / "conditional-marker").exists())

    def test_rejects_potential_implicit_recipes_without_running_make(self) -> None:
        self.write("fixture.c", "int fixture(void) { return 0; }\n")
        for declaration, location in (
            (".PHONY: aggregate\naggregate: fixture.o\nfixture.o:\n", 4),
            (".PHONY: aggregate\naggregate: fixture.o\n", 3),
            (
                "fixture.o:\nifneq ($(strip $(ENABLE)),)\n\t$(CC) fixture.c\nendif\n",
                2,
            ),
        ):
            with self.subTest(declaration=declaration):
                self.write("Makefile", "CC = touch implicit-marker\n" + declaration)
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(
                    f"unsupported-make: Makefile:{location}: ",
                    result.stderr,
                )
                self.assertIn("implicit Make recipes are unsupported", result.stderr)
                self.assertFalse((self.checkout / "implicit-marker").exists())
                self.assertFalse((self.checkout / "fixture.o").exists())

    def test_rejects_opaque_make_expansions_without_evaluating_them(self) -> None:
        for declaration, location in (
            ("FLAG := $(shell touch parse-marker)\nfixture:\n", 1),
            ("fixture:\n\t@echo '$(shell touch parse-marker)'\n", 2),
            ("$(shell touch parse-marker):\n", 1),
        ):
            with self.subTest(declaration=declaration):
                self.write("Makefile", declaration)
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(
                    f"unsupported-make: Makefile:{location}: unsupported Make expansion",
                    result.stderr,
                )
                self.assertFalse((self.checkout / "parse-marker").exists())

    def test_rejects_a_make_assignment_with_an_escaped_comment(self) -> None:
        self.write("Makefile", "RUN = echo \\#literal\nfixture:\n\t$(RUN)\n")
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(
            "unsupported-make: Makefile:1: escaped Make declarations are unsupported",
            result.stderr,
        )

    def test_disregards_make_declaration_comments_for_script_invocation(self) -> None:
        self.write("helper.py", "def value():\n    return 42\n")
        self.write(
            "GNUmakefile",
            "FIXTURE = python helper.py\n"
            "fixture: # See python helper.py for an example.\n"
            "\t@echo $(FIXTURE)\n",
        )
        result = self.guard()
        self.assertIn("unreviewed-route: GNUmakefile:2:", result.stderr)
        self.assertNotIn("helper.py", result.stderr)

        self.write("GNUmakefile", "fixture:\n\tpython helper.py\n")
        result = self.guard()
        self.assertIn("unreviewed-route: helper.py:1", result.stderr)

    def test_rejects_make_dollar_passthrough_and_unbound_assignments(self) -> None:
        for source, line, reason in (
            (
                'FLAG = changed\nfixture:\n\t@echo "$$FLAG"\n',
                3,
                "escaped Make dollars are unsupported",
            ),
            (
                "FLAG = changed\nCOPY = $$FLAG\nfixture:\n\t@echo $(COPY)\n",
                2,
                "escaped Make dollars are unsupported",
            ),
            (
                "FLAG = changed\nfixture:\n\tprintenv FLAG\n",
                1,
                "assignment 'FLAG' has no reviewed Make target binding",
            ),
            (
                "COPY := $(FLAG)\nFLAG = changed\nfixture:\n\t@echo $(COPY)\n",
                2,
                "assignment 'FLAG' has no reviewed Make target binding",
            ),
        ):
            with self.subTest(source=source):
                self.write("Makefile", source)
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"unsupported-make: Makefile:{line}: {reason}", result.stderr)

    def test_accepts_tab_indented_comments_before_a_make_target(self) -> None:
        self.write("Makefile", "\t# Building the fixture.\nfixture:\n\ttrue\n")
        result = self.guard()
        self.assertIn("unreviewed-route: Makefile:2:", result.stderr)
        self.assertNotIn("unsupported-make", result.stderr)

    def test_counts_make_physical_lines_with_a_final_newline(self) -> None:
        makefile = ".PHONY: fixture\n" + "# Comment\n" * 4095
        self.write("Makefile", makefile)
        result = self.guard()
        self.assertIn("unreviewed-route: Makefile:1:", result.stderr)
        self.assertNotIn("unsupported-make", result.stderr)

        self.write("Makefile", makefile + "# Extra line\n")
        result = self.guard()
        self.assertIn("unsupported-make: Makefile:4097: Makefile exceeds 4096 lines", result.stderr)

    def test_reports_the_line_of_invalid_make_utf8(self) -> None:
        self.write("makefile", "").write_bytes(b"# Comment\nfixture:\n\t\xff\n")
        result = self.guard()
        self.assertIn("unsupported-make: makefile:3: Makefile must be UTF-8", result.stderr)

    def test_rejects_a_makefile_that_would_shadow_another(self) -> None:
        self.write("Makefile", "fixture:\n")
        self.write("GNUmakefile", "fixture:\n")
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unsupported-make: GNUmakefile:1:", result.stderr)
        self.assertIn("takes precedence over Makefile", result.stderr)

    def test_accepts_reviewed_non_executable_shell_script_without_running_it(self) -> None:
        self.write("launch.sh", "touch unexpected-marker\n").chmod(0o644)
        self.write("Makefile", "fixture:\n\tbash launch.sh\n")
        self.write(
            "harness/command-routes.toml",
            """
[[routes]]
id = "script:launch.sh"
owner = "Fixture owner"
purpose = "Exercise a non-executable shell entrypoint."
definition = "sha256:0705999b86cf5187492f0c7c70e4dcf4ecbb38d72d83e5aa503907277bdda284"

[[routes]]
id = "make:Makefile#fixture"
owner = "Fixture owner"
purpose = "Call the reviewed shell fixture."
definition = "sha256:cec90692693b7115564f530437ead65437c03bcede1911cd5c899be870ce88c8"
""",
        )
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("command-routes: reviewed command routes match", result.stdout)
        self.assertFalse((self.checkout / "unexpected-marker").exists())

    def test_reports_unreviewed_file_and_module_routes_in_working_tree(self) -> None:
        self.write(".gitignore", "ignored-*\n")
        self.write("ignored-entry.sh", "#!/bin/sh\nexit 0\n")
        self.write("plain_runner.py", "print('directly called')\n").chmod(0o644)
        self.write("Makefile", "fixture:\n\tpython3 -B plain_runner.py\n")
        self.write("package/__main__.py", "print('module entrypoint')\n")
        self.write("package/helper.py", "def imported():\n    return 42\n")
        self.write(
            "tests/test_value.py",
            'import unittest\n\nif __name__ == "__main__":\n    unittest.main()\n',
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unreviewed-route: plain_runner.py:1", result.stderr)
        self.assertIn("unreviewed-route: package/__main__.py:1", result.stderr)
        self.assertIn("fix: reuse an existing route", result.stderr)
        self.assertIn("harness.md#reviewing-command-routes", result.stderr)
        self.assertNotIn("ignored-entry.sh", result.stderr)
        self.assertNotIn("helper.py", result.stderr)
        self.assertNotIn("test_value.py", result.stderr)

    def test_accepts_imported_helpers_and_flags_a_new_main_guard(self) -> None:
        self.write("package/helper.py", "def imported():\n    return 42\n")
        self.write("package/caller.py", "from .helper import imported\n")
        initial = self.guard()
        self.assertEqual(initial.returncode, 0, initial.stderr)
        self.write(
            "package/caller.py",
            'from .helper import imported\n\nif "__main__" == __name__:\n    print(imported())\n',
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unreviewed-route: package/caller.py:3", result.stderr)

    def test_reports_changed_and_removed_working_tree_definitions(self) -> None:
        self.write("launch.sh", "touch unexpected-marker\n")
        self.write("removed.sh", "touch unexpected-marker\n")
        self.write(
            "harness/command-routes.toml",
            """
[[routes]]
id = "script:launch.sh"
owner = "Fixture owner"
purpose = "Exercise a changed working-tree script."
definition = "sha256:0705999b86cf5187492f0c7c70e4dcf4ecbb38d72d83e5aa503907277bdda284"

[[routes]]
id = "script:removed.sh"
owner = "Fixture owner"
purpose = "Exercise working-tree deletion of a tracked route."
definition = "sha256:0705999b86cf5187492f0c7c70e4dcf4ecbb38d72d83e5aa503907277bdda284"
""",
        )
        subprocess.run(
            ["git", "add", "launch.sh", "removed.sh"], cwd=self.checkout, check=True, timeout=10
        )
        self.assertEqual(self.guard().returncode, 0)
        self.write("launch.sh", "exit 0\n")
        (self.checkout / "removed.sh").unlink()
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("changed-definition: launch.sh:1", result.stderr)
        self.assertIn(
            "missing-route: harness/command-routes.toml: 'script:removed.sh'", result.stderr
        )

    def test_discovers_a_separately_invoked_pod_payload_from_an_imported_file(self) -> None:
        self.write("package/client.py", "print('pod client')\n")
        self.write(
            "package/transport.py",
            """
from pathlib import Path

from package import client

def run_in_pod():
    return run(["kubectl", "exec", "pod", "--", "python", "-c",
                Path(client.__file__).read_text(encoding="utf-8")])
""",
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unreviewed-route: package/transport.py:8", result.stderr)
        self.assertIn("'payload:package/transport.py#package.client'", result.stderr)

    def test_discovers_named_payloads_passed_to_the_running_python_executable(self) -> None:
        self.write(
            "runner.py",
            """import sys

RUN_MODULE = "import runpy; " "runpy.run_module('provider')"
OFFLINE = "print('offline check')"

def commands():
    return (
        [sys.executable, "-I", "-c", RUN_MODULE],
        ["unshare", "-Urn", sys.executable, "-IB", "-c", OFFLINE],
    )
""",
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(
            "unreviewed-route: runner.py:8: 'payload:runner.py#RUN_MODULE'", result.stderr
        )
        self.assertIn("unreviewed-route: runner.py:9: 'payload:runner.py#OFFLINE'", result.stderr)

    def test_detects_changes_to_the_reviewed_artifact_upload_pod_programs(self) -> None:
        source = """def upload_script(reference):
    return f"printf '%s' '{reference}'"

def publish(kube, pod_name, source):
    cluster.run([*kube, "exec", "-i", pod_name, "--", "sh", "-c", "cat > /tmp/source"],
                input_data=source.content)
    cluster.run([*kube, "exec", pod_name, "--", "sh", "-c", upload_script(source.reference)])
"""
        self.write("scripts/artifact_upload.py", source)
        self.write(
            "harness/command-routes.toml",
            """
[[routes]]
id = "payload:scripts/artifact_upload.py#stage_source"
owner = "Fixture owner"
purpose = "Stage the fixture source."
definition = "sha256:2d20c7f8f60786cb258c0d91a8e50fcdacf5d9b0155e83b1f1ff6b05cda481a4"

[[routes]]
id = "payload:scripts/artifact_upload.py#upload_script"
owner = "Fixture owner"
purpose = "Build the fixture program."
definition = "sha256:8a29d638a866ceb2f5a2bf619154915082f9fb7c011b371fe10635f5c35ea1cc"
""",
        )
        accepted = self.guard()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        for original, replacement, line, name in (
            ("cat > /tmp/source", "cat > /tmp/other", 5, "stage_source"),
            ("printf '%s' '{reference}'", "echo '{reference}'", 7, "upload_script"),
        ):
            with self.subTest(name=name):
                self.write("scripts/artifact_upload.py", source.replace(original, replacement))
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(
                    f"changed-definition: scripts/artifact_upload.py:{line}: "
                    f"'payload:scripts/artifact_upload.py#{name}'",
                    result.stderr,
                )

    def test_output_only_recipes_do_not_make_import_helpers_callable(self) -> None:
        self.write("helper.py", "def value():\n    return 42\n")
        self.write(
            "Makefile",
            "help:\n"
            "\t@echo 'For development, python helper.py prints its value.'\n"
            '\t@printf "%s\\n" "Try python helper.py for details."\n',
        )
        self.write(
            "harness/command-routes.toml",
            """\
[[routes]]
id = "make:Makefile#help"
owner = "Fixture owner"
purpose = "Display the help text."
definition = "sha256:9fe8cb4f229fc3892dd22c8d09ae6c225c528f54ebca1dba4c4d0b08eb160302"
""",
        )
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.write("Makefile", "run:\n\t@echo 'Starting'; python helper.py\n")
        result = self.guard()
        self.assertIn("unreviewed-route: helper.py:1", result.stderr)

    def test_oversized_source_fails_with_its_path_and_no_route_proposal(self) -> None:
        checkout_file = self.write("oversized.py", "pass\n")
        with checkout_file.open("ab") as stream:
            stream.truncate(2 * 1024 * 1024 + 1)
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("scan-failed: .: oversized.py exceeds the 2 MiB source limit", result.stderr)
        self.assertNotIn("unreviewed-route", result.stderr)

    def test_source_only_shell_fragment_stays_with_its_caller(self) -> None:
        self.write("shell/caller.sh", '#!/bin/sh\n. "./shell/library.sh"\n')
        self.write("shell/library.sh", "#!/bin/sh\nhello() { echo hello; }\n")
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unreviewed-route: shell/caller.sh:1", result.stderr)
        self.assertNotIn("shell/library.sh", result.stderr)
        self.write("Makefile", "fixture:\n\tbash shell/library.sh\n")
        result = self.guard()
        self.assertIn("unreviewed-route: shell/library.sh:1", result.stderr)
