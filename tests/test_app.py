"""Interaction regressions using temporary storage, never the project JSON files."""

import copy
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests
from streamlit.testing.v1 import AppTest

import storage


APP_FILE = Path(__file__).resolve().parents[1] / "app.py"


class AppInteractionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="homenest-app-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        paths = {
            "DATA_FILE": self.root / "properties.json",
            "USERS_FILE": self.root / "users.json",
            "MESSAGES_FILE": self.root / "messages.json",
            "FORUM_FILE": self.root / "forum.json",
            "IMAGES_DIR": self.root / "images",
        }
        paths["IMAGES_DIR"].mkdir()
        isolation = patch.multiple(storage, **paths)
        isolation.start()
        self.addCleanup(isolation.stop)
        # Exercise the real API routes in-process while isolating storage.
        from fastapi.testclient import TestClient
        import api
        from api import app as api_app
        import auth_store
        from auth_store import AccountStore
        client = TestClient(api_app)
        self.addCleanup(client.close)
        auth_secret = patch.dict(os.environ, {
            'AUTH_JWT_SECRET': 'test-only-signing-key-with-more-than-32-bytes',
        })
        auth_secret.start()
        self.addCleanup(auth_secret.stop)
        self.auth_store = AccountStore(self.root / 'accounts.sqlite3')
        account_store = patch.object(api, 'AccountStore', return_value=self.auth_store)
        account_store.start()
        self.addCleanup(account_store.stop)
        fast_passwords = patch.object(auth_store, 'PBKDF2_ITERATIONS', 1000)
        fast_passwords.start()
        self.addCleanup(fast_passwords.stop)
        self.accounts = {
            'alice': ('alice@example.test', 'Alice', 'Alice-Password-123', 'User'),
            'bob': ('bob@example.test', 'Bob', 'Bob-Password-123', 'User'),
            'manager': ('manager@example.test', 'Manager', 'Manager-Password-123', 'Manager'),
            'admin': ('admin@example.test', 'Administrator', 'Admin-Password-123', 'Admin'),
        }
        for email, full_name, password, role in self.accounts.values():
            self.auth_store.create_account(email, full_name, password, role)
        import api_client
        def as_requests_response(response, method, url):
            result = requests.Response()
            result.status_code = response.status_code
            result._content = response.content
            result.headers = dict(response.headers)
            result.url = url
            result.request = requests.Request(method, url).prepare()
            return result

        transport = patch.object(api_client.requests, 'post',
                                 side_effect=lambda url, **kwargs: as_requests_response(
                                     client.post('/' + url.split('/', 3)[3],
                                                 json=kwargs['json'],
                                                 headers=kwargs.get('headers', {})),
                                     'POST', url))
        transport.start()
        self.addCleanup(transport.stop)
        health_transport = patch.object(
            api_client.requests, 'get',
            side_effect=lambda url, **kwargs: as_requests_response(
                client.get('/' + url.split('/', 3)[3],
                           headers=kwargs.get('headers', {})),
                'GET', url))
        health_transport.start()
        self.addCleanup(health_transport.stop)

        # Copy the built-in samples; keep a legacy record without display metadata,
        # an owned listing, a listing above the former 10-billion budget cap, and rent.
        self.records = copy.deepcopy(storage.default_properties()[:5])
        for key in ("user_id", "rating", "reviews", "images"):
            self.records[0].pop(key, None)
        self.records[2]["user_id"] = "alice@example.test"
        self.records[3].update(user_id="alice@example.test", price=12_000_000_000)
        self.records[4].update(user_id="bob@example.test", price=8_000_000)
        storage.save_properties(self.records)
        storage.save_users({
            "alice@example.test": {"full_name": "Alice", "favorites": []},
            "bob@example.test": {"full_name": "Bob", "favorites": []},
            "admin": {"full_name": "Administrator", "favorites": []},
        })
        storage.save_messages([
            {"from": "admin", "to": "alice@example.test", "content": "Message for Alice",
             "created_at": "2026-09-01 09:00:00"},
            {"from": "admin", "to": "bob@example.test", "content": "Message for Bob",
             "created_at": "2026-09-02 09:00:00"},
        ])
        self.app = AppTest.from_file(str(APP_FILE), default_timeout=20).run()
        self.assert_no_exception()
        self.open_login()

    def assert_no_exception(self):
        self.assertEqual(list(self.app.exception), [])

    def open_login(self):
        if not any(widget.key == "login_email" for widget in self.app.text_input):
            self.app.button(key="landing_login").click().run()
            self.assert_no_exception()

    def assert_landing(self):
        self.assert_no_exception()
        self.assertFalse(self.app.session_state.logged_in)
        self.assertEqual(self.app.session_state.public_page, "landing")
        self.assertEqual(list(self.app.text_input), [])
        self.assertEqual(list(self.app.sidebar.button), [])
        button_keys = {button.key for button in self.app.button}
        self.assertTrue({"landing_login", "landing_start", "landing_cta"} <= button_keys)
        self.assertTrue({
            "landing_feature_explore", "landing_feature_valuation", "landing_feature_mine",
            "landing_feature_favorites", "landing_feature_overview", "landing_feature_forum",
        } <= button_keys)
        self.assertFalse(any(key and key.startswith(("nav_", "primary_nav_"))
                             for key in button_keys))
        self.assertNotIn("account_login_submit", button_keys)
        self.assertNotIn("register_submit", button_keys)

    def login(self, username="alice"):
        self.open_login()
        email, _, password, _ = self.accounts[username]
        self.app.text_input(key="login_email").set_value(email)
        self.app.text_input(key="login_password").set_value(password)
        self.app.button(key="account_login_submit").click().run()
        self.assert_no_exception()
        self.assertTrue(self.app.session_state.logged_in)

    def login_as_role(self, role):
        role_users = {"User": "alice", "Manager": "manager", "Admin": "admin"}
        self.login(role_users[role])
        self.assertEqual(self.app.session_state.role, role)

    def navigate(self, view):
        self.app.button(key=f"nav_{view}").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, view)

    def prefix(self):
        return f"editor_{self.app.session_state.editor_revision}"

    def stored_records(self):
        return json.loads(storage.DATA_FILE.read_text(encoding="utf-8"))

    def valuation_map_query(self):
        maps = [element.proto.srcdoc for element in self.app.get("iframe")
                if 'homenest-map-config' in element.proto.srcdoc]
        self.assertEqual(len(maps), 1)
        config_tag = re.search(
            r'<script\b(?=[^>]*\bid=[\"\']homenest-map-config[\"\'])[^>]*>(.*?)</script\s*>',
            maps[0], re.DOTALL | re.IGNORECASE)
        self.assertIsNotNone(config_tag, 'The local map must expose its normalized configuration')
        point = json.loads(config_tag.group(1))['point']
        return {'marker': [f'{point[0]},{point[1]}']} if point is not None else {}

    def account_options(self):
        return [label.rsplit(" · ", 1)[-1]
                for label in self.app.selectbox(key="account_selected").options]

    def test_public_landing_ctas_open_login_and_allow_returning(self):
        self.app = AppTest.from_file(str(APP_FILE), default_timeout=20).run()
        self.assert_landing()

        for key in ("landing_login", "landing_start", "landing_cta"):
            with self.subTest(entry=key):
                self.app.button(key=key).click().run()
                self.assert_no_exception()
                self.assertFalse(self.app.session_state.logged_in)
                self.assertEqual(self.app.session_state.public_page, "auth")
                self.assertEqual(self.app.session_state.auth_screen, "login")
                self.assertEqual({widget.key for widget in self.app.text_input},
                                 {"login_email", "login_password"})
                self.assertFalse(any(button.key == "nav_overview"
                                     for button in self.app.button))
                self.app.button(key="back_to_landing").click().run()
                self.assert_landing()
        self.assertEqual(self.stored_records(), self.records)

    def test_landing_feature_button_opens_its_destination_after_login(self):
        self.app = AppTest.from_file(str(APP_FILE), default_timeout=20).run()
        self.app.button(key="landing_feature_explore").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.public_page, "auth")
        self.assertEqual(self.app.session_state.auth_screen, "login")
        self.assertEqual(self.app.session_state._post_auth_view, "explore")

        self.login()

        self.assertEqual(self.app.session_state.view, "explore")
        self.assert_no_exception()

    def test_landing_feature_button_handles_roles_without_access(self):
        self.app = AppTest.from_file(str(APP_FILE), default_timeout=20).run()
        self.app.button(key="landing_feature_valuation").click().run()
        self.assert_no_exception()

        self.login("admin")

        self.assertEqual(self.app.session_state.view, "overview")
        self.assertTrue(any(
            "Mục này không khả dụng với vai trò tài khoản của bạn." in error.value
            for error in self.app.error
        ))
        self.assert_no_exception()

    def test_registration_can_return_to_landing_and_logout_restores_it(self):
        self.app.button(key="show_register").click().run()
        self.assert_no_exception()
        self.assertIn("register_full_name", [widget.key for widget in self.app.text_input])
        self.assertFalse(self.app.session_state.logged_in)
        self.app.button(key="back_to_landing").click().run()
        self.assert_landing()

        self.login()
        self.assertEqual(self.app.session_state.view, "overview")
        self.app.button(key="logout").click().run()
        self.assert_landing()
        self.assertEqual(self.stored_records(), self.records)

    def test_user_navigation_and_inbox_are_scoped_to_account(self):
        self.app.text_input(key="login_email").set_value("not-an-email")
        self.app.text_input(key="login_password").set_value("bad")
        self.app.button(key="account_login_submit").click().run()
        self.assertFalse(self.app.session_state.logged_in)
        self.assertEqual(len(self.app.error), 1)
        self.login()

        for view in ("valuation", "explore", "mine", "favorites", "messages", "profile", "editor", "overview"):
            with self.subTest(view=view):
                self.navigate(view)
                self.assertEqual(len(self.app.error), 0)
                if view == "messages":
                    contents = " ".join(element.value for element in self.app.markdown)
                    self.assertIn("Message for Alice", contents)
                    self.assertNotIn("Message for Bob", contents)
        self.assertEqual(self.app.metric[0].label, "Tin đang bán")
        self.assertEqual(self.app.metric[0].value, "4")
        self.assertEqual(self.app.metric[1].label, "Tin cho thuê")
        self.assertEqual(self.app.metric[1].value, "1")
        self.assertEqual(self.app.metric[2].label, "Lựa chọn đang có")
        self.assertEqual(self.app.metric[2].value, "5")

    def test_user_login_starts_on_marketing_home_with_featured_properties(self):
        self.login()
        self.assertEqual(self.app.session_state.view, "overview")
        copy = " ".join(element.value for element in self.app.markdown)
        captions = " ".join(element.value for element in self.app.caption)
        self.assertIn("HOMENEST · KHÔNG GIAN SỐNG CỦA BẠN", captions)
        self.assertIn("Ảnh minh họa · Thông tin và giá tham khảo theo danh mục HomeNest",
                      captions)
        self.assertIn("Dự án & bất động sản tiêu biểu", copy)
        self.assertIn("Tìm hiểu kỹ hơn trước khi lựa chọn", copy)
        self.assertIn("Một nền tảng, nhiều cách tìm thấy tổ ấm", copy)
        self.assertIn(
            "không đại diện giá giao dịch hay định giá độc lập",
            copy,
        )

        button_keys = {button.key for button in self.app.button}
        self.assertTrue({
            "home_explore", "home_price_guide", "home_all_properties",
            "home_final_explore", "home_story_explore",
            "home_story_valuation", "home_story_forum",
        } <= button_keys)
        featured_detail_buttons = [
            button for button in self.app.button
            if button.key and button.key.startswith("home_featured_detail_")
        ]
        self.assertEqual(len(featured_detail_buttons), 3)

        self.app.button(key="home_all_properties").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "explore")
        self.assertTrue(any(button.key == "search_fav_1" for button in self.app.button))

    def test_admin_navigation_and_authentication(self):
        self.assertIn('login_email', [widget.key for widget in self.app.text_input])
        self.assertIn('login_password', [widget.key for widget in self.app.text_input])
        self.app.text_input(key="login_email").set_value(self.accounts["admin"][0])
        self.app.text_input(key="login_password").set_value("Incorrect-Password")
        self.app.button(key="account_login_submit").click().run()
        self.assertFalse(self.app.session_state.logged_in)
        self.assertEqual(len(self.app.error), 1)
        self.login_as_role("Admin")

        for view in ("listings", "accounts", "duplicates", "reports", "settings", "editor", "overview"):
            with self.subTest(view=view):
                self.navigate(view)
                self.assertEqual(len(self.app.error), 0)
        self.assertTrue(any(element.value == "5" for element in self.app.metric))

    def test_admin_navigation_contains_only_management_and_publishing(self):
        self.login_as_role("Admin")
        keys = {button.key for button in self.app.button}
        self.assertTrue({
            "nav_overview", "nav_listings", "nav_accounts", "nav_duplicates",
            "nav_reports", "nav_settings", "nav_messages", "nav_editor",
            "primary_nav_admin_home", "primary_nav_admin_listings",
            "primary_nav_admin_accounts", "primary_nav_admin_reports",
            "primary_nav_admin_messages",
        } <= keys)
        self.assertFalse({
            "nav_valuation", "nav_explore", "nav_mine", "nav_favorites", "nav_profile",
            "primary_nav_home", "primary_nav_location", "primary_nav_plan",
            "primary_nav_amenities", "primary_nav_design", "primary_nav_forum",
            "primary_nav_contact", "overview_explore", "overview_valuation",
            "overview_find",
        } & keys)
        for view in ("explore", "valuation", "market", "services", "design",
                     "forum", "contact", "mine", "favorites", "profile"):
            with self.subTest(view=view):
                self.app.session_state.view = view
                self.app.run()
                self.assert_no_exception()
                self.assertEqual(len(self.app.error), 1)
                self.assertNotIn("detail_favorite", {button.key for button in self.app.button})

        self.navigate("listings")
        self.app.session_state.view = "detail"
        self.app.session_state.selected_id = 2
        self.app.session_state.return_view = "listings"
        self.app.run()
        self.assert_no_exception()
        detail_keys = {button.key for button in self.app.button}
        self.assertTrue({"detail_edit", "detail_delete"} <= detail_keys)
        self.assertNotIn("detail_favorite", detail_keys)

    def test_admin_account_directory_searches_filters_and_creates_only_users(self):
        self.login_as_role("Admin")
        self.navigate("accounts")
        self.assertEqual(set(self.account_options()),
                         {"alice@example.test", "bob@example.test"})
        self.assertTrue({"account_reload", "account_create_submit"}
                        <= {button.key for button in self.app.button})
        self.app.text_input(key="account_search").set_value("ALICE").run()
        self.assertEqual(self.account_options(),
                         ["alice@example.test"])
        self.app.text_input(key="account_search").set_value("does-not-exist").run()
        self.assertFalse(any(widget.key == "account_selected" for widget in self.app.selectbox))
        self.app.text_input(key="account_search").set_value("").run()

        self.app.text_input(key="new_user_name").set_value("New Managed User")
        self.app.text_input(key="new_user_email").set_value("new.managed@example.test")
        self.app.text_input(key="new_user_password").set_value("New-Managed-Password-123")
        self.app.text_input(key="new_user_password_confirm").set_value("New-Managed-Password-123")
        self.app.button(key="account_create_submit").click().run()
        self.assert_no_exception()
        account = self.auth_store.authenticate("new.managed@example.test", "New-Managed-Password-123")
        self.assertIsNotNone(account)
        self.assertEqual((account["full_name"], account["role"]), ("New Managed User", "User"))
        self.assertIn("new.managed@example.test", self.account_options())
        self.assertFalse(any("role" in str(widget.key)
                             for widget in self.app.selectbox))

    def test_admin_account_profile_edit_preserves_favorites_and_ownership(self):
        users = storage.load_users()
        users["alice@example.test"]["favorites"] = [1, 4]
        storage.save_users(users)
        self.login_as_role("Admin")
        self.navigate("accounts")
        self.app.selectbox(key="account_selected").select("alice@example.test").run()
        self.app.text_input(key="admin_account_name").set_value("Alice Updated")
        self.app.text_input(key="admin_account_phone").set_value("0901234567")
        self.app.text_area(key="admin_account_bio").set_value("Thông tin hồ sơ đã được xác minh.")
        self.app.text_area(key="admin_account_note").set_value("Đã kiểm tra liên hệ theo yêu cầu.")
        self.app.button(key="account_update_submit").click().run()
        self.assert_no_exception()
        self.assertEqual(self.auth_store.get_user("alice@example.test")["full_name"], "Alice Updated")
        profile = storage.load_users()["alice@example.test"]
        self.assertEqual(profile["full_name"], "Alice Updated")
        self.assertEqual(profile["phone"], "0901234567")
        self.assertEqual(profile["bio"], "Thông tin hồ sơ đã được xác minh.")
        self.assertEqual(profile["favorites"], [1, 4])
        self.assertEqual(self.stored_records(), self.records)
        self.app.selectbox(key="account_selected").select("bob@example.test").run()
        self.assertEqual(self.app.text_input(key="admin_account_name").value, "Bob")
        self.assertEqual(self.app.text_input(key="admin_account_phone").value, "")

    def test_user_profile_updates_canonical_account_and_admin_details(self):
        users = storage.load_users()
        users["alice@example.test"]["favorites"] = [1, 4]
        storage.save_users(users)
        self.login()
        self.navigate("profile")
        self.assertFalse(any(str(widget.key).startswith("admin_account_")
                             for widget in list(self.app.text_input) + list(self.app.text_area)))
        for label, value in (("Họ và tên", "Alice Self Updated"),
                             ("Số điện thoại", "0912345678"),
                             ("Email", "alice.contact@example.test")):
            next(widget for widget in self.app.text_input if widget.label == label).set_value(value)
        next(widget for widget in self.app.text_area if widget.label == "Giới thiệu").set_value(
            "Thông tin được Alice tự cập nhật."
        )
        next(button for button in self.app.button if button.label == "Lưu hồ sơ").click().run()
        self.assert_no_exception()
        self.assertEqual(len(self.app.error), 0)
        self.assertEqual(self.auth_store.get_user("alice@example.test")["full_name"], "Alice Self Updated")
        self.assertIsNone(self.auth_store.get_user("alice.contact@example.test"))
        profile = storage.load_users()["alice@example.test"]
        self.assertEqual(profile["full_name"], "Alice Self Updated")
        self.assertEqual(profile["phone"], "0912345678")
        self.assertEqual(profile["bio"], "Thông tin được Alice tự cập nhật.")
        self.assertEqual(profile["favorites"], [1, 4])
        self.app.run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.username, "alice@example.test")
        self.assertEqual(self.app.session_state.display_name, "Alice Self Updated")

        self.app.button(key="logout").click().run()
        self.login_as_role("Admin")
        self.navigate("accounts")
        self.app.selectbox(key="account_selected").select("alice@example.test").run()
        self.assert_no_exception()
        self.assertEqual(self.app.text_input(key="admin_account_name").value, "Alice Self Updated")
        self.assertEqual(self.app.text_input(key="admin_account_phone").value, "0912345678")
        self.assertEqual(self.app.text_area(key="admin_account_bio").value,
                         "Thông tin được Alice tự cập nhật.")
        self.assertEqual(storage.load_users()["alice@example.test"]["favorites"], [1, 4])
        self.assertEqual(self.stored_records(), self.records)

    def test_admin_can_lock_and_unlock_user_with_reason_and_confirmation(self):
        self.login_as_role("Admin")
        self.navigate("accounts")
        self.app.selectbox(key="account_selected").select("alice@example.test").run()
        self.app.text_area(key="admin_account_status_reason").set_value("Tạm khóa để xác minh tài khoản.")
        self.app.button(key="account_status_submit").click().run()
        self.assert_no_exception()
        self.assertTrue(self.auth_store.get_user("alice@example.test")["is_active"])
        self.assertGreater(len(self.app.error), 0)

        self.app.checkbox(key="admin_account_status_confirm").check()
        self.app.button(key="account_status_submit").click().run()
        self.assert_no_exception()
        self.assertFalse(self.auth_store.get_user("alice@example.test")["is_active"])
        self.assertIsNone(self.auth_store.authenticate("alice@example.test", self.accounts["alice"][2]))
        self.app.selectbox(key="account_status_filter").select("Đã khóa").run()
        self.assertEqual(self.account_options(), ["alice@example.test"])

        self.app.text_area(key="admin_account_status_reason").set_value("Đã xác minh xong thông tin tài khoản.")
        self.app.checkbox(key="admin_account_status_confirm").check()
        self.app.button(key="account_status_submit").click().run()
        self.assert_no_exception()
        self.assertTrue(self.auth_store.get_user("alice@example.test")["is_active"])
        self.assertIsNotNone(self.auth_store.authenticate("alice@example.test", self.accounts["alice"][2]))
        self.app.selectbox(key="account_status_filter").select("Đang hoạt động").run()
        self.assertEqual(set(self.account_options()),
                         {"alice@example.test", "bob@example.test"})

    def test_admin_password_reset_changes_credentials_without_changing_user_role(self):
        self.login_as_role("Admin")
        self.navigate("accounts")
        self.app.selectbox(key="account_selected").select("alice@example.test").run()
        self.app.text_input(key="admin_account_password").set_value("Reset-User-Password-123")
        self.app.text_input(key="admin_account_password_confirm").set_value("Reset-User-Password-123")
        self.app.text_area(key="admin_account_password_reason").set_value("Đặt lại theo yêu cầu hỗ trợ đã xác minh.")
        self.app.checkbox(key="admin_account_password_confirmed").check()
        self.app.button(key="account_password_submit").click().run()
        self.assert_no_exception()
        self.assertIsNone(self.auth_store.authenticate("alice@example.test", self.accounts["alice"][2]))
        reset_account = self.auth_store.authenticate("alice@example.test", "Reset-User-Password-123")
        self.assertIsNotNone(reset_account)
        self.assertEqual(reset_account["role"], "User")
        self.assertEqual(self.stored_records(), self.records)

    def test_create_validates_resets_saves_and_returns_to_owned_list(self):
        self.login()
        self.navigate("editor")
        self.app.button(key="submit_editor").click().run()
        self.assertEqual(len(self.app.error), 1)
        self.assertEqual(len(self.stored_records()), 5)

        prefix = self.prefix()
        self.app.text_input(key=f"{prefix}_title").set_value("Discard this draft")
        self.app.number_input(key=f"{prefix}_price").set_value(999_000_000)
        self.app.button(key="reset_editor").click().run()
        self.assert_no_exception()
        self.assertNotEqual(self.prefix(), prefix)
        prefix = self.prefix()
        self.assertEqual(self.app.text_input(key=f"{prefix}_title").value, "")
        self.assertEqual(self.app.number_input(key=f"{prefix}_price").value, 0)

        self.app.text_input(key=f"{prefix}_title").set_value("New apartment")
        self.app.text_input(key=f"{prefix}_location").set_value("Test district")
        self.app.text_input(key=f"{prefix}_contact").set_value("0901234567")
        self.app.text_area(key=f"{prefix}_description").set_value("A bright apartment.")
        self.app.number_input(key=f"{prefix}_price").set_value(3_500_000_000)
        self.app.number_input(key=f"{prefix}_area").set_value(80.0)
        self.app.button(key="submit_editor").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "detail")
        self.assertEqual(len(self.app.success), 1)
        created = self.stored_records()[-1]
        self.assertEqual((created["title"], created["user_id"], created["area"]),
                         ("New apartment", "alice@example.test", 80.0))
        self.assertEqual(len(self.stored_records()), 6)
        self.assertEqual(list(storage.IMAGES_DIR.iterdir()), [])

        self.app.button(key="detail_back").click().run()
        self.assertEqual(self.app.session_state.view, "mine")
        self.assertEqual(len(self.app.dataframe[0].value), 3)
        self.app.selectbox(key="manage_selected").set_value(created["id"]).run()
        self.app.button(key="manage_detail").click().run()
        self.assert_no_exception()
        self.assertEqual(len(self.app.success), 0)

    def test_edit_restores_original_and_preserves_listing_metadata(self):
        self.login()
        self.navigate("mine")
        self.app.selectbox(key="manage_selected").set_value(3).run()
        self.app.button(key="manage_edit").click().run()
        prefix = self.prefix()
        original = copy.deepcopy(self.records[2])
        self.app.text_input(key=f"{prefix}_title").set_value("Discard edit")
        self.app.button(key="reset_editor").click().run()
        self.assertEqual(self.app.text_input(key=f"{self.prefix()}_title").value, original["title"])

        prefix = self.prefix()
        self.app.text_input(key=f"{prefix}_title").set_value("Updated land listing")
        self.app.number_input(key=f"{prefix}_price").set_value(2_100_000_000)
        self.app.button(key="submit_editor").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "detail")
        updated = next(p for p in self.stored_records() if p["id"] == 3)
        self.assertEqual(updated["title"], "Updated land listing")
        self.assertEqual(updated["price"], 2_100_000_000)
        for key in ("id", "user_id", "created_at", "rating", "reviews", "images"):
            self.assertEqual(updated[key], original[key], key)
        self.assertIn("updated_at", updated)
        self.assertEqual(len(self.stored_records()), 5)
        self.app.button(key="detail_edit").click().run()
        self.app.button(key="editor_back").click().run()
        self.assertEqual(self.app.session_state.view, "mine")

    def test_delete_can_be_cancelled_then_confirmed(self):
        self.login()
        self.navigate("mine")
        self.app.selectbox(key="manage_selected").set_value(3).run()
        self.app.button(key="manage_delete").click().run()
        self.assert_no_exception()
        # AppTest runs the whole script for a dialog click (the browser reruns
        # its fragment), so replay the opener to execute the dialog body.
        self.app.button(key="manage_delete").click()
        self.app.button(key="cancel_delete").click().run()
        self.assertEqual(len(self.stored_records()), 5)
        self.app.button(key="manage_delete").click().run()
        self.app.button(key="manage_delete").click()
        self.app.button(key="confirm_delete").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "mine")
        self.assertEqual({p["id"] for p in self.stored_records()}, {1, 2, 4, 5})
        self.assertEqual(len(self.app.dataframe[0].value), 1)

    def test_favorites_persist_and_logout_clears_other_account_state(self):
        self.login()
        self.navigate("explore")
        self.app.button(key="search_fav_1").click().run()
        self.assertEqual(storage.load_users()["alice@example.test"]["favorites"], [1])
        self.navigate("favorites")
        self.assertEqual(len([b for b in self.app.button if b.key == "saved_detail_1"]), 1)
        self.app.button(key="logout").click().run()
        self.assertFalse(self.app.session_state.logged_in)
        self.login("bob")
        self.assertEqual(self.app.session_state.favorites, [])
        self.navigate("favorites")
        self.assertEqual(len(self.app.info), 1)
        self.app.button(key="logout").click().run()
        self.login("alice")
        self.assertEqual(self.app.session_state.favorites, [1])
        self.navigate("favorites")
        self.app.button(key="saved_fav_1").click().run()
        self.assertEqual(storage.load_users()["alice@example.test"]["favorites"], [])
        self.assert_no_exception()

    def test_search_supports_prices_above_ten_billion_and_detail_back(self):
        self.login()
        self.navigate("explore")
        self.assertEqual(self.app.number_input(key="search_budget").value, 0)
        self.assertTrue(any(button.key == "search_detail_4" for button in self.app.button))
        self.app.number_input(key="search_budget").set_value(10_000_000_000).run()
        self.assertFalse(any(button.key == "search_detail_4" for button in self.app.button))
        self.app.number_input(key="search_budget").set_value(13_000_000_000).run()
        self.app.button(key="search_detail_4").click().run()
        self.assertEqual(self.app.session_state.view, "detail")
        self.app.button(key="detail_back").click().run()
        self.assertEqual(self.app.session_state.view, "explore")
        self.assert_no_exception()

    def test_user_cannot_manage_other_owners_or_admin_views(self):
        self.login()
        self.navigate("explore")
        self.app.button(key="search_detail_2").click().run()
        keys = {button.key for button in self.app.button}
        self.assertNotIn("detail_edit", keys)
        self.assertNotIn("detail_delete", keys)

        self.app.session_state.view = "editor"
        self.app.session_state.selected_id = 2
        self.app.run()
        self.assertEqual(len(self.app.error), 1)
        self.assertFalse(any(button.key == "submit_editor" for button in self.app.button))
        self.app.session_state.view = "reports"
        self.app.run()
        self.assertEqual(len(self.app.error), 1)
        self.assertEqual(len(self.app.metric), 0)
        self.assertEqual(self.stored_records(), self.records)
        self.assert_no_exception()

    def test_user_and_manager_cannot_open_account_management(self):
        for role in ("User", "Manager"):
            with self.subTest(role=role):
                self.login_as_role(role)
                self.assertNotIn("nav_accounts", {button.key for button in self.app.button})
                self.app.session_state.view = "accounts"
                self.app.run()
                self.assert_no_exception()
                self.assertEqual(len(self.app.error), 1)
                self.assertFalse(any(widget.key == "account_search"
                                     for widget in self.app.text_input))
                self.app.button(key="logout").click().run()

    def test_locked_account_cannot_use_a_previously_rendered_favorite_button(self):
        self.login()
        self.navigate("explore")
        with self.auth_store._connection() as connection:
            connection.execute("UPDATE accounts SET is_active = 0 WHERE email = ?",
                               (self.accounts["alice"][0],))
        self.app.button(key="search_fav_1").click().run()
        self.assert_no_exception()
        self.assertFalse(self.app.session_state.logged_in)
        self.assertEqual(storage.load_users()[self.accounts["alice"][0]]["favorites"], [])
        self.assertFalse(any(button.key == "search_fav_1" for button in self.app.button))

    def test_admin_role_requires_a_provisioned_account_and_password(self):
        self.app.text_input(key='login_email').set_value('admin')
        self.app.text_input(key='login_password').set_value('admin123')
        self.app.button(key='account_login_submit').click().run()
        self.assertFalse(self.app.session_state.logged_in)
        self.assertEqual(len(self.app.error), 1)

    def test_demo_login_is_removed_and_internal_login_is_always_available(self):
        with patch.dict(os.environ, {'HOMENEST_DEMO_AUTH': 'true'}):
            self.app.run()
        self.assert_no_exception()
        self.assertFalse(self.app.session_state.logged_in)
        self.assertIn('login_email', [widget.key for widget in self.app.text_input])
        self.assertIn('login_password', [widget.key for widget in self.app.text_input])
        self.assertFalse(any(widget.key in {'admin_password', 'login_username'}
                             for widget in self.app.text_input))
        self.assertFalse(any(widget.key == 'login_role'
                             for widget in self.app.segmented_control))
        self.assertNotIn('Đăng nhập demo',
                         ' '.join(element.value for element in self.app.markdown))
        self.assertTrue(any(button.key == 'show_register'
                            for button in self.app.button))
        self.app.button(key='show_register').click().run()
        self.assertIn('register_full_name',
                      [widget.key for widget in self.app.text_input])

    def test_manager_can_view_method_and_use_batch_feature(self):
        self.login_as_role('Manager')
        self.assertEqual(self.app.metric[0].value, "4")
        self.assertEqual(self.app.metric[1].value, "1")
        self.assertEqual(self.app.metric[2].value, "5")
        self.navigate('valuation')
        self.assertTrue(any(button.key == 'batch_submit' for button in self.app.button))
        self.app.button(key='model_info_load').click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.model_info['type'],
                         'deterministic-estimator')

    def test_primary_navigation_sections_and_category_links(self):
        self.login()
        sidebar_keys = {
            button.key for button in self.app.sidebar.button if button.key
        }
        self.assertTrue({
            "nav_overview", "nav_valuation", "nav_explore", "nav_mine",
            "nav_favorites", "nav_messages", "nav_profile", "nav_editor", "logout",
        } <= sidebar_keys)
        sidebar_copy = " ".join(element.value for element in self.app.sidebar.caption)
        self.assertIn("PHÂN TÍCH", sidebar_copy)
        self.assertIn("DANH MỤC", sidebar_copy)
        self.assertIn("TÀI KHOẢN", sidebar_copy)

        nav_buttons = {
            button.key: button
            for button in self.app.button
            if button.key and button.key.startswith("primary_nav_")
        }
        self.assertTrue({
            "primary_nav_home", "primary_nav_location", "primary_nav_plan",
            "primary_nav_amenities", "primary_nav_design", "primary_nav_forum",
            "primary_nav_contact",
        } <= nav_buttons.keys())
        self.assertTrue(all(button.proto.icon for button in nav_buttons.values()))

        sections = {
            "home": "overview",
            "location": "explore",
            "plan": "market",
            "amenities": "services",
            "design": "design",
            "forum": "forum",
            "contact": "contact",
        }
        for key, view in sections.items():
            with self.subTest(section=key):
                self.app.button(key=f"primary_nav_{key}").click().run()
                self.assert_no_exception()
                self.assertEqual(self.app.session_state.view, view)

        self.app.button(key="primary_nav_design").click().run()
        self.app.button(key="design_category_0").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "explore")
        self.assertEqual(self.app.session_state.search_type,
                         sorted(set(["Căn hộ", "Nhà phố", "Đất nền", "Biệt thự", "Khác"]
                                    + [prop["type"] for prop in self.records]))[0])

    def test_design_filter_keeps_category_identity_and_survives_detail_navigation(self):
        self.login()
        self.app.button(key="primary_nav_design").click().run()
        self.assert_no_exception()
        kinds = ["Biệt thự", "Căn hộ", "Khác", "Nhà phố", "Đất nền"]
        self.assertEqual(self.app.pills(key="design_kind").options, ["Tất cả"] + kinds)
        self.assertEqual(self.app.pills(key="design_kind").value, "Tất cả")
        category_keys = {f"design_category_{index}" for index in range(len(kinds))}
        self.assertEqual(category_keys & {button.key for button in self.app.button},
                         category_keys)

        self.app.pills(key="design_kind").select("Căn hộ").run()
        self.assert_no_exception()
        apartment_key = f"design_category_{kinds.index('Căn hộ')}"
        self.assertEqual(category_keys & {button.key for button in self.app.button},
                         {apartment_key})
        self.app.session_state.search_page = 9
        self.app.button(key=apartment_key).click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "explore")
        self.assertEqual(self.app.selectbox(key="search_type").value, "Căn hộ")
        self.assertEqual(self.app.session_state.search_page, 1)
        self.assertEqual({button.key for button in self.app.button
                          if button.key and button.key.startswith("search_detail_")},
                         {f"search_detail_{prop['id']}" for prop in self.records
                          if prop["type"] == "Căn hộ"})

        self.app.button(key="search_detail_1").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "detail")
        self.app.button(key="detail_back").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "explore")
        self.app.button(key="primary_nav_design").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.pills(key="design_kind").value, "Căn hộ")
        self.assertEqual(category_keys & {button.key for button in self.app.button},
                         {apartment_key})
        self.app.pills(key="design_kind").select("Tất cả").run()
        self.assert_no_exception()
        self.assertEqual(category_keys & {button.key for button in self.app.button},
                         category_keys)
        self.assertEqual(self.stored_records(), self.records)

    def test_design_catalog_supports_custom_kinds_and_empty_inventory(self):
        self.login()
        custom_records = copy.deepcopy(self.records)
        custom_records[0]["type"] = "Nhà vườn"
        self.app.session_state.properties = custom_records
        self.app.button(key="primary_nav_design").click().run()
        self.assert_no_exception()
        kinds = ["Biệt thự", "Căn hộ", "Khác", "Nhà phố", "Nhà vườn", "Đất nền"]
        self.assertEqual(self.app.pills(key="design_kind").options, ["Tất cả"] + kinds)
        self.app.pills(key="design_kind").select("Nhà vườn").run()
        self.assert_no_exception()
        custom_key = f"design_category_{kinds.index('Nhà vườn')}"
        self.assertEqual({button.key for button in self.app.button
                          if button.key and button.key.startswith("design_category_")},
                         {custom_key})
        self.app.button(key=custom_key).click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "explore")
        self.assertEqual(self.app.selectbox(key="search_type").value, "Nhà vườn")
        self.assertEqual({button.key for button in self.app.button
                          if button.key and button.key.startswith("search_detail_")},
                         {"search_detail_1"})

        self.app.button(key="primary_nav_design").click().run()
        self.app.pills(key="design_kind").select("Khác").run()
        self.assert_no_exception()
        empty_key = f"design_category_{kinds.index('Khác')}"
        self.assertFalse(self.app.button(key=empty_key).disabled)
        self.app.button(key=empty_key).click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "explore")
        self.assertEqual(self.app.selectbox(key="search_type").value, "Khác")
        self.assertFalse(any(button.key and button.key.startswith("search_detail_")
                             for button in self.app.button))
        self.assertTrue(any("Chưa có kết quả" in item.value for item in self.app.info))

        self.app.button(key="primary_nav_design").click().run()
        self.app.pills(key="design_kind").select("Tất cả").run()
        self.app.session_state.properties = []
        self.app.run()
        self.assert_no_exception()
        fixed_kinds = ["Biệt thự", "Căn hộ", "Khác", "Nhà phố", "Đất nền"]
        self.assertEqual(self.app.pills(key="design_kind").options,
                         ["Tất cả"] + fixed_kinds)
        self.assertEqual({button.key for button in self.app.button
                          if button.key and button.key.startswith("design_category_")},
                         {f"design_category_{index}" for index in range(len(fixed_kinds))})
        self.app.button(key="design_category_0").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "explore")
        self.assertFalse(any(button.key and button.key.startswith("search_detail_")
                             for button in self.app.button))
        self.assertEqual(self.stored_records(), self.records)

    def test_design_hero_and_support_actions_open_their_destinations(self):
        self.login()
        self.app.button(key="primary_nav_design").click().run()
        self.assert_no_exception()
        self.app.pills(key="design_kind").select("Nhà phố").run()
        for key, view in {
            "design_hero_explore": "explore",
            "design_contact": "contact",
            "design_forum": "forum",
        }.items():
            with self.subTest(action=key):
                self.app.button(key=key).click().run()
                self.assert_no_exception()
                self.assertEqual(self.app.session_state.view, view)
                self.app.button(key="primary_nav_design").click().run()
                self.assert_no_exception()
                self.assertEqual(self.app.pills(key="design_kind").value, "Nhà phố")
        self.assertEqual(self.stored_records(), self.records)

    def test_services_filters_persist_and_tool_actions_open_their_destinations(self):
        self.login()
        self.app.button(key="primary_nav_amenities").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "services")
        goals = self.app.segmented_control(key="services_goal")
        self.assertEqual(goals.options, ["Tất cả", "Tìm & chọn nhà", "Đăng & quản lý"])
        self.assertEqual(goals.value, "Tất cả")
        tools = {
            "services_explore": "explore",
            "services_valuation": "valuation",
            "services_create": "editor",
            "services_favorites": "favorites",
            "services_market": "market",
            "services_mine": "mine",
        }
        self.assertTrue(tools.keys() <= {button.key for button in self.app.button})
        self.assertFalse(any(button.key == "services_batch" for button in self.app.button))

        self.app.segmented_control(key="services_goal").set_value("Tìm & chọn nhà").run()
        self.assert_no_exception()
        self.assertEqual(
            tools.keys() & {button.key for button in self.app.button},
            {"services_explore", "services_valuation", "services_favorites", "services_market"},
        )
        self.app.segmented_control(key="services_goal").set_value("Đăng & quản lý").run()
        self.assert_no_exception()
        self.assertEqual(
            tools.keys() & {button.key for button in self.app.button},
            {"services_create", "services_mine"},
        )
        self.app.button(key="services_mine").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "mine")
        self.app.button(key="primary_nav_amenities").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.segmented_control(key="services_goal").value,
                         "Đăng & quản lý")
        self.app.segmented_control(key="services_goal").set_value("Tất cả").run()

        destinations = {
            **tools,
            "services_hero_explore": "explore",
            "services_hero_valuation": "valuation",
            "services_featured_valuation": "valuation",
            "services_contact": "contact",
            "services_forum": "forum",
        }
        for key, view in destinations.items():
            with self.subTest(action=key):
                editor_revision = self.app.session_state.editor_revision
                self.app.button(key=key).click().run()
                self.assert_no_exception()
                self.assertEqual(self.app.session_state.view, view)
                if view == "editor":
                    self.assertEqual(self.app.session_state.editor_revision,
                                     editor_revision + 1)
                    self.assertIsNone(self.app.session_state.selected_id)
                self.app.button(key="primary_nav_amenities").click().run()
                self.assert_no_exception()
                self.assertEqual(self.app.segmented_control(key="services_goal").value,
                                 "Tất cả")

    def test_services_search_combines_with_goal_and_survives_tool_navigation(self):
        self.login()
        self.app.button(key="primary_nav_amenities").click().run()
        tool_keys = {"services_explore", "services_valuation", "services_create",
                     "services_mine", "services_favorites", "services_market"}

        def assert_tools(expected):
            self.assert_no_exception()
            self.assertEqual(tool_keys & {button.key for button in self.app.button},
                             expected)
            counter = next(item.proto.body for item in self.app.get("html")
                           if 'class="hn-tools-results"' in item.proto.body)
            visible_text = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", counter)).strip()
            self.assertEqual(visible_text,
                             f"Đang hiển thị {len(expected)} / 6 tiện ích")

        assert_tools(tool_keys)
        # Unaccented, uppercase input and extra whitespace should find the same tool.
        self.app.text_input(key="services_search").set_value("  DINH   GIA  ").run()
        assert_tools({"services_valuation"})
        self.app.segmented_control(key="services_goal").set_value("Đăng & quản lý").run()
        assert_tools(set())
        self.assertTrue(any(item.value == "Chưa tìm thấy tiện ích phù hợp"
                            for item in self.app.subheader))

        self.app.button(key="services_search_clear").click().run()
        self.assertEqual(self.app.text_input(key="services_search").value, "")
        self.assertEqual(self.app.segmented_control(key="services_goal").value, "Tất cả")
        assert_tools(tool_keys)

        self.app.text_input(key="services_search").set_value("ban do").run()
        assert_tools({"services_valuation"})
        self.app.button(key="services_valuation").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "valuation")
        self.app.button(key="primary_nav_amenities").click().run()
        self.assertEqual(self.app.text_input(key="services_search").value, "ban do")
        assert_tools({"services_valuation"})

        self.app.text_input(key="services_search").set_value("tin da luu").run()
        assert_tools({"services_favorites"})
        self.assertEqual(self.stored_records(), self.records)

    def test_manager_services_batch_opens_csv_upload_panel(self):
        self.login_as_role("Manager")
        self.app.button(key="primary_nav_amenities").click().run()
        self.assert_no_exception()
        self.app.button(key="services_batch").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "valuation")
        # AppTest represents expanders with an icon as Status blocks.
        batch_panel = next(panel for panel in [*self.app.expander, *self.app.status]
                           if panel.proto.label == "Định giá hàng loạt bằng CSV")
        self.assertTrue(batch_panel.proto.expanded)
        self.assertIn("batch_upload", {widget.key for widget in self.app.file_uploader})
        self.assertIn("batch_template", {widget.key for widget in self.app.download_button})
        self.assertTrue(self.app.button(key="batch_submit").disabled)
        self.assertNotIn("_open_batch_from_services", self.app.session_state)

    def test_support_contact_reaches_admin_inbox(self):
        self.login()
        self.app.button(key="primary_nav_contact").click().run()
        self.app.text_input(key="support_subject").set_value("Cần hỗ trợ tài khoản")
        self.app.text_area(key="support_content").set_value("Tôi cần được hướng dẫn cập nhật hồ sơ.")
        self.app.button(key="support_submit").click().run()
        self.assert_no_exception()
        self.assertTrue(any("Đã gửi yêu cầu thành công" in element.value
                            for element in self.app.success))
        self.assertEqual(self.app.text_input(key="support_subject").value, "")
        self.assertEqual(self.app.text_area(key="support_content").value, "")
        support_request = next(
            message for message in storage.load_messages()
            if message.get("to") == "support"
            and message.get("subject") == "Hỗ trợ tài khoản · Cần hỗ trợ tài khoản"
        )
        self.assertEqual(support_request["from"], self.accounts["alice"][0])
        saved_messages = storage.load_messages()
        self.app.run()
        self.assert_no_exception()
        self.assertTrue(any("Đã gửi yêu cầu thành công" in element.value
                            for element in self.app.success))
        self.assertEqual(storage.load_messages(), saved_messages)

        self.app.button(key="support_submit").click().run()
        self.assert_no_exception()
        self.assertEqual(list(self.app.success), [])
        self.assertTrue(any("Vui lòng nhập tiêu đề và nội dung yêu cầu" in element.value
                            for element in self.app.error))
        self.assertEqual(storage.load_messages(), saved_messages)

        self.app.button(key="logout").click().run()
        self.login_as_role("Admin")
        self.navigate("messages")
        inbox = " ".join(element.value for element in self.app.markdown)
        self.assertIn("Tôi cần được hướng dẫn cập nhật hồ sơ.", inbox)
        self.assertTrue(any("Cần hỗ trợ tài khoản" in element.value
                            for element in self.app.subheader))

    def test_support_contact_keeps_draft_and_reports_storage_failure(self):
        self.login()
        self.app.button(key="primary_nav_contact").click().run()
        self.app.text_input(key="support_subject").set_value("Cần hỗ trợ tài khoản")
        self.app.text_area(key="support_content").set_value("Tôi cần cập nhật hồ sơ.")
        saved_messages = storage.load_messages()
        with patch.object(storage, "_save_json", side_effect=OSError("Storage unavailable")):
            self.app.button(key="support_submit").click().run()
        self.assert_no_exception()
        self.assertEqual(list(self.app.success), [])
        self.assertTrue(any("Chưa gửi được yêu cầu" in element.value
                            for element in self.app.error))
        self.assertEqual(self.app.text_input(key="support_subject").value, "Cần hỗ trợ tài khoản")
        self.assertEqual(self.app.text_area(key="support_content").value, "Tôi cần cập nhật hồ sơ.")
        self.assertEqual(storage.load_messages(), saved_messages)

        self.app.button(key="support_submit").click().run()
        self.assert_no_exception()
        self.assertTrue(any("Đã gửi yêu cầu thành công" in element.value
                            for element in self.app.success))
        self.assertEqual(len(storage.load_messages()), len(saved_messages) + 1)

    def test_forum_supports_publishing_replies_likes_and_search(self):
        self.login()
        self.app.button(key="primary_nav_forum").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.view, "forum")

        self.app.selectbox(key="forum_new_category").select("Mua bán")
        self.app.text_input(key="forum_new_title").set_value("Kinh nghiệm xem nhà lần đầu")
        self.app.text_area(key="forum_new_content").set_value(
            "Mọi người thường kiểm tra những điểm nào khi đi xem nhà lần đầu?"
        )
        self.app.button(key="forum_submit_post").click().run()
        self.assert_no_exception()
        post = next(
            item for item in storage.load_forum_posts()
            if item.get("title") == "Kinh nghiệm xem nhà lần đầu"
        )
        self.assertEqual(post["author_id"], self.accounts["alice"][0])

        self.app.button(key=f"forum_open_{post['id']}").click().run()
        self.app.text_area(key=f"forum_comment_text_{post['id']}").set_value(
            "Mình thường kiểm tra ánh sáng và tiếng ồn vào nhiều khung giờ."
        )
        self.app.button(key=f"forum_submit_comment_{post['id']}").click().run()
        self.assert_no_exception()
        post = next(item for item in storage.load_forum_posts() if item["id"] == post["id"])
        self.assertEqual(len(post["comments"]), 1)

        self.app.button(key=f"forum_detail_like_{post['id']}").click().run()
        self.assert_no_exception()
        post = next(item for item in storage.load_forum_posts() if item["id"] == post["id"])
        self.assertIn(self.accounts["alice"][0], post["likes"])

        self.app.button(key="forum_back").click().run()
        self.app.text_input(key="forum_search").set_value("kinh nghiệm xem nhà")
        self.app.run()
        self.assertTrue(any(
            button.key == f"forum_open_{post['id']}" for button in self.app.button
        ))
        self.app.text_input(key="forum_search").set_value("không có chủ đề này")
        self.app.run()
        self.assertFalse(any(
            button.key == f"forum_open_{post['id']}" for button in self.app.button
        ))

    def test_forum_category_tags_and_featured_navigation(self):
        self.login()
        self.app.button(key="primary_nav_forum").click().run()
        self.assert_no_exception()

        self.app.pills(key="forum_tags").select("Mua bán").run()
        self.assert_no_exception()
        self.assertEqual(self.app.selectbox(key="forum_category").value, "Mua bán")
        button_keys = {button.key for button in self.app.button}
        self.assertIn("forum_open_home-viewing-checklist", button_keys)
        self.assertNotIn("forum_open_reading-home-estimates", button_keys)

        self.app.selectbox(key="forum_category").select("Định giá").run()
        self.assert_no_exception()
        self.assertEqual(self.app.pills(key="forum_tags").value, "Định giá")
        button_keys = {button.key for button in self.app.button}
        self.assertIn("forum_open_reading-home-estimates", button_keys)
        self.assertNotIn("forum_open_home-viewing-checklist", button_keys)

        self.app.selectbox(key="forum_category").select("Tất cả").run()
        self.app.button(key="forum_featured_open_home-viewing-checklist").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.forum_selected_id, "home-viewing-checklist")
        self.assertIn("forum_detail_like_home-viewing-checklist",
                      {button.key for button in self.app.button})
        self.app.button(key="forum_back").click().run()
        self.assert_no_exception()

        self.app.text_input(key="forum_search").set_value("không có chủ đề này")
        self.app.selectbox(key="forum_category").select("Định giá").run()
        self.app.button(key="forum_show_all").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.text_input(key="forum_search").value, "")
        self.assertEqual(self.app.selectbox(key="forum_category").value, "Tất cả")
        self.assertIn("forum_open_home-viewing-checklist",
                      {button.key for button in self.app.button})

        self.app.button(key="forum_show_popular").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.selectbox(key="forum_sort").value, "Nhiều trao đổi")

    def test_forum_recent_replies_and_feed_likes(self):
        posts = storage.default_forum_posts()
        parent_id = posts[1]["id"]
        posts[1]["comments"] = [{
            "id": "recent-viewing-comment",
            "author": "Bob",
            "author_id": self.accounts["bob"][0],
            "content": "Nên kiểm tra tiếng ồn vào cả buổi sáng và buổi tối.",
            "created_at": "2026-10-22T09:00:00+07:00",
        }]
        storage.save_forum_posts(posts)
        self.login()
        self.app.button(key="primary_nav_forum").click().run()
        self.assert_no_exception()

        self.app.button(key=f"forum_recent_open_{parent_id}_0").click().run()
        self.assert_no_exception()
        self.assertEqual(self.app.session_state.forum_selected_id, parent_id)
        self.assertTrue(any("Nên kiểm tra tiếng ồn" in item.value
                            for item in self.app.markdown))
        self.app.button(key="forum_back").click().run()
        self.assert_no_exception()

        self.app.button(key=f"forum_feed_like_{parent_id}").click().run()
        self.assert_no_exception()
        saved_post = next(post for post in storage.load_forum_posts()
                          if post["id"] == parent_id)
        self.assertEqual(saved_post["likes"], [self.accounts["alice"][0]])
        self.assertIn("Bỏ thích", self.app.button(key=f"forum_feed_like_{parent_id}").label)

        self.app.button(key=f"forum_feed_like_{parent_id}").click().run()
        self.assert_no_exception()
        saved_post = next(post for post in storage.load_forum_posts()
                          if post["id"] == parent_id)
        self.assertEqual(saved_post["likes"], [])

    def test_user_can_register_and_sign_in_with_internal_account(self):
        self.app.button(key='show_register').click().run()
        self.assertIn('register_full_name', [widget.key for widget in self.app.text_input])
        self.app.text_input(key='register_full_name').set_value('New HomeNest User')
        self.app.text_input(key='register_email').set_value('new.user@example.test')
        self.app.text_input(key='register_password').set_value('Unique-Password-123')
        self.app.text_input(key='register_password_confirm').set_value('Unique-Password-123')
        self.app.button(key='register_submit').click().run()
        self.assert_no_exception()
        self.assertTrue(self.app.session_state.logged_in,
                        ' | '.join(element.value for element in self.app.error))
        self.assertEqual(self.app.session_state.auth_mode, 'internal')
        self.assertEqual(self.app.session_state.role, 'User')
        self.assertEqual(self.app.session_state.username, 'new.user@example.test')
        self.assertEqual(
            self.auth_store.authenticate('new.user@example.test', 'Unique-Password-123')['role'],
            'User')

        self.app.button(key='logout').click().run()
        self.assertFalse(self.app.session_state.logged_in)
        self.assert_landing()
        self.open_login()
        self.app.button(key='show_register').click().run()
        self.app.button(key='back_to_login').click().run()
        self.app.text_input(key='login_email').set_value('new.user@example.test')
        self.app.text_input(key='login_password').set_value('Unique-Password-123')
        self.app.button(key='account_login_submit').click().run()
        self.assert_no_exception()
        self.assertTrue(self.app.session_state.logged_in)
        self.assertEqual(self.app.session_state.auth_mode, 'internal')

    def test_valuation_optional_fields_validate_and_batch_is_manager_feature(self):
        self.login()
        self.navigate('valuation')
        self.assertFalse(any(button.key == 'batch_submit' for button in self.app.button))
        self.assertFalse(any(button.key == 'model_info_load' for button in self.app.button))
        self.app.number_input(key='valuation_frontage').set_value(0.0).run()
        self.assertEqual(len(self.app.error), 1)
        self.assertEqual(len(self.app.metric), 0)
        self.assertNotIn('marker', self.valuation_map_query())
        self.app.number_input(key='valuation_frontage').set_value(4.0).run()
        self.assertEqual(len(self.app.error), 0)
        self.assertEqual(len(self.app.metric), 3)
        self.assert_no_exception()
        self.app.button(key='logout').click().run()
        self.login_as_role('Manager')
        self.navigate('valuation')
        self.assertTrue(self.app.button(key='batch_submit').disabled)
        self.assert_no_exception()

    def test_valuation_listing_prefills_inputs_and_switching_clears_old_map_pin(self):
        self.login()
        records = copy.deepcopy(self.records)
        first, second = records[:2]
        first.update(latitude=10.71, longitude=106.72,
                     address='Địa chỉ thử nghiệm tại Quận 7')
        self.app.session_state.properties = records
        self.navigate('valuation')
        self.assertIsNone(self.app.selectbox(key='valuation_listing').value)
        self.assertNotIn('marker', self.valuation_map_query())

        self.app.selectbox(key='valuation_listing').set_value(first['id']).run()
        self.assert_no_exception()
        self.assertEqual(self.app.selectbox(key='valuation_location').value, first['location'])
        self.assertEqual(self.app.selectbox(key='valuation_type').value, first['type'])
        self.assertEqual(self.app.number_input(key='valuation_area').value, first['area'])
        self.assertEqual(self.app.text_input(key='valuation_title').value, first['title'])
        self.assertEqual(self.app.number_input(key='valuation_bedrooms').value, first['bedrooms'])
        self.assertEqual(self.app.text_input(key='valuation_map_address').value, first['address'])
        marker = self.valuation_map_query()['marker'][0]
        self.assertEqual(tuple(map(float, marker.split(','))), (10.71, 106.72))

        self.app.selectbox(key='valuation_listing').set_value(second['id']).run()
        self.assert_no_exception()
        self.assertEqual(self.app.selectbox(key='valuation_location').value, second['location'])
        self.assertEqual(self.app.selectbox(key='valuation_type').value, second['type'])
        self.assertEqual(self.app.number_input(key='valuation_area').value, second['area'])
        self.assertIsNone(self.app.number_input(key='valuation_latitude').value)
        self.assertIsNone(self.app.number_input(key='valuation_longitude').value)
        self.assertNotIn('marker', self.valuation_map_query())

        self.app.selectbox(key='valuation_listing').set_value(None).run()
        self.assert_no_exception()
        self.assertEqual(self.app.text_input(key='valuation_title').value, '')
        self.assertIsNone(self.app.number_input(key='valuation_bedrooms').value)
        self.assertNotIn('marker', self.valuation_map_query())

    def test_valuation_map_requires_pair_accepts_zero_and_reset_keeps_cached_estimate(self):
        import valuation

        self.login()
        with patch('valuation.predict', wraps=valuation.predict) as predict:
            self.navigate('valuation')
            self.assertEqual(predict.call_count, 1)
            self.assertNotIn('marker', self.valuation_map_query())

            self.app.number_input(key='valuation_latitude').set_value(0.0).run()
            self.assert_no_exception()
            self.assertNotIn('marker', self.valuation_map_query())
            self.app.number_input(key='valuation_longitude').set_value(0.0).run()
            self.assert_no_exception()
            marker = self.valuation_map_query()['marker'][0]
            self.assertEqual(tuple(map(float, marker.split(','))), (0.0, 0.0))

            self.app.button(key='valuation_map_clear').click().run()
            self.assert_no_exception()
            self.assertIsNone(self.app.number_input(key='valuation_latitude').value)
            self.assertIsNone(self.app.number_input(key='valuation_longitude').value)
            self.assertNotIn('marker', self.valuation_map_query())
            self.assertEqual(predict.call_count, 1)

    def test_valuation_map_preserves_location_when_returning_to_page(self):
        self.login()
        records = copy.deepcopy(self.records)
        first = records[0]
        first.update(latitude=10.71, longitude=106.72,
                     address='Địa chỉ thử nghiệm tại Quận 7')
        self.app.session_state.properties = records
        self.navigate('valuation')
        self.app.selectbox(key='valuation_listing').set_value(first['id']).run()

        def assert_map_survives_navigation():
            marker = self.valuation_map_query()['marker']
            address = self.app.text_input(key='valuation_map_address').value
            self.navigate('overview')
            self.navigate('valuation')
            self.assertEqual(self.app.selectbox(key='valuation_listing').value, first['id'])
            self.assertEqual(self.valuation_map_query()['marker'], marker)
            self.assertEqual(self.app.text_input(key='valuation_map_address').value, address)

        assert_map_survives_navigation()
        self.app.number_input(key='valuation_latitude').set_value(10.81).run()
        self.app.number_input(key='valuation_longitude').set_value(106.82).run()
        assert_map_survives_navigation()
        self.assertEqual(self.app.number_input(key='valuation_latitude').value, 10.81)
        self.assertEqual(self.app.number_input(key='valuation_longitude').value, 106.82)

        candidates = [dict(latitude=21.03, longitude=105.85,
                           label='Khu vực thử nghiệm', kind='area')]
        with patch('property_map.geocode_address', return_value=candidates) as lookup:
            self.app.text_input(key='valuation_map_address').set_value('Địa chỉ tra cứu').run()
            self.app.button(key='valuation_map_search').click().run()
            self.app.selectbox(key='valuation_map_candidate').set_value(0).run()
            assert_map_survives_navigation()
            self.assertEqual(self.app.selectbox(key='valuation_map_candidate').value, 0)
            lookup.assert_called_once_with('Địa chỉ tra cứu')

    def test_valuation_map_remains_available_when_estimate_fails_or_has_no_comparables(self):
        from api_client import ValuationAPIError

        self.login()
        self.navigate('valuation')
        self.app.number_input(key='valuation_latitude').set_value(10.71).run()
        self.app.number_input(key='valuation_longitude').set_value(106.72).run()
        expected_marker = self.valuation_map_query()['marker']
        no_samples = dict(samples=[], skipped=0, unit_price=None, price=None,
                          low=None, high=None, _client_response_time_ms=0)
        for response in (ValuationAPIError('Dịch vụ định giá tạm thời không phản hồi.'), no_samples):
            with self.subTest(response=type(response).__name__):
                if '_valuation_cached_result' in self.app.session_state:
                    del self.app.session_state['_valuation_cached_result']
                kwargs = ({'side_effect': response} if isinstance(response, Exception)
                          else {'return_value': response})
                with patch('valuation.predict', **kwargs) as predict:
                    self.app.run()
                self.assert_no_exception()
                predict.assert_called_once()
                self.assertEqual(self.valuation_map_query()['marker'], expected_marker)
                self.assertEqual(len(self.app.metric), 0)
                if isinstance(response, Exception):
                    self.assertEqual(len(self.app.error), 1)
                else:
                    self.assertEqual(len(self.app.error), 0)
                    self.assertTrue(self.app.info)

    def test_valuation_address_lookup_requires_search_and_selection_and_clears_stale_pin(self):
        self.login()
        self.navigate('valuation')
        candidates = [dict(latitude=10.71, longitude=106.72,
                           label='Địa chỉ thử nghiệm tại Quận 7', kind='address')]
        with patch('property_map.geocode_address', return_value=candidates) as lookup:
            self.app.text_input(key='valuation_map_address').set_value('Địa chỉ thử nghiệm').run()
            lookup.assert_not_called()
            self.app.button(key='valuation_map_search').click().run()
            self.assert_no_exception()
            lookup.assert_called_once_with('Địa chỉ thử nghiệm')
            self.assertIsNone(self.app.selectbox(key='valuation_map_candidate').value)
            self.assertNotIn('marker', self.valuation_map_query())

            self.app.selectbox(key='valuation_map_candidate').set_value(0).run()
            self.assert_no_exception()
            marker = self.valuation_map_query()['marker'][0]
            self.assertEqual(tuple(map(float, marker.split(','))), (10.71, 106.72))
            self.app.text_input(key='valuation_map_address').set_value('Một địa chỉ khác').run()
            self.assert_no_exception()
            self.assertNotIn('marker', self.valuation_map_query())
            self.assertNotIn('valuation_map_candidate',
                             {widget.key for widget in self.app.selectbox})
            self.assertEqual(lookup.call_count, 1)

    def test_refresh_keeps_public_login_and_registration_routes(self):
        for screen, widget in (("login", "login_email"), ("register", "register_email")):
            with self.subTest(screen=screen):
                self.app = AppTest.from_file(str(APP_FILE), default_timeout=20)
                self.app.query_params["view"] = screen
                self.app.run()
                self.assert_no_exception()
                self.assertFalse(self.app.session_state.logged_in)
                self.assertEqual(self.app.session_state.public_page, "auth")
                self.assertEqual(self.app.session_state.auth_screen, screen)
                self.assertEqual(self.app.query_params["view"], [screen])
                self.assertIn(widget, [element.key for element in self.app.text_input])

    def refresh_from_browser_session(self, user, route):
        import api_client
        email, _, password, _ = self.accounts[user]
        account = api_client.login_account(email, password)
        handle = api_client.create_browser_session(account["access_token"])["browser_session"]
        self.app = AppTest.from_file(str(APP_FILE), default_timeout=20)
        self.app.query_params.update(route)
        with patch("browser_auth.enabled", return_value=True), \
                patch("browser_auth.browser_handle", return_value=handle):
            self.app.run()
        self.assert_no_exception()
        self.assertTrue(self.app.session_state.logged_in)
        self.assertEqual(self.app.session_state.username, email)

    def test_refresh_restores_login_listing_and_return_page_from_url(self):
        listing_id = self.records[2]["id"]
        self.refresh_from_browser_session("alice", {
            "view": "detail", "id": str(listing_id), "back": "mine",
        })
        self.assertEqual(self.app.session_state.view, "detail")
        self.assertEqual(self.app.session_state.selected_id, listing_id)
        self.assertEqual(self.app.session_state.return_view, "mine")
        self.assertEqual(self.app.query_params["id"], [str(listing_id)])
        self.assertIn("detail_back", [element.key for element in self.app.button])

    def test_refresh_preserves_admin_account_management_route(self):
        self.refresh_from_browser_session("admin", {"view": "accounts"})
        self.assertEqual(self.app.session_state.role, "Admin")
        self.assertEqual(self.app.session_state.view, "accounts")
        self.assertEqual(self.app.query_params["view"], ["accounts"])
        self.assertIn("account_selected", [element.key for element in self.app.selectbox])

    def test_refresh_cannot_grant_user_admin_access_through_url(self):
        self.refresh_from_browser_session("alice", {"view": "accounts"})
        self.assertEqual(self.app.session_state.role, "User")
        self.assertEqual(self.app.session_state.view, "overview")
        self.assertEqual(self.app.query_params["view"], ["overview"])
        self.assertNotIn("account_selected", [element.key for element in self.app.selectbox])
        self.assertNotIn("nav_accounts", [element.key for element in self.app.button])

    def test_refresh_rejects_invalid_listing_id_and_forbidden_return_page(self):
        self.refresh_from_browser_session("alice", {
            "view": "detail", "id": "../../accounts", "back": "accounts",
        })
        self.assertIsNone(self.app.session_state.selected_id)
        self.assertEqual(self.app.session_state.return_view, "overview")
        self.assertNotIn("id", self.app.query_params)

    def test_batch_csv_upload_errors_and_mixed_results(self):
        self.login_as_role('Manager')
        content = b'id,location,type,area\n'
        upload = SimpleNamespace(size=len(content), getvalue=lambda: content)
        with patch('streamlit.file_uploader', return_value=upload):
            self.navigate('valuation')
            self.app.button(key='batch_submit').click().run()
            self.assertEqual(len(self.app.error), 1)
            self.assertNotIn('batch_results', self.app.session_state)
            content = ('id,location,type,area\nA,Long An,Đất nền,20\n'
                       'B,Long An,Đất nền,abc\nC,Khác,Đất nền,20\n').encode('utf-8')
            upload.size = len(content)
            self.app.run()
            self.app.button(key='batch_submit').click().run()
            self.assert_no_exception()
            rows = self.app.session_state.batch_results
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]['Trạng thái'], 'Thành công')
            self.assertEqual(rows[1]['Trạng thái'], 'Dữ liệu không hợp lệ')
            self.assertEqual(rows[2]['Trạng thái'], 'Thiếu dữ liệu so sánh')
            content = b'id,location,type,area\n'
            self.app.run()
            self.assertNotIn('batch_results', self.app.session_state)


if __name__ == "__main__":
    unittest.main()
