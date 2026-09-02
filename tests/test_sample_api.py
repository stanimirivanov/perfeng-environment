import json
import unittest
from http.client import HTTPConnection

from scripts.check_sample_api import fixture, load_api


def request(port, method, path, body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


class SampleApiTests(unittest.TestCase):
    def test_all_scenario_endpoints(self):
        with fixture() as port:
            for method, path, body, status, expected in [
                ("GET", "/api/v1/cart", None, 200, {"cartId": "example-cart"}),
                (
                    "POST",
                    "/api/v1/checkout",
                    '{"cartId":"example-cart"}',
                    201,
                    {"orderId": "fixture-order"},
                ),
                ("GET", "/api/v1/search?q=random", None, 200, {"results": ["fixture-result"]}),
                ("GET", "/api/v1/users/user-1-0", None, 200, {"id": "fixture-user"}),
                (
                    "PUT",
                    "/api/v1/users/user-1-0/preferences",
                    '{"theme":"dark"}',
                    200,
                    {"updated": True},
                ),
            ]:
                actual, payload = request(port, method, path, body)
                self.assertEqual(actual, status)
                self.assertEqual(json.loads(payload), expected)

    def test_failure_modes_leave_health_available(self):
        for mode, method, path, body, status in [
            ("cart-failure", "GET", "/api/v1/cart", None, 500),
            ("search-failure", "GET", "/api/v1/search", None, 500),
            ("preferences-failure", "PUT", "/api/v1/users/u/preferences", "{}", 500),
        ]:
            with fixture(mode) as port:
                self.assertEqual(request(port, method, path, body)[0], status)
                self.assertEqual(request(port, "GET", "/healthz")[0], 200)

    def test_order_validation_failure_modes(self):
        for mode, expected in [("missing-order", b"{}"), ("malformed-order", b"not-json")]:
            with fixture(mode) as port:
                self.assertEqual(
                    request(port, "POST", "/api/v1/checkout", '{"cartId":"example-cart"}'),
                    (201, expected),
                )

    def test_malformed_or_non_object_json_rejected(self):
        with fixture() as port:
            for body in ["not-json", "[]", "null", ""]:
                self.assertEqual(request(port, "POST", "/api/v1/checkout", body)[0], 400)
            self.assertEqual(
                request(port, "POST", "/api/v1/checkout", '{"cartId":"other"}')[0], 400
            )

    def test_oversized_or_ambiguous_body_rejected(self):
        with fixture() as port:
            self.assertEqual(request(port, "POST", "/api/v1/checkout", "x" * 16385)[0], 413)
            self.assertEqual(
                request(port, "POST", "/api/v1/checkout", "", {"Transfer-Encoding": "chunked"})[0],
                400,
            )
            self.assertEqual(
                request(port, "POST", "/api/v1/checkout", "", {"Content-Length": "-1"})[0], 400
            )

    def test_unknown_paths_and_versions_do_not_pass(self):
        with fixture() as port:
            for path in ["/api/v2/cart", "/missing", "/api/v1/users/u/extra"]:
                self.assertEqual(request(port, "GET", path)[0], 404)
            self.assertEqual(request(port, "POST", "/api/v1/search", "{}")[0], 404)

    def test_stateless_responses_do_not_echo_input(self):
        with fixture() as port:
            for _ in range(2):
                status, payload = request(
                    port,
                    "PUT",
                    "/api/v1/users/private-user/preferences",
                    '{"private":"secret"}',
                    {"Authorization": "Bearer private"},
                )
                self.assertEqual((status, json.loads(payload)), (200, {"updated": True}))

    def test_unknown_mode_fails_before_binding(self):
        with self.assertRaises(ValueError):
            load_api().create_server(mode="typo")


if __name__ == "__main__":
    unittest.main()
