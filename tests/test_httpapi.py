import unittest
from unittest import mock

import _path  # noqa: F401
from _server import FakeHost
from contribution_mirror import httpapi
from contribution_mirror.httpapi import Api, ApiError


class Client(unittest.TestCase):
    def test_get_sends_headers_and_decodes_json(self):
        with FakeHost() as host:
            host.routes["/x"] = (200, {"ok": 1})
            self.assertEqual(Api(host.url, {"Authorization": "Bearer t"}).get("/x", a="1"), {"ok": 1})
            self.assertEqual((host.requests[0]["auth"], host.requests[0]["query"]), ("Bearer t", {"a": "1"}))

    def test_list_parameters_are_repeated(self):
        with FakeHost() as host:
            host.routes["/x"] = (200, [])
            Api(host.url).get("/x", state=["OPEN", "MERGED"])
            self.assertEqual(host.requests[0]["query"]["state"], ["OPEN", "MERGED"])

    def test_pages_walk_until_a_short_page(self):
        with FakeHost() as host:
            host.routes["/items"] = lambda q: (200, list(range(100)) if q["page"] == "1" else [100, 101])
            self.assertEqual(len(list(Api(host.url).pages("/items"))), 102)
            self.assertEqual([r["query"]["page"] for r in host.requests], ["1", "2"])

    def test_forbidden_is_an_error_not_a_retry_loop(self):
        with FakeHost() as host, mock.patch.object(httpapi.time, "sleep") as sleep:
            host.routes["/x"] = (403, {"message": "no"})
            with self.assertRaises(ApiError) as cm:
                Api(host.url).get("/x")
            self.assertEqual(cm.exception.status, 403)
            sleep.assert_not_called()
            self.assertEqual(len(host.requests), 1)

    def test_server_errors_are_retried_then_succeed(self):
        with FakeHost() as host, mock.patch.object(httpapi.time, "sleep"):
            answers = iter([(500, {}), (502, {}), (200, {"ok": True})])
            host.routes["/x"] = lambda q: next(answers)
            self.assertEqual(Api(host.url).get("/x"), {"ok": True})

    def test_a_link_to_another_host_is_never_followed(self):
        with FakeHost() as host:
            with self.assertRaisesRegex(ValueError, "another host"):
                Api(host.url, {"Authorization": "Bearer secret"}).get("https://evil.example.com/steal")

    def test_absolute_next_link_on_the_same_host_works(self):
        with FakeHost() as host:
            host.routes["/page2"] = (200, {"values": [2]})
            self.assertEqual(Api(host.url).get(f"{host.url}/page2?x=1"), {"values": [2]})
            self.assertEqual(host.requests[0]["query"], {"x": "1"})


class TokensStayOnHttps(unittest.TestCase):
    def test_plain_http_to_a_real_host_is_refused(self):
        with self.assertRaisesRegex(ValueError, "refusing to send credentials"):
            Api("http://git.example.com/api", {"Authorization": "Bearer t"})

    def test_https_and_loopback_http_are_fine(self):
        Api("https://api.github.com")
        Api("http://127.0.0.1:1234")
        Api("http://localhost:1234")


if __name__ == "__main__":
    unittest.main()
