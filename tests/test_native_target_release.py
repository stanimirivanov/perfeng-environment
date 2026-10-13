"""Pinned target selection is independent of live clusters and sibling working-tree state."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import native_target_release as release


class NativeTargetReleaseTests(unittest.TestCase):
    def setUp(self):
        self.selection = release.load_selection()

    def test_current_selection_binds_published_image_and_chart(self):
        self.assertEqual(
            self.selection.deployment_revision, "a20633e150db4e940aba4355a32a7931b9a7f45a"
        )
        self.assertTrue(
            self.selection.image.endswith(
                "3f6179c9f3784be884761e7c6ceb4229c48631b8290d5cc65d0b488eef3b4386"
            )
        )
        self.assertEqual(
            release.content_identity(release.expected_files()),
            self.selection.candidate["targetSha256"],
        )

    def test_lock_rejects_unknown_mutable_and_wrong_topology_inputs(self):
        original = json.loads(release.LOCK.read_bytes())
        cases = []
        for section, field, value in (
            ("candidate", "image", release.REPOSITORY + ":latest"),
            ("candidate", "platform", "windows/amd64"),
            ("candidate", "targetSha256", "a" * 64),
            ("candidate", "extra", True),
            ("deployment", "revision", "main"),
            ("deployment", "namespace", "other"),
            ("deployment", "imagePullSecret", "other"),
        ):
            changed = copy.deepcopy(original)
            changed[section][field] = value
            cases.append(changed)
        changed = copy.deepcopy(original)
        changed["schemaVersion"] = True
        cases.append(changed)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock.json"
            for value in cases:
                with self.subTest(value=value):
                    path.write_text(json.dumps(value), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        release.load_selection(path)
            path.write_text(
                release.LOCK.read_text().replace(
                    '"schemaVersion": 1,', '"schemaVersion": 1, "schemaVersion": 1,', 1
                )
            )
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                release.load_selection(path)

    def test_application_rejects_pin_credentials_pruning_or_self_heal_drift(self):
        application = release.application_document(self.selection)
        release.validate_application(application, self.selection)
        for section, field, value in (
            ("source", "targetRevision", "main"),
            ("destination", "namespace", "other"),
            ("syncPolicy", "automated", {"enabled": True, "prune": True, "selfHeal": True}),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(application)
                changed["spec"][section][field] = value
                with self.assertRaisesRegex(ValueError, "Application differs"):
                    release.validate_application(changed, self.selection)

    def test_source_checks_read_pinned_records_and_exact_fixture_bytes(self):
        files = release.expected_files()
        records = {
            "charts/native-search/Chart.yaml": b'apiVersion: v2\nname: perfeng-native-search\nversion: 1.0.0\nappVersion: "1.0.0"\n',
            "releases/native-search/1.0.0/image.json": json.dumps(
                dict(self.selection.candidate)
            ).encode(),
        }

        def source(path):
            return files[Path(path).name]

        release.validate_sources(self.selection, records.__getitem__, source)
        records["releases/native-search/1.0.0/image.json"] = b"{}"
        with self.assertRaisesRegex(ValueError, "provenance differs"):
            release.validate_sources(self.selection, records.__getitem__, source)
        records["releases/native-search/1.0.0/image.json"] = json.dumps(
            dict(self.selection.candidate)
        ).encode()
        files["search.js"] = b"different"
        with self.assertRaisesRegex(ValueError, "source bytes differ"):
            release.validate_sources(self.selection, records.__getitem__, source)

    def test_local_expected_files_cannot_drift_to_another_candidate(self):
        with patch.object(release, "content_identity", return_value="b" * 64):
            with self.assertRaisesRegex(ValueError, "Local expected target bytes differ"):
                release.expected_files()

    def test_cli_passes_explicit_checkout_without_any_live_execution_option(self):
        with (
            patch.object(release, "validate", return_value=self.selection) as validate,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(release.main(["validate", "--k8s-root", "selected-source"]), 0)
        validate.assert_called_once_with(k8s_root=Path("selected-source"))

    def test_ci_keeps_target_and_control_plane_source_pins_separate(self):
        workflow = yaml.safe_load((release.ROOT / ".github/workflows/validate.yml").read_text())
        steps = workflow["jobs"]["validate"]["steps"]
        target = next(
            step
            for step in steps
            if step.get("with", {}).get("path") == "perfeng-native-target-k8s"
        )
        self.assertEqual(target["with"]["ref"], self.selection.deployment_revision)
        self.assertFalse(target["with"]["persist-credentials"])
        selection = next(
            step for step in steps if step.get("run") == "make release-selection-check"
        )
        self.assertEqual(selection["env"]["NATIVE_TARGET_K8S_ROOT"], "../perfeng-native-target-k8s")
        own = next(
            step for step in steps if step.get("with", {}).get("path") == "perfeng-environment"
        )
        self.assertEqual(own["with"]["fetch-depth"], 0)


if __name__ == "__main__":
    unittest.main()
