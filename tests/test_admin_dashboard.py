"""Admin dashboard interaction regressions backed by isolated storage and API."""

import copy
from io import BytesIO
import unittest
from unittest.mock import patch

import pandas as pd

import storage
import test_app as app_suite


class AdminDashboardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = app_suite.AppInteractionTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app

    def login(self):
        self.fixture.login_as_role("Admin")

    def navigate(self, view):
        self.fixture.navigate(view)

    def assert_clean(self):
        self.fixture.assert_no_exception()
        self.assertEqual(list(self.app.error), [])

    def install_records(self, records):
        storage.save_properties(records)
        self.app.session_state.properties = copy.deepcopy(records)

    def listing_ids(self):
        frames = [element.value for element in self.app.dataframe
                  if "ID" in element.value.columns]
        self.assertEqual(len(frames), 1)
        return frames[0]["ID"].tolist()

    def test_listing_filters_combine_search_sort_reset_and_keep_results_on_return(self):
        self.login()
        self.navigate("listings")
        self.app.selectbox(key="admin_listing_status").select("Cho thuê").run()
        self.app.selectbox(key="admin_listing_kind").select("Căn hộ").run()
        self.app.text_input(key="manage_search").set_value("can ho").run()
        self.assert_clean()
        self.assertEqual(self.listing_ids(), [5])
        self.assertEqual(len(self.app.selectbox(key="manage_selected").options), 1)

        self.navigate("reports")
        self.navigate("listings")
        self.assertEqual(self.app.text_input(key="manage_search").value, "can ho")
        self.assertEqual(self.app.selectbox(key="admin_listing_status").value, "Cho thuê")
        self.assertEqual(self.listing_ids(), [5])

        self.app.text_input(key="manage_search").set_value("khong-co-tin-nay").run()
        self.assert_clean()
        self.assertFalse(any(widget.key == "manage_selected" for widget in self.app.selectbox))
        self.assertFalse(any(button.key == "manage_delete" for button in self.app.button))
        self.app.button(key="admin_listing_reset").click().run()
        self.app.selectbox(key="admin_listing_sort").select("Giá tăng dần").run()
        self.assert_clean()
        self.assertEqual(self.app.text_input(key="manage_search").value, "")
        expected = [record["id"] for record in sorted(self.fixture.records, key=lambda record: record["price"])]
        self.assertEqual(self.listing_ids(), expected)

    def test_report_export_matches_rent_filter_and_never_adds_rent_to_sale_total(self):
        records = copy.deepcopy(self.fixture.records)
        records[-1]["title"] = "=SUM(1,2)"
        self.install_records(records)
        self.login()
        self.navigate("reports")
        self.app.selectbox(key="admin_report_status").select("Cho thuê").run()
        self.app.selectbox(key="admin_report_location").select(records[-1]["location"]).run()
        self.assert_clean()
        metrics = {metric.label: metric.value for metric in self.app.metric}
        self.assertEqual(metrics["Tin phù hợp"], "1")
        self.assertEqual(metrics["Tổng giá chào bán"], "0 ₫")
        self.assertEqual(metrics["Trung vị đơn giá bán / m²"], "—")
        self.assertEqual(self.listing_ids(), [5])

        from csv_export import spreadsheet_safe_csv
        with patch("admin_dashboard.spreadsheet_safe_csv", wraps=spreadsheet_safe_csv) as export:
            self.app.run()
        self.assert_clean()
        frame = export.call_args.args[0]
        exported = pd.read_csv(BytesIO(spreadsheet_safe_csv(frame)))
        self.assertEqual(exported["id"].tolist(), [5])
        self.assertEqual(exported["status"].tolist(), ["Cho thuê"])
        self.assertEqual(exported["title"].tolist(), ["'=SUM(1,2)"])
        self.assertNotIn("password", exported.columns)

        self.app.selectbox(key="admin_report_period").select("7 ngày gần đây").run()
        self.assert_clean()
        self.assertEqual(next(metric.value for metric in self.app.metric if metric.label == "Tin phù hợp"), "0")
        self.assertTrue(self.app.get("download_button")[0].proto.disabled)

    def test_support_search_pagination_and_channel_keep_other_users_messages_private(self):
        messages = [{"from": "alice@example.test", "to": "support", "subject": f"Yêu cầu số {index:02d}",
                     "content": f"Nội dung hỗ trợ số {index:02d}", "created_at": f"2026-09-{index:02d} 09:00:00"}
                    for index in range(1, 26)]
        messages.extend([
            {"from": "alice@example.test", "to": "admin@example.test", "subject": "Xác minh riêng",
             "content": "Thông tin riêng cho quản trị viên.", "created_at": "2026-09-26 09:00:00"},
            {"from": "admin@example.test", "to": "bob@example.test", "subject": "PRIVATE BOB SENTINEL",
             "content": "Private content must never enter the Admin inbox.", "created_at": "2026-09-27 09:00:00"},
        ])
        storage.save_messages(messages)
        self.login()
        self.navigate("messages")
        self.assert_clean()
        subjects = [heading.value for heading in self.app.subheader]
        self.assertEqual(len(subjects), 20)
        self.assertEqual(subjects[0], "Xác minh riêng")
        self.assertNotIn("PRIVATE BOB SENTINEL", subjects)
        self.app.selectbox(key="admin_support_page").select(2).run()
        self.assertEqual(len(self.app.subheader), 6)
        self.app.selectbox(key="admin_support_channel").select("Gửi riêng cho bạn").run()
        self.assert_clean()
        self.assertEqual(self.app.selectbox(key="admin_support_page").value, 1)
        self.assertEqual([heading.value for heading in self.app.subheader], ["Xác minh riêng"])
        self.app.text_input(key="admin_support_search").set_value("xac minh").run()
        self.assertEqual([heading.value for heading in self.app.subheader], ["Xác minh riêng"])
        self.app.text_input(key="admin_support_search").set_value("PRIVATE BOB SENTINEL").run()
        self.assert_clean()
        self.assertEqual(list(self.app.subheader), [])
        self.assertFalse(any(widget.key == "admin_support_page" for widget in self.app.selectbox))

    def test_report_export_includes_matching_rows_from_pages_not_displayed(self):
        records = copy.deepcopy(self.fixture.records)
        for index in range(25):
            extra = copy.deepcopy(records[-1])
            extra.update(id=100 + index, title=f"Tin thuê bổ sung {index + 1:02d}")
            records.append(extra)
        self.install_records(records)
        self.login()
        self.navigate("reports")
        self.app.selectbox(key="admin_report_status").select("Cho thuê").run()
        self.assert_clean()
        self.assertEqual(len(self.listing_ids()), 20)
        from csv_export import spreadsheet_safe_csv
        with patch("admin_dashboard.spreadsheet_safe_csv", wraps=spreadsheet_safe_csv) as export:
            self.app.run()
        frame = export.call_args.args[0]
        self.assertEqual(set(frame["id"]), {5} | set(range(100, 125)))
        self.app.selectbox(key="admin_report_page").select(2).run()
        self.assert_clean()
        self.assertEqual(len(self.listing_ids()), 6)

    def test_quality_shortcut_and_refresh_reconcile_filters_with_saved_inventory(self):
        self.login()
        self.app.button(key="admin_review_missing_owner").click().run()
        self.assert_clean()
        self.assertEqual(self.app.session_state.view, "listings")
        self.assertEqual(self.app.selectbox(key="admin_listing_quality").value, "Chưa rõ người đăng")
        self.assertEqual(self.listing_ids(), [1])
        self.app.button(key="admin_listing_reset").click().run()
        self.app.selectbox(key="admin_listing_location").select(self.fixture.records[0]["location"]).run()
        remaining = [record for record in self.fixture.records if record["location"] != self.fixture.records[0]["location"]]
        storage.save_properties(remaining)
        self.app.button(key="admin_listing_refresh").click().run()
        self.assert_clean()
        self.assertEqual(self.app.selectbox(key="admin_listing_location").value, "Tất cả")
        self.assertEqual(set(self.listing_ids()), {record["id"] for record in remaining})

    def test_admin_can_publish_edit_and_delete_a_listing_from_dashboard_routes(self):
        self.login()
        self.navigate("editor")
        prefix = self.fixture.prefix()
        self.app.text_input(key=f"{prefix}_title").set_value("Tin mới từ dashboard Admin")
        self.app.text_input(key=f"{prefix}_location").set_value("Quận 7, TP.HCM")
        self.app.text_input(key=f"{prefix}_contact").set_value("0901234567")
        self.app.number_input(key=f"{prefix}_price").set_value(3_200_000_000)
        self.app.number_input(key=f"{prefix}_area").set_value(85)
        self.app.text_area(key=f"{prefix}_description").set_value("Căn hộ đã được kiểm tra nội dung và liên hệ.")
        self.app.button(key="submit_editor").click().run()
        self.assert_clean()
        self.assertEqual(self.app.session_state.view, "detail")
        created = next(record for record in self.fixture.stored_records() if record["id"] == 6)
        self.assertEqual(created["user_id"], self.fixture.accounts["admin"][0])
        self.app.button(key="detail_edit").click().run()
        prefix = self.fixture.prefix()
        self.app.text_input(key=f"{prefix}_title").set_value("Tin đã cập nhật từ dashboard Admin")
        self.app.button(key="submit_editor").click().run()
        self.assert_clean()
        self.assertEqual(self.app.session_state.view, "detail")
        self.assertEqual(next(record for record in self.fixture.stored_records() if record["id"] == 6)["title"],
                         "Tin đã cập nhật từ dashboard Admin")
        self.app.button(key="detail_back").click().run()
        self.assertEqual(self.app.session_state.view, "listings")
        self.app.selectbox(key="manage_selected").select(6).run()
        self.app.button(key="manage_delete").click().run()
        self.assertEqual(len(self.fixture.stored_records()), 6)
        self.app.button(key="confirm_delete").click().run()
        self.assert_clean()
        self.assertEqual({record["id"] for record in self.fixture.stored_records()}, {1, 2, 3, 4, 5})


if __name__ == "__main__":
    unittest.main()
