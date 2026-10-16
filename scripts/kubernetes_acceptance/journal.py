"""Create-only acceptance evidence with append-only, bounded partial-progress checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts import kubernetes_benchmark as qualification
from scripts.kubernetes_acceptance.errors import AcceptanceError


class Journal:
    """Keep safe identities before writes; never overwrite a previous session or receipt."""

    def __init__(self, root: Path, name: str) -> None:
        """Open a new ordinary results journal exclusively; caller must close it."""
        self.receipt = qualification.destination(root / "results" / f"{name}.json", root)
        path = qualification.destination(
            root / "results" / f"{name}-journal.json", root
        ).with_suffix(".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.stream = path.open("xb")
        self.size = 0
        self.digest = hashlib.sha256()

    def record(self, event: str, **safe_fields: Any) -> None:
        """Flush only caller-selected non-secret evidence; bound the complete journal to 1 MiB."""
        content = (
            json.dumps(
                {"event": event, "at": datetime.now(UTC).isoformat(), **safe_fields},
                allow_nan=False,
            )
            + "\n"
        ).encode()
        if self.size + len(content) > 1 << 20:
            raise AcceptanceError("Acceptance journal reached its 1 MiB bound; evidence retained")
        self.stream.write(content)
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.size += len(content)
        self.digest.update(content)
        identity = safe_fields.get("runId", "")
        print(f"M04 acceptance: {event} {identity}".rstrip(), flush=True)

    def finish(self, value: dict[str, Any]) -> None:
        """Atomically publish success only after every required live assertion passed."""
        qualification.destination(self.receipt, self.receipt.parent.parent)
        value = {
            **value,
            "journal": {
                "file": self.path.name,
                "sha256": self.digest.hexdigest(),
                "sizeBytes": self.size,
            },
        }
        qualification.publish(
            self.receipt, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
        )

    def close(self) -> None:
        """Close the journal while preserving all success and partial-failure evidence."""
        self.stream.close()
