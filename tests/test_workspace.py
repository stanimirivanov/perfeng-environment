import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.workspace import Repository, State, bootstrap, canonical_origin, inspect, load_manifest

URL = "https://github.com/stanimirivanov/perfeng-k6.git"


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        printer = patch("builtins.print")
        printer.start()
        self.addCleanup(printer.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.environment = self.root / "perfeng-environment"
        self.environment.mkdir()
        self.manifest = self.environment / "workspace.json"
        self.data: dict[str, Any] = {
            "schemaVersion": 1,
            "repositories": [
                {
                    "name": "perfeng-k6",
                    "path": "../perfeng-k6",
                    "url": URL,
                    "source": {"branch": "main"},
                }
            ],
        }
        self.repo = Repository("perfeng-k6", self.root / "perfeng-k6", URL, "main")

    def load(self):
        self.manifest.write_text(json.dumps(self.data), encoding="utf-8")
        with patch("scripts.workspace.git", return_value=""):
            return load_manifest(self.manifest)

    def test_portable_paths(self):
        self.assertEqual(self.load()[0].path, self.root / "perfeng-k6")

    def test_linked_destination_is_rejected(self):
        external = self.root / "external"
        external.mkdir()
        if os.name == "nt":
            # A directory junction requires no symlink privilege on Windows.
            importlib.import_module("_winapi").CreateJunction(str(external), str(self.repo.path))
        else:
            self.repo.path.symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.load()

    def test_invalid_manifest(self):
        cases = [
            ("path", "../../outside"),
            ("path", "/absolute"),
            ("path", "../different"),
            ("url", "https://user:secret@github.com/stanimirivanov/perfeng-k6.git"),
            ("url", "file:///local"),
            ("name", "../escape"),
            ("source", {"image": "image:latest"}),
            ("source", {"branch": "--upload-pack=bad"}),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                original = self.data["repositories"][0][key]
                self.data["repositories"][0][key] = value
                with self.assertRaises(ValueError):
                    self.load()
                self.data["repositories"][0][key] = original

    def test_duplicate_repository(self):
        self.data["repositories"].append(self.data["repositories"][0])
        with self.assertRaises(ValueError):
            self.load()

    def test_duplicate_json_keys(self):
        self.manifest.write_text('{"schemaVersion":1,"schemaVersion":1,"repositories":[]}')
        with self.assertRaises(ValueError):
            load_manifest(self.manifest)

    def test_existing_directory_and_file_not_replaced(self):
        self.repo.path.write_text("user data")
        self.assertEqual(inspect(self.repo).state, "conflict")
        self.assertEqual(bootstrap([self.repo], dry_run=False), 1)
        self.assertEqual(self.repo.path.read_text(), "user data")

    def test_dry_run_never_clones(self):
        with patch("scripts.workspace.git") as git:
            self.assertEqual(bootstrap([self.repo], dry_run=True), 0)
            git.assert_not_called()
        self.assertFalse(self.repo.path.exists())

    def test_missing_checkout_is_reserved_then_cloned(self):
        def clone(*args):
            self.assertTrue(self.repo.path.is_dir())
            self.assertEqual(list(self.repo.path.iterdir()), [])
            self.assertEqual(
                args,
                ("clone", "--branch", "main", "--single-branch", "--", URL, str(self.repo.path)),
            )
            return ""

        with patch("scripts.workspace.git", side_effect=clone):
            self.assertEqual(bootstrap([self.repo], dry_run=False), 0)

    def test_dirty_issue_branch_is_left_untouched(self):
        state = State(self.repo, "existing", "issue-17", "abcdef", True)
        with (
            patch("scripts.workspace.inspect", return_value=state),
            patch("scripts.workspace.git") as git,
        ):
            self.assertEqual(bootstrap([self.repo], dry_run=False), 0)
            git.assert_not_called()

    def test_preflight_prevents_partial_work_on_conflict(self):
        other = Repository("other", self.root / "other", URL, "main")
        with (
            patch(
                "scripts.workspace.inspect",
                side_effect=[State(self.repo, "missing"), State(other, "wrong-origin")],
            ),
            patch("scripts.workspace.git") as git,
        ):
            self.assertEqual(bootstrap([self.repo, other], dry_run=False), 1)
            git.assert_not_called()
        self.assertFalse(self.repo.path.exists())

    def test_clone_failure_retains_partial_directory(self):
        with patch("scripts.workspace.git", side_effect=ValueError("clone failed")):
            self.assertEqual(bootstrap([self.repo], dry_run=False), 1)
        self.assertTrue(self.repo.path.is_dir())

    def test_race_does_not_use_someone_elses_directory(self):
        self.repo.path.mkdir()
        with (
            patch("scripts.workspace.inspect", return_value=State(self.repo, "missing")),
            patch("scripts.workspace.git") as git,
        ):
            with self.assertRaises(FileExistsError):
                bootstrap([self.repo], dry_run=False)
            git.assert_not_called()

    def test_ssh_origin_spellings(self):
        self.assertEqual(
            canonical_origin(URL), canonical_origin("git@github.com:stanimirivanov/perfeng-k6.git")
        )
        self.assertEqual(
            canonical_origin(URL),
            canonical_origin("ssh://git@github.com/stanimirivanov/perfeng-k6.git"),
        )

    def test_parent_repository_is_not_a_checkout(self):
        self.repo.path.mkdir()
        with patch("scripts.workspace.git", return_value=str(self.root)):
            self.assertEqual(inspect(self.repo).state, "conflict")

    def test_wrong_origin_is_reported(self):
        self.repo.path.mkdir()
        with patch(
            "scripts.workspace.git",
            side_effect=[str(self.repo.path), "https://github.com/other/repo.git"],
        ):
            self.assertEqual(inspect(self.repo).state, "wrong-origin")


if __name__ == "__main__":
    unittest.main()
