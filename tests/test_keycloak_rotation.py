"""Fixed provider key-lifecycle expectations; no infrastructure in offline tests."""

import unittest
from copy import deepcopy

from scripts import keycloak_qualification as fixture
from scripts import keycloak_rotation as rotation


class RotationTests(unittest.TestCase):
    def test_rotation_requires_a_distinct_active_key_and_overlap(self):
        rotation.check_keys("old", "new", {"old", "new"}, retired=False)
        for active, published in [("old", {"old"}), ("new", {"new"}), ("new", {"old"})]:
            with self.subTest(active=active, published=published), self.assertRaises(ValueError):
                rotation.check_keys("old", active, published, retired=False)
        rotation.check_keys("old", "new", {"new"}, retired=True)
        with self.assertRaises(ValueError):
            rotation.check_keys("old", "new", {"old", "new"}, retired=True)

    def test_only_separate_fixture_account_receives_key_administration(self):
        config = fixture.realm("ci-cert")
        original = deepcopy(config["clients"])
        rotation.configure(config, "rotation-cert")
        self.assertEqual(config["clients"][:-1], original)
        for client in original:
            self.assertFalse(client["fullScopeAllowed"])
        administrator = config["clients"][-1]
        self.assertEqual(administrator["clientId"], rotation.CLIENT)
        self.assertEqual(administrator["defaultClientScopes"], ["kc-admin-api"])
        self.assertFalse(administrator["standardFlowEnabled"])
        self.assertFalse(administrator["directAccessGrantsEnabled"])
        self.assertEqual(
            config["users"][-1]["clientRoles"], {"realm-management": ["view-realm", "manage-realm"]}
        )
        self.assertNotEqual(rotation.CLIENT, fixture.CI_CLIENT)

    def test_component_path_cannot_escape_the_fixture_api(self):
        self.assertEqual(
            rotation.component_path("test-provider"),
            "/admin/realms/perfeng-fixture/components/test-provider",
        )
        for identifier in ("../foreign", "id?admin=true", "id/other", ""):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                rotation.component_path(identifier)

    def test_old_key_must_be_explicitly_passive_before_retirement(self):
        rotation.check_passive({"keys": [{"kid": "old", "status": "PASSIVE"}]}, "old")
        with self.assertRaises(ValueError):
            rotation.check_passive({"keys": [{"kid": "old", "status": "ACTIVE"}]}, "old")

    def test_receipt_never_confuses_provider_rotation_with_cache_admission(self):
        partial = fixture.receipt(rotation.CHECKS[:-1])
        self.assertIn("real-key-rotation", partial["remainingGates"])
        complete = fixture.receipt(rotation.CHECKS)
        self.assertEqual(complete["admission"], "NOT_ADMITTED")
        self.assertIn("control-plane-real-key-rotation", complete["remainingGates"])
