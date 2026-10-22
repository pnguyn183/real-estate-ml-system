"""Exercise the HTTP cookie gateway without a Streamlit Runtime or database."""

from http.cookies import SimpleCookie
import unittest
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient

import web
from api_client import ValuationAPIError


class WebSessionTests(unittest.TestCase):
    handle = "A" * 43
    now = 1_900_000_000
    endpoint = "/homenest/auth/session"

    def setUp(self):
        restore_patch = patch.object(web, "restore_browser_session", return_value={
            "access_token": "private-server-token", "role": "User",
            "email": "alice@example.test", "expires_at": self.now + 300.9,
        })
        revoke_patch = patch.object(web, "revoke_browser_session")
        clock_patch = patch.object(web.time, "time", return_value=self.now)
        self.restore = restore_patch.start()
        self.revoke = revoke_patch.start()
        clock_patch.start()
        self.addCleanup(restore_patch.stop)
        self.addCleanup(revoke_patch.stop)
        self.addCleanup(clock_patch.stop)

    def client(self, origin="http://testserver", base_path="", extra_routes=()):
        app = Starlette(
            routes=web.create_session_routes(base_path=base_path) + list(extra_routes),
            middleware=[Middleware(web.BrowserSessionMiddleware)],
        )
        client = TestClient(app, base_url=origin)
        self.addCleanup(client.close)
        return client

    def assert_no_cookie(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def cookie(self, response):
        jar = SimpleCookie()
        jar.load(response.headers["set-cookie"])
        return jar[web.COOKIE_NAME]

    def test_post_and_delete_require_exact_origin(self):
        client = self.client()
        for method in ("POST", "DELETE"):
            for origin in (None, "null", "https://testserver", "http://other.test",
                           "http://testserver:81", "http://testserver/",
                           "http://testserver?x=1", "http://testserver#fragment"):
                with self.subTest(method=method, origin=origin):
                    headers = {} if origin is None else {"Origin": origin}
                    response = client.request(method, self.endpoint, headers=headers,
                                              json={"browser_session": self.handle})
                    self.assert_no_cookie(response, 403)
        self.restore.assert_not_called()
        self.revoke.assert_not_called()

    def test_post_requires_json_content_type(self):
        client = self.client()
        for content_type in (None, "text/plain", "application/x-www-form-urlencoded"):
            with self.subTest(content_type=content_type):
                headers = {"Origin": "http://testserver"}
                if content_type:
                    headers["Content-Type"] = content_type
                response = client.post(self.endpoint, headers=headers, content=b"{}")
                self.assert_no_cookie(response, 415)
        self.restore.assert_not_called()

    def test_malformed_json_and_wrong_payload_shapes_are_rejected(self):
        client = self.client()
        for body in (b"", b"{", b"\xff", b"null", b"[]", b'"string"', b"42",
                     b"{}", b'{"browser_session":null}', b'{"browser_session":43}',
                     b'{"browser_session":{}}', b'{"browser_session":[]}',
                     b'{"browser_session":"' + self.handle.encode() + b'","role":"Admin"}'):
            with self.subTest(body=body):
                response = client.post(self.endpoint, headers={
                    "Origin": "http://testserver", "Content-Type": "application/json",
                }, content=body)
                self.assert_no_cookie(response, 400)
        self.restore.assert_not_called()

    def test_oversized_request_is_rejected_before_restoring(self):
        client = self.client()
        response = client.post(self.endpoint, headers={
            "Origin": "http://testserver", "Content-Type": "application/json",
        }, content=b" " * 1025)
        self.assert_no_cookie(response, 413)
        self.restore.assert_not_called()

    def test_invalid_handle_format_is_rejected_before_restoring(self):
        client = self.client()
        for handle in ("", "A" * 42, "A" * 44, "/" + "A" * 42,
                       "A" * 42 + "=", "A" * 42 + " ", "A" * 42 + "\u00e9"):
            with self.subTest(handle=handle):
                response = client.post(self.endpoint, headers={"Origin": "http://testserver"},
                                       json={"browser_session": handle})
                self.assert_no_cookie(response, 400)
        self.restore.assert_not_called()

    def test_rejected_session_never_sets_cookie(self):
        self.restore.side_effect = ValuationAPIError("Expired or revoked")
        response = self.client().post(self.endpoint, headers={"Origin": "http://testserver"},
                                      json={"browser_session": self.handle})
        self.assert_no_cookie(response, 401)
        self.restore.assert_called_once_with(self.handle)

    def test_missing_expired_or_invalid_expiry_never_sets_cookie(self):
        client = self.client()
        for account in ({}, {"expires_at": None}, {"expires_at": "invalid"},
                        {"expires_at": self.now}, {"expires_at": self.now - 1},
                        {"expires_at": float("nan")}, {"expires_at": float("inf")}):
            with self.subTest(account=account):
                self.restore.return_value = account
                response = client.post(self.endpoint, headers={"Origin": "http://testserver"},
                                       json={"browser_session": self.handle})
                self.assert_no_cookie(response, 401)

    def test_success_sets_http_only_lax_cookie_with_actual_remaining_expiry(self):
        response = self.client().post(self.endpoint, headers={
            "Origin": "http://testserver", "Content-Type": "application/json; charset=utf-8",
        }, json={"browser_session": self.handle})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"persisted": True})
        self.assertEqual(response.headers["cache-control"], "no-store")
        cookie = self.cookie(response)
        self.assertEqual(cookie.value, self.handle)
        self.assertEqual(cookie["max-age"], "300")
        self.assertEqual(cookie["path"], "/")
        self.assertEqual(cookie["samesite"], "lax")
        self.assertTrue(cookie["httponly"])
        self.assertFalse(cookie["secure"])
        self.assertFalse(cookie["domain"])
        self.assertNotIn("private-server-token", response.text + str(response.headers))
        self.assertNotIn("alice@example.test", response.text + str(response.headers))

    def test_https_cookie_is_secure(self):
        response = self.client(origin="https://testserver").post(
            self.endpoint, headers={"Origin": "https://testserver"},
            json={"browser_session": self.handle},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.cookie(response)["secure"])

    def test_configured_prefix_scopes_route_and_cookie_for_set_and_delete(self):
        client = self.client(base_path="/nested/")
        endpoint = "/nested" + self.endpoint
        saved = client.post(endpoint, headers={"Origin": "http://testserver"},
                            json={"browser_session": self.handle})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.cookie(saved)["path"], "/nested/")
        self.assertEqual(client.post(self.endpoint, headers={"Origin": "http://testserver"},
                                     json={"browser_session": self.handle}).status_code, 404)
        deleted = client.delete(endpoint, headers={"Origin": "http://testserver"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.cookie(deleted)["path"], "/nested/")
        self.assertEqual(self.cookie(deleted)["max-age"], "0")
        self.revoke.assert_called_once_with(self.handle)

    def test_delete_revokes_cookie_and_clears_it_even_if_backend_unavailable(self):
        for backend_error in (False, True):
            with self.subTest(backend_error=backend_error):
                self.revoke.reset_mock()
                self.revoke.side_effect = ValuationAPIError("Unavailable") if backend_error else None
                client = self.client(origin="https://testserver")
                client.cookies.set(web.COOKIE_NAME, self.handle)
                response = client.delete(self.endpoint, headers={"Origin": "https://testserver"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"revoked": True})
                self.assertEqual(response.headers["cache-control"], "no-store")
                cookie = self.cookie(response)
                self.assertEqual(cookie.value, "")
                self.assertEqual(cookie["max-age"], "0")
                self.assertTrue(cookie["httponly"])
                self.assertTrue(cookie["secure"])
                self.assertEqual(cookie["samesite"], "lax")
                self.revoke.assert_called_once_with(self.handle)

    def test_delete_without_cookie_is_idempotent(self):
        client = self.client()
        for _ in range(2):
            response = client.delete(self.endpoint, headers={"Origin": "http://testserver"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.cookie(response)["max-age"], "0")
        self.revoke.assert_not_called()

    def test_middleware_overwrites_spoofed_header_for_http_and_websocket(self):
        async def echo_http(request: Request):
            return JSONResponse(request.headers.getlist("x-homenest-session"))

        async def echo_websocket(websocket):
            await websocket.accept()
            await websocket.send_json(websocket.headers.getlist("x-homenest-session"))
            await websocket.close()

        client = self.client(extra_routes=[
            Route("/echo", echo_http), WebSocketRoute("/echo-ws", echo_websocket),
        ])
        headers = [("X-HomeNest-Session", "spoofed"), ("x-homenest-session", "other")]
        self.assertEqual(client.get("/echo", headers=headers).json(), ["1"])
        self.assertEqual(client.get("/echo").json(), ["1"])
        with client.websocket_connect("/echo-ws", headers=dict(headers)) as websocket:
            self.assertEqual(websocket.receive_json(), ["1"])


if __name__ == "__main__":
    unittest.main()
