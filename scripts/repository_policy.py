"""Validate repository-local documentation and harness policy without network access."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

ISSUE_HEADINGS = [
    "Goal",
    "Scope",
    "Design decisions",
    "Security and operations",
    "Acceptance criteria",
    "Verification",
    "Out of scope",
]
PULL_REQUEST_HEADINGS = [
    "Linked work",
    "Outcome and scope",
    "Design, compatibility and operations",
    "Verification",
    "Limitations and review",
]
IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".local",
    ".ruff_cache",
    ".venv",
    "node_modules",
}
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*(?:<([^>]+)>|(\S+))", re.MULTILINE)
ADR_NAME = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
ADR_HEADING = re.compile(r"^# ADR(?:-| )(\d{4}):\s+\S")
METADATA = re.compile(r"^- ([A-Za-z][A-Za-z ]+):\s*(.*)$", re.MULTILINE)
PR_TITLE = re.compile(r"^\[#([1-9][0-9]*)\] \S")
PR_CLOSING_LINE = re.compile(r"\ACloses #([1-9][0-9]*)(?:\r?\n|\Z)")
PR_BRANCH = re.compile(r"(?:feature|fix)/issue-([1-9][0-9]*)\Z")


@dataclass(frozen=True, order=True)
class Violation:
    path: str
    line: int
    rule: str
    message: str
    fix: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}; fix: {self.fix}"


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _content_lines(text: str) -> list[str]:
    visible: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("~~~") or line.lstrip().startswith("```"):
            fenced = not fenced
            visible.append("")
        else:
            visible.append("" if fenced else line)
    return visible


def _slug(heading: str) -> str:
    value = re.sub(r"<[^>]+>", "", heading).strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return re.sub(r"[\s-]+", "-", value).strip("-")


def _anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for line in _content_lines(text):
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        base = _slug(match.group(1))
        count = counts.get(base, 0)
        counts[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def _destination(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    return value.split(maxsplit=1)[0]


def _markdown_violations(root: Path, path: Path) -> list[Violation]:
    relative = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    visible = "\n".join(_content_lines(text))
    violations: list[Violation] = []

    word_count = len(re.findall(r"\b\w+\b", visible))
    sections = len(re.findall(r"^##\s+", visible, re.MULTILINE))
    selective = relative.startswith("docs/") and any(
        term in relative.lower()
        for term in ("architecture", "security", "migration", "operational", "harness")
    )
    if (word_count >= 800 or sections > 5 or selective) and not re.search(
        r"^## TL;DR\s*$", visible, re.MULTILINE
    ):
        violations.append(
            Violation(
                relative,
                1,
                "docs.tldr",
                "long or selectively read documentation has no TL;DR",
                "add a useful `## TL;DR` immediately after the title and metadata",
            )
        )

    candidates: list[tuple[int, str]] = []
    for match in LINK.finditer(visible):
        candidates.append((_line(visible, match.start()), _destination(match.group(1))))
    for match in REFERENCE.finditer(visible):
        candidates.append(
            (_line(visible, match.start()), _destination(match.group(1) or match.group(2)))
        )

    for line, destination in candidates:
        if not destination or re.match(r"^[a-z][a-z0-9+.-]*:", destination, re.I):
            continue
        location, _, fragment = destination.partition("#")
        target = path if not location else (path.parent / unquote(location)).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            violations.append(
                Violation(
                    relative,
                    line,
                    "docs.link",
                    f"local link escapes the repository: {destination}",
                    "link to a repository-owned path",
                )
            )
            continue
        if not target.exists():
            violations.append(
                Violation(
                    relative,
                    line,
                    "docs.link",
                    f"local link target does not exist: {destination}",
                    "correct the path or remove the stale link",
                )
            )
            continue
        if (
            fragment
            and target.is_file()
            and fragment not in _anchors(target.read_text(encoding="utf-8"))
        ):
            violations.append(
                Violation(
                    relative,
                    line,
                    "docs.anchor",
                    f"heading anchor does not exist: {destination}",
                    "link to an existing heading",
                )
            )
    return violations


def _template_violations(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    templates = [
        (root / ".github/ISSUE_TEMPLATE/work-item.md", ISSUE_HEADINGS, "template.issue"),
        (root / ".github/PULL_REQUEST_TEMPLATE.md", PULL_REQUEST_HEADINGS, "template.pull-request"),
    ]
    for path, expected, rule in templates:
        relative = path.relative_to(root).as_posix()
        if not path.is_file():
            violations.append(
                Violation(
                    relative,
                    1,
                    rule,
                    "required template is missing",
                    "restore the canonical template",
                )
            )
            continue
        text = path.read_text(encoding="utf-8")
        headings = re.findall(r"^##\s+(.+?)\s*$", text, re.MULTILINE)
        if headings != expected:
            violations.append(
                Violation(
                    relative,
                    1,
                    rule,
                    f"section order is {headings!r}",
                    f"use the canonical section order {expected!r}",
                )
            )
        if "**Milestone:**" not in text and "- Milestone:" not in text:
            violations.append(
                Violation(
                    relative,
                    1,
                    rule,
                    "milestone field is missing",
                    "restore the exact milestone prompt",
                )
            )
        if path.name == "PULL_REQUEST_TEMPLATE.md":
            requirements = [
                (
                    text.startswith("Closes #<issue-number>\n"),
                    "begin with `Closes #<issue-number>`",
                ),
                (
                    "[#<issue-number>]" in text,
                    "document the mandatory `[#<issue-number>]` PR title prefix",
                ),
                (
                    "- [ ] Human review and merge:" in text,
                    "retain the human review/merge pause checklist item",
                ),
            ]
            for satisfied, fix in requirements:
                if not satisfied:
                    violations.append(
                        Violation(
                            relative,
                            1,
                            "template.pull-request.workflow",
                            "automated PR workflow requirement is missing",
                            fix,
                        )
                    )

    comment_path = root / ".github/PR_FIX_COMMENT_TEMPLATE.md"
    comment_text = comment_path.read_text(encoding="utf-8") if comment_path.is_file() else ""
    if not (
        comment_text.startswith("### Fix Applied\n")
        and all(
            re.search(rf"^\*\*{field}:\*\*\s+\S", comment_text, re.MULTILINE)
            for field in ("Issue", "Resolution", "Verification")
        )
    ):
        violations.append(
            Violation(
                ".github/PR_FIX_COMMENT_TEMPLATE.md",
                1,
                "template.fix-comment",
                "structured revision comment template is missing or incomplete",
                "start with `### Fix Applied` and include Issue, Resolution and Verification fields",
            )
        )
    return violations


def _adr_violations(root: Path) -> list[Violation]:
    decisions = root / "docs/decisions"
    if not decisions.is_dir():
        return []
    index_path = decisions / "README.md"
    index = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    violations: list[Violation] = []
    numbers: list[int] = []
    for path in sorted(decisions.glob("[0-9][0-9][0-9][0-9]-*.md")):
        match = ADR_NAME.fullmatch(path.name)
        if match is None:
            continue
        number = int(match.group(1))
        numbers.append(number)
        text = path.read_text(encoding="utf-8")
        heading = ADR_HEADING.match(text)
        if heading is None or int(heading.group(1)) != number:
            violations.append(
                Violation(
                    path.relative_to(root).as_posix(),
                    1,
                    "adr.heading",
                    "ADR heading and filename number disagree",
                    "use `# ADR NNNN: Decision title`",
                )
            )
        metadata = {key: value for key, value in METADATA.findall(text)}
        for required in ("Status", "Date"):
            if not metadata.get(required):
                violations.append(
                    Violation(
                        path.relative_to(root).as_posix(),
                        1,
                        "adr.metadata",
                        f"{required} metadata is missing",
                        f"add `- {required}: ...`",
                    )
                )
        milestone = metadata.get("Milestone")
        if milestone and not re.fullmatch(r"M\d{2} — .+", milestone):
            violations.append(
                Violation(
                    path.relative_to(root).as_posix(),
                    1,
                    "adr.milestone",
                    "milestone does not use the published Perfeng form",
                    "use `MNN — Outcome`",
                )
            )
        if f"({path.name})" not in index:
            violations.append(
                Violation(
                    index_path.relative_to(root).as_posix(),
                    1,
                    "adr.index",
                    f"{path.name} is not indexed",
                    "add a link to the ADR index",
                )
            )
    if numbers and numbers != list(range(1, max(numbers) + 1)):
        violations.append(
            Violation(
                "docs/decisions/README.md",
                1,
                "adr.sequence",
                f"ADR sequence is {numbers!r}",
                "allocate sequential numbers without reusing gaps",
            )
        )
    return violations


def check_repository(root: Path) -> list[Violation]:
    root = root.resolve()
    violations: list[Violation] = []
    for path in root.rglob("*.md"):
        if any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts):
            continue
        if ".github" in path.parts and (
            "ISSUE_TEMPLATE" in path.parts or path.name == "PULL_REQUEST_TEMPLATE.md"
        ):
            continue
        violations.extend(_markdown_violations(root, path))
    violations.extend(_template_violations(root))
    violations.extend(_adr_violations(root))
    return sorted(set(violations))


def check_pull_request_metadata(title: str, body: str, branch: str) -> list[str]:
    """Require one issue identity across the PR title, closing line, and branch."""
    errors: list[str] = []
    title_match = PR_TITLE.match(title)
    body_match = PR_CLOSING_LINE.match(body)
    branch_match = PR_BRANCH.fullmatch(branch)
    if title_match is None:
        errors.append("PR title must start with [#N] followed by a summary")
    if body_match is None:
        errors.append("PR body must begin with a standalone Closes #N line")
    if branch_match is None:
        errors.append("PR branch must be feature/issue-N or fix/issue-N")
    numbers = {
        match.group(1) for match in (title_match, body_match, branch_match) if match is not None
    }
    if len(numbers) > 1:
        errors.append("PR title, closing line, and branch must reference the same issue")
    return errors


def check_pull_request_event(path: Path) -> list[str]:
    """Read GitHub's local event file without network access or PR body logging."""
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["pull request event file could not be read as JSON"]
    if not isinstance(event, dict):
        return ["pull request event object is missing"]
    request = event.get("pull_request")
    if not isinstance(request, dict):
        return ["pull request metadata is missing from the event"]
    author = request.get("user")
    if isinstance(author, dict) and author.get("login") == "dependabot[bot]":
        return []
    head = request.get("head")
    if not isinstance(head, dict):
        return ["pull request branch metadata is missing from the event"]
    title, body, branch = request.get("title"), request.get("body"), head.get("ref")
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(branch, str):
        return ["pull request title, body, or branch is missing from the event"]
    return check_pull_request_metadata(title, body, branch)


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--pr-event":
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        errors = (
            check_pull_request_event(Path(event_path))
            if event_path
            else ["GITHUB_EVENT_PATH is missing"]
        )
        for error in errors:
            print(f"pr-policy: {error}")
        if errors:
            return 1
        print("pr-policy: issue-linked pull request metadata passed")
        return 0
    if len(sys.argv) != 1:
        print("usage: repository_policy.py [--pr-event]")
        return 2
    root = Path(__file__).resolve().parents[1]
    violations = check_repository(root)
    for violation in violations:
        print(violation)
    if violations:
        print(f"repo-policy: found {len(violations)} violation(s)")
        return 1
    print("repo-policy: repository policy checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
