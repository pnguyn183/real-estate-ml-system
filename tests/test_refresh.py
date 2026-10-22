"""Browser login restoration and its cookie lifecycle, with no real user data."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import browser_auth
from api_client import ValuationAPIError


class BrowserAuthTests(unittest.TestCase):
    handle = "A" * 43

    def setUp(self):
        self.state = {}
        self.context = SimpleNamespace(
            headers={"x-homenest-session": "1"},
            cookies={browser_auth.COOKIE_NAME: self.handle},
        )
        self.streamlit = SimpleNamespace(session_state=self.state, context=self.context)
        patcher = patch.object(browser_auth, "st", self.streamlit)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.account = {
            "access_token": "server-verified-token", "expires_at": 4_000_000_000,
            "email": "alice@example.test", "full_name": "Alice", "role": "User",
        }

    def test_gateway_header_is_required_before_restoring_or_remembering(self):
        self.context.headers.clear()
        with patch.object(browser_auth, "restore_browser_session") as restore, \
                patch.object(browser_auth, "create_browser_session") as create:
            browser_auth.restore_login()
            browser_auth.remember_login(self.account)
        restore.assert_not_called()
        create.assert_not_called()
        self.assertEqual(self.state, {})

    def test_invalid_cookie_never_reaches_session_api(self):
        for value in ("", "A" * 42, "A" * 44, "token/" + "A" * 37, 123, None):
            with self.subTest(value=value):
                self.state.clear()
                self.context.cookies[browser_auth.COOKIE_NAME] = value
                with patch.object(browser_auth, "restore_browser_session") as restore:
                    browser_auth.restore_login()
                restore.assert_not_called()
                self.assertNotIn("auth_token", self.state)

    def test_restore_uses_server_identity_and_runs_once_per_streamlit_session(self):
        self.context.cookies.update(role="Admin", email="other@example.test")
        with patch.object(browser_auth, "restore_browser_session", return_value=self.account) as restore:
            browser_auth.restore_login()
            browser_auth.restore_login()
        restore.assert_called_once_with(self.handle)
        self.assertTrue(self.state["logged_in"])
        self.assertEqual(self.state["auth_mode"], "internal")
        self.assertEqual(self.state["auth_token"], self.account["access_token"])
        self.assertEqual(self.state["auth_expires_at"], self.account["expires_at"])
        self.assertEqual(self.state["username"], self.account["email"])
        self.assertEqual(self.state["display_name"], "Alice")
        self.assertEqual(self.state["role"], "User")

    def test_authenticated_session_does_not_repeat_browser_restoration(self):
        self.state["auth_mode"] = "internal"
        with patch.object(browser_auth, "restore_browser_session") as restore:
            browser_auth.restore_login()
        restore.assert_not_called()

    def test_expired_handle_clears_cookie_and_displays_login_notice(self):
        with patch.object(browser_auth, "restore_browser_session", side_effect=ValuationAPIError("Expired")):
            browser_auth.restore_login()
        self.assertEqual(self.state["public_page"], "auth")
        self.assertEqual(self.state["auth_screen"], "login")
        self.assertTrue(self.state["auth_notice"])
        self.assertEqual(self.state["_browser_cookie_operation"]["operation"], "clear")
        self.assertNotIn("_browser_session_handle", self.state)
        self.assertNotIn("auth_token", self.state)

    def test_remember_login_queues_opaque_handle_without_token_or_role(self):
        with patch.object(browser_auth, "create_browser_session", return_value={"browser_session": self.handle}) as create:
            browser_auth.remember_login(self.account)
        create.assert_called_once_with(self.account["access_token"])
        operation = self.state["_browser_cookie_operation"]
        self.assertEqual(operation["operation"], "save")
        self.assertEqual(operation["handle"], self.handle)
        self.assertEqual(set(operation), {"operation", "handle", "id"})
        self.assertEqual(self.state["_browser_session_handle"], self.handle)
        self.assertTrue(self.state["_browser_restore_checked"])

    def test_cookie_setup_failure_preserves_current_login_and_explains_refresh_limit(self):
        self.state.update(logged_in=True, auth_mode="internal", auth_token="current-token")
        with patch.object(browser_auth, "create_browser_session", side_effect=ValuationAPIError("Unavailable")):
            browser_auth.remember_login(self.account)
        self.assertTrue(self.state["logged_in"])
        self.assertEqual(self.state["auth_token"], "current-token")
        self.assertTrue(self.state["_browser_cookie_error"])
        self.assertNotIn("_browser_cookie_operation", self.state)

    def test_logout_revokes_active_handle_even_before_cookie_round_trip(self):
        active = "B" * 43
        self.state["_browser_session_handle"] = active
        with patch.object(browser_auth, "revoke_browser_session") as revoke:
            browser_auth.revoke_current_session()
        revoke.assert_called_once_with(active)
        with patch.object(browser_auth, "revoke_browser_session", side_effect=ValuationAPIError("Unavailable")):
            browser_auth.revoke_current_session()
        browser_auth.queue_cookie_clear()
        self.assertNotIn("_browser_session_handle", self.state)
        self.assertEqual(self.state["_browser_cookie_operation"]["operation"], "clear")

    def test_cookie_acknowledgements_must_match_pending_operation(self):
        self.state["_browser_cookie_operation"] = {"operation": "save", "id": "current"}
        self.state[browser_auth.BRIDGE_KEY] = {"result": {"id": "previous", "ok": True}}
        browser_auth._cookie_result()
        self.assertIn("_browser_cookie_operation", self.state)
        self.state[browser_auth.BRIDGE_KEY]["result"] = {"id": "current", "ok": True}
        browser_auth._cookie_result()
        self.assertNotIn("_browser_cookie_operation", self.state)


if __name__ == "__main__":
    unittest.main()
