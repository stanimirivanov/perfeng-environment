"""Offline schema closure from the existing reviewed M04 contracts revision."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Never

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from scripts import kubernetes_benchmark_contract as contract
from scripts.control_plane_release import git, revision_reader


class Schemas:
    """Validate product evidence without downloading schemas or copying their definitions."""

    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        """Build a closed registry; unresolved references fail instead of accessing the network."""

        def refuse(uri: str) -> Never:
            raise ValueError("Acceptance schema resolution cannot access the network")

        self.documents = documents
        self.registry = Registry(retrieve=refuse).with_resources(
            (value["$id"], Resource.from_contents(value)) for value in documents.values()
        )

    def validate(self, name: str, value: Any) -> None:
        """Reject a malformed product artifact using the pinned schema and formats."""
        validator = Draft202012Validator(
            self.documents[name], registry=self.registry, format_checker=FormatChecker()
        )
        if next(iter(validator.iter_errors(value)), None) is not None:
            raise ValueError("Acceptance artifact violates its pinned product schema")


def load(root: Path) -> Schemas:
    """Read only exact Git objects already required by the qualification contract lock."""
    source = root.parent / "perfeng-contracts"
    read = revision_reader(source, contract.REPOSITORY, contract.REVISION)
    names = (
        git(source, "ls-tree", "-r", "--name-only", contract.REVISION, "schemas")
        .decode()
        .splitlines()
    )
    documents = {
        name.removeprefix("schemas/"): contract.document(read(name))
        for name in names
        if name.endswith(".schema.json")
    }
    if len(documents) != 20:
        raise ValueError("M04 acceptance requires the complete reviewed 20-schema closure")
    if (
        read(contract.SOURCE)
        != (root / "contracts/kubernetes-benchmark-evidence.schema.json").read_bytes()
    ):
        raise ValueError("Acceptance and qualification contract selections differ")
    return Schemas(documents)
