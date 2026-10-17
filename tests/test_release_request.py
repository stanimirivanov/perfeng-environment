"""Offline compatibility checks for the environment release-request contract."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from scripts.workspace import unique_object

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "deployments/release-request-v1.schema.json"
EXAMPLE = ROOT / "tests/fixtures/release-request-v1.json"


class ReleaseRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)
        cls.example = json.loads(
            EXAMPLE.read_text(encoding="utf-8"), object_pairs_hook=unique_object
        )

    def request(self) -> dict[str, Any]:
        return copy.deepcopy(self.example)

    def test_historical_example_matches_the_versioned_schema(self) -> None:
        self.assertEqual(list(self.validator.iter_errors(self.example)), [])
        self.assertEqual(
            self.example["selection"]["revision"], "80bd42a6bd4f4da86b751fdea7fe21e59a523e64"
        )
        self.assertEqual(len(self.example["selection"]["lockDigests"]), 3)

    def test_mutable_or_foreign_selection_is_rejected(self) -> None:
        for field, value in (
            ("revision", "main"),
            ("revision", "80bd42a"),
            ("repository", "https://github.com/other/environment.git"),
            ("gitopsPath", "../gitops/environments/local"),
        ):
            with self.subTest(field=field, value=value):
                request = self.request()
                request["selection"][field] = value
                self.assertFalse(self.validator.is_valid(request))

    def test_lock_paths_and_digests_are_exact(self) -> None:
        for path, digest in (
            ("../deployments/local-control-plane.json", "sha256:" + "a" * 64),
            ("deployments/../local-control-plane.json", "sha256:" + "a" * 64),
            ("deployments/local-control-plane.json", "latest"),
            ("deployments/local-control-plane.json", "sha256:" + "A" * 64),
        ):
            with self.subTest(path=path, digest=digest):
                request = self.request()
                request["selection"]["lockDigests"] = {path: digest}
                self.assertFalse(self.validator.is_valid(request))

    def test_forward_only_migration_requires_manual_activation_and_identity(self) -> None:
        request = self.request()
        request["promotion"]["activation"] = "automated"
        self.assertFalse(self.validator.is_valid(request))

        request = self.request()
        del request["promotion"]["migration"]["id"]
        self.assertFalse(self.validator.is_valid(request))

        request = self.request()
        request["promotion"]["migration"] = {"mode": "none"}
        request["promotion"]["activation"] = "automated"
        request["environment"]["name"] = "perfeng-staging"
        request["selection"]["gitopsPath"] = "gitops/environments/staging"
        self.assertTrue(self.validator.is_valid(request))

    def test_local_request_requires_all_locks_and_manual_promotion(self) -> None:
        request = self.request()
        del request["selection"]["lockDigests"]["deployments/local-native-target.json"]
        self.assertFalse(self.validator.is_valid(request))

        request = self.request()
        request["promotion"]["migration"] = {"mode": "none"}
        request["promotion"]["activation"] = "automated"
        self.assertFalse(self.validator.is_valid(request))

    def test_upgrade_can_bind_an_exact_prior_request_identity(self) -> None:
        request = self.request()
        request["promotion"]["expectedCurrent"] = "sha256:" + "a" * 64
        self.assertTrue(self.validator.is_valid(request))

    def test_unknown_or_secret_bearing_fields_and_invalid_expectation_are_rejected(self) -> None:
        for section, field, value in (
            ("root", "token", "secret"),
            ("environment", "kubeconfig", "C:/private/kubeconfig"),
            ("selection", "branch", "main"),
            ("promotion", "approval", "self-approved"),
            ("promotion", "expectedCurrent", "main"),
        ):
            with self.subTest(section=section, field=field):
                request = self.request()
                target = request if section == "root" else request[section]
                target[field] = value
                self.assertFalse(self.validator.is_valid(request))

    def test_duplicate_json_keys_are_rejected_before_schema_validation(self) -> None:
        document = EXAMPLE.read_text(encoding="utf-8").replace(
            '"schemaVersion": 1,', '"schemaVersion": 1, "schemaVersion": 1,', 1
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            json.loads(document, object_pairs_hook=unique_object)


if __name__ == "__main__":
    unittest.main()
