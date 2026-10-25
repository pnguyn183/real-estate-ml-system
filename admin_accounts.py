"""Admin-only account directory and account controls through authenticated API calls."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from api_client import (ValuationAPIError, admin_create_user, admin_list_users,
                        admin_reset_user_password, admin_set_user_status,
                        admin_update_user, admin_user_detail)
from admin_ui import admin_heading, normalized
from session_auth import api_bearer_token


DETAIL_FIELDS = (
    "admin_account_name", "admin_account_phone", "admin_account_bio", "admin_account_note",
    "admin_account_status_reason", "admin_account_status_confirm",
    "admin_account_password", "admin_account_password_confirm",
    "admin_account_password_reason", "admin_account_password_confirmed",
    "account_listing",
)
CREATE_FIELDS = ("new_user_name", "new_user_email", "new_user_password", "new_user_password_confirm")
STATUS_OPTIONS = ("Tất cả", "Đang hoạt động", "Đã khóa")


def clear_detail_fields():
    for key in DETAIL_FIELDS:
        st.session_state.pop(key, None)


def reset_account_filters():
    st.session_state.account_search = ""
    st.session_state.account_status_filter = STATUS_OPTIONS[0]
    st.session_state.pop("account_selected", None)
    clear_detail_fields()


def local_time(value):
    if not value:
        return "Chưa có"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=7))).strftime("%d/%m/%Y · %H:%M")
    except ValueError:
        return str(value)


def completed(message, *, created_email=None):
    st.session_state.flash = message
    st.session_state._clear_account_detail = True
    if created_email:
        st.session_state._created_account = created_email
    st.rerun()


def render_account_create(token):
    with st.container(key="hn-account-create"), st.expander(
            "Tạo tài khoản User", icon=":material/person_add:"):
        st.subheader("Thêm người dùng mới")
        st.caption("Tài khoản mới luôn có vai trò User. Email đăng nhập được giữ cố định để liên kết đúng bài đăng và hồ sơ.")
        with st.form("admin_create_account", border=False):
            left, right = st.columns(2)
            name = left.text_input("Họ và tên *", max_chars=120, key="new_user_name")
            email = right.text_input("Email đăng nhập *", max_chars=254, key="new_user_email")
            left, right = st.columns(2)
            password = left.text_input("Mật khẩu *", type="password", max_chars=128, key="new_user_password",
                                       autocomplete="new-password")
            confirm = right.text_input("Nhập lại mật khẩu *", type="password", max_chars=128,
                                       key="new_user_password_confirm", autocomplete="new-password")
            st.caption("Mật khẩu cần từ 12 đến 128 ký tự. Các trường có dấu * là bắt buộc.")
            submitted = st.form_submit_button("Tạo tài khoản User", type="primary",
                                               icon=":material/person_add:", key="account_create_submit")
        if submitted:
            if password != confirm:
                st.error("Hai mật khẩu không trùng khớp.")
            elif not name.strip() or not email.strip():
                st.error("Vui lòng nhập họ tên và email đăng nhập.")
            else:
                try:
                    result = admin_create_user({"email": email.strip(), "full_name": name.strip(),
                                                "password": password}, token)
                except ValuationAPIError as error:
                    st.error(str(error))
                else:
                    completed("Đã tạo tài khoản User.", created_email=result["user"]["email"])


def render_accounts(on_open_listing):
    if st.session_state.get("role") != "Admin":
        st.error("Bạn không có quyền quản lý tài khoản.")
        return
    if st.session_state.pop("_clear_account_detail", False):
        clear_detail_fields()
    if created := st.session_state.pop("_created_account", None):
        for key in CREATE_FIELDS:
            st.session_state.pop(key, None)
        st.session_state.account_search = ""
        st.session_state.account_status_filter = "Tất cả"
        st.session_state.account_selected = created

    admin_heading("Quản lý tài khoản User",
                  "Theo dõi người dùng, cập nhật hồ sơ và kiểm soát quyền truy cập trong một không gian quản trị.",
                  section="TÀI KHOẢN")
    try:
        token = api_bearer_token()
        accounts = admin_list_users(token)["users"]
    except (ValuationAPIError, RuntimeError) as error:
        st.error(str(error))
        return

    with st.container(horizontal=True, key="hn-account-kpis"):
        st.metric("Tài khoản User", len(accounts), border=True, icon=":material/group:",
                  help="Tài khoản User đăng nhập bằng email và mật khẩu.")
        st.metric("Đang hoạt động", sum(user["is_active"] for user in accounts), border=True,
                  icon=":material/verified_user:")
        st.metric("Đã khóa", sum(not user["is_active"] for user in accounts), border=True,
                  icon=":material/lock:")
    render_account_create(token)
    st.session_state.setdefault("account_search", "")
    if st.session_state.get("account_status_filter") not in STATUS_OPTIONS:
        st.session_state.account_status_filter = STATUS_OPTIONS[0]
    with st.container(border=True, key="hn-account-directory"):
        st.subheader("Danh sách tài khoản")
        st.caption("Tìm người dùng và chọn một tài khoản để xem hồ sơ, hoạt động hoặc quản lý bảo mật.")
        left, right = st.columns([2, 1])
        query = left.text_input("Tìm người dùng", value=None, placeholder="Họ tên hoặc email…", key="account_search",
                                icon=":material/search:", max_chars=254, persist_state="session")
        state = right.selectbox("Trạng thái", STATUS_OPTIONS, index=None, key="account_status_filter",
                                persist_state="session")
        needle = normalized(query)
        filtered = [user for user in accounts
                    if (not needle or needle in normalized(f"{user['full_name']} {user['email']}"))
                    and (state == "Tất cả" or user["is_active"] == (state == "Đang hoạt động"))]
        filtered.sort(key=lambda user: (user["full_name"].casefold(), user["email"]))
        emails = [user["email"] for user in filtered]
        if st.session_state.get("account_selected") not in emails:
            st.session_state.pop("account_selected", None)
            clear_detail_fields()
            if emails:
                st.session_state.account_selected = emails[0]
        with st.container(horizontal=True, vertical_alignment="center", key="hn-account-directory-actions"):
            st.caption(f"Hiển thị {len(filtered)} / {len(accounts)} tài khoản")
            st.button("Tải lại danh sách", icon=":material/refresh:", key="account_reload")
            st.button("Xóa bộ lọc", icon=":material/filter_alt_off:", key="account_reset_filters",
                      on_click=reset_account_filters, disabled=not query and state == STATUS_OPTIONS[0])
        if not filtered:
            with st.container(key="hn-account-empty"):
                st.info("Không có tài khoản phù hợp với bộ lọc." if accounts else "Chưa có tài khoản User.",
                        icon=":material/person_search:")
                st.caption("Thử tìm bằng tên hoặc email khác, hoặc xóa bộ lọc để xem toàn bộ người dùng."
                           if accounts else "Mở mục Tạo tài khoản User phía trên để thêm người dùng đầu tiên.")
            return
        st.dataframe(pd.DataFrame([{
            "Họ và tên": user["full_name"], "Email đăng nhập": user["email"],
            "Trạng thái": "Đang hoạt động" if user["is_active"] else "Đã khóa",
            "Ngày tạo": local_time(user.get("created_at")),
            "Đăng nhập gần nhất": local_time(user.get("last_login_at")),
        } for user in filtered]), hide_index=True, width="stretch", height=min(390, 38 + 40 * len(filtered)),
                     row_height=40, column_config={
                         "Họ và tên": st.column_config.TextColumn(width="medium", pinned=True),
                         "Email đăng nhập": st.column_config.TextColumn(width="medium"),
                         "Trạng thái": st.column_config.TextColumn(width="small"),
                     })
        selected = st.selectbox("Chọn tài khoản để xem và quản lý", emails, index=None,
                                format_func=lambda email: next(
                                    f"{user['full_name']} · {email}" for user in filtered if user["email"] == email),
                                key="account_selected", on_change=clear_detail_fields, persist_state="session")
        st.caption("Thời gian hiển thị theo giờ Việt Nam (UTC+7).")

    if st.session_state.get("_account_form_user") != selected:
        clear_detail_fields()
        st.session_state._account_form_user = selected
    try:
        detail = admin_user_detail(selected, token)
    except ValuationAPIError as error:
        st.error(str(error))
        return
    user = detail["user"]
    profile = detail.get("profile", {})
    activity = detail.get("activity", {})
    for key, value in (("admin_account_name", user["full_name"]),
                       ("admin_account_phone", profile.get("phone", "")),
                       ("admin_account_bio", profile.get("bio", "")),
                       ("admin_account_note", user.get("admin_note", ""))):
        st.session_state.setdefault(key, value)
    with st.container(border=True, key="hn-account-detail"):
        st.caption("TÀI KHOẢN ĐANG CHỌN")
        with st.container(horizontal=True, vertical_alignment="center", horizontal_alignment="distribute"):
            st.subheader(user["full_name"])
            st.badge("Đang hoạt động" if user["is_active"] else "Đã khóa",
                     icon=":material/verified_user:" if user["is_active"] else ":material/lock:",
                     color="green" if user["is_active"] else "red")
        st.write(user["email"])
        if profile.get("email") and profile["email"] != user["email"]:
            st.caption(f"Email liên hệ · {profile['email']}")
        st.caption(f"Vai trò User · Tạo lúc {local_time(user.get('created_at'))} · "
                   f"Đăng nhập gần nhất {local_time(user.get('last_login_at'))}")
        if not user["is_active"]:
            st.warning("Tài khoản đã bị khóa. Lý do: " + (user.get("disabled_reason") or "Chưa có thông tin."))
        profile_tab, activity_tab, access_tab, audit_tab = st.tabs(
            ["Hồ sơ", "Hoạt động", "Truy cập & bảo mật", "Nhật ký quản trị"])
        with profile_tab, st.container(key="hn-account-profile"):
            st.subheader("Thông tin hồ sơ")
            st.caption("Cập nhật thông tin liên hệ và ghi chú để thuận tiện theo dõi người dùng.")
            with st.form("admin_account_profile", border=False):
                left, right = st.columns(2)
                name = left.text_input("Họ và tên", value=None, max_chars=120, key="admin_account_name")
                phone = right.text_input("Số điện thoại", value=None, max_chars=30,
                                         key="admin_account_phone")
                bio = st.text_area("Giới thiệu", value=None, max_chars=2000, key="admin_account_bio", height=110)
                note = st.text_area("Ghi chú nội bộ của quản trị viên", value=None,
                                   max_chars=2000, key="admin_account_note", height=110)
                st.caption("Ghi chú nội bộ chỉ xuất hiện trong khu vực quản trị. Email đăng nhập và vai trò không đổi qua biểu mẫu này.")
                submitted = st.form_submit_button("Lưu thông tin tài khoản", type="primary",
                                                  icon=":material/save:", key="account_update_submit")
            if submitted:
                try:
                    admin_update_user(selected, {"full_name": (name or "").strip(), "phone": (phone or "").strip(),
                                                 "bio": (bio or "").strip(), "admin_note": (note or "").strip()}, token)
                except ValuationAPIError as error:
                    st.error(str(error))
                else:
                    completed("Đã cập nhật thông tin tài khoản.")
            st.caption(f"Cập nhật gần nhất · {local_time(user.get('updated_at'))}")
        with activity_tab, st.container(key="hn-account-activity"):
            st.subheader("Hoạt động của người dùng")
            with st.container(horizontal=True, key="hn-account-activity-kpis"):
                for label, key in (("Tin đăng", "listing_count"), ("Tin bán", "sale_count"),
                                   ("Tin cho thuê", "rent_count"), ("Tin đã lưu", "favorite_count")):
                    st.metric(label, activity.get(key, 0), border=True)
            st.caption(f"Diễn đàn: {activity.get('forum_post_count', 0)} chủ đề · "
                       f"{activity.get('forum_comment_count', 0)} phản hồi · "
                       f"{activity.get('support_request_count', 0)} yêu cầu hỗ trợ đã gửi.")
            listings = detail.get("listings", [])
            st.subheader("Tin đăng của tài khoản")
            if listings:
                st.caption(f"{len(listings)} tin đăng · Mở một tin để xem nội dung hoặc cập nhật thông tin.")
                st.dataframe(pd.DataFrame([{
                    "ID": prop["id"], "Tiêu đề": prop.get("title", ""), "Khu vực": prop.get("location", ""),
                    "Nhu cầu": prop.get("status", ""), "Giá chào (₫)": prop.get("price", 0),
                    "Ngày đăng": prop.get("created_at", ""),
                } for prop in listings]), hide_index=True, width="stretch", height=min(390, 38 + 40 * len(listings)),
                    row_height=40,
                    column_config={"Giá chào (₫)": st.column_config.NumberColumn(format="%,.0f")})
                if st.session_state.get("account_listing") not in [prop["id"] for prop in listings]:
                    st.session_state.pop("account_listing", None)
                listing_id = st.selectbox("Tin đăng cần xem", [prop["id"] for prop in listings],
                    format_func=lambda pid: next(f"#{p['id']} · {p['title']}" for p in listings if p["id"] == pid),
                    key="account_listing")
                st.button("Mở tin để quản lý", icon=":material/open_in_new:", key="account_open_listing",
                          on_click=on_open_listing, args=(listing_id,))
            else:
                st.info("Tài khoản chưa có tin đăng.", icon=":material/real_estate_agent:")
        with access_tab, st.container(key="hn-account-access"):
            st.subheader("Truy cập & bảo mật")
            st.caption("Kiểm soát trạng thái đăng nhập hoặc cấp lại mật khẩu khi người dùng cần hỗ trợ.")
            render_account_access(user, token)
        with audit_tab, st.container(key="hn-account-audit"):
            st.subheader("Lịch sử quản trị")
            st.caption("Theo dõi thay đổi hồ sơ và quyền truy cập của tài khoản đang chọn.")
            audit = detail.get("audit", [])
            if audit:
                actions = {"create": "Tạo tài khoản", "update": "Cập nhật hồ sơ", "disable": "Khóa tài khoản",
                           "activate": "Mở khóa tài khoản", "reset_password": "Đặt lại mật khẩu",
                           "profile_update": "Người dùng cập nhật hồ sơ"}
                field_labels = {"full_name": "Họ tên", "phone": "Số điện thoại", "bio": "Giới thiệu",
                                "admin_note": "Ghi chú nội bộ", "contact_email": "Email liên hệ"}
                st.dataframe(pd.DataFrame([{
                    "Thời gian": local_time(event.get("created_at")), "Người thực hiện": event.get("actor", ""),
                    "Thao tác": actions.get(event.get("action"), event.get("action", "")),
                    "Lý do / nội dung": event.get("reason") or ", ".join(
                        field_labels.get(field, field) for field in event.get("details", {}).get("changed_fields", [])),
                } for event in audit]), hide_index=True, width="stretch", height=min(430, 38 + 40 * len(audit)),
                             row_height=40,
                             column_config={"Lý do / nội dung": st.column_config.TextColumn(width="large")})
                st.caption("Hiển thị các thao tác quản trị gần nhất. Nhật ký không lưu mật khẩu.")
            else:
                st.info("Chưa có thao tác quản trị được ghi nhận cho tài khoản này.", icon=":material/history:")
    st.caption("Danh sách này quản lý tài khoản User đăng nhập bằng email/mật khẩu. Tài khoản OIDC được quản lý tại nhà cung cấp danh tính.")


def render_account_access(user, token):
    active = user["is_active"]
    with st.container(border=True, key="hn-account-status"):
        st.subheader("Khóa quyền truy cập" if active else "Khôi phục quyền truy cập")
        st.caption("Khóa tài khoản sẽ kết thúc các phiên đăng nhập cũ khi người dùng thao tác tiếp. Bài đăng và hồ sơ được giữ lại.")
        with st.form("admin_account_status", border=False):
            reason = st.text_area("Lý do khóa tài khoản *" if active else "Lý do mở khóa tài khoản",
                                  max_chars=1000, key="admin_account_status_reason")
            confirm = st.checkbox("Tôi xác nhận khóa tài khoản này." if active else "Tôi xác nhận mở khóa tài khoản này.",
                                   key="admin_account_status_confirm")
            submitted = st.form_submit_button("Khóa tài khoản" if active else "Mở khóa tài khoản",
                icon=":material/lock:" if active else ":material/lock_open:", key="account_status_submit")
        if submitted:
            if not confirm:
                st.error("Vui lòng đánh dấu xác nhận thay đổi trạng thái tài khoản.")
            elif active and not reason.strip():
                st.error("Vui lòng nhập lý do khóa tài khoản.")
            else:
                try:
                    admin_set_user_status(user["email"], not active, reason.strip(), token)
                except ValuationAPIError as error:
                    st.error(str(error))
                else:
                    completed("Đã khóa tài khoản và thu hồi phiên cũ." if active else "Đã mở khóa tài khoản. Người dùng có thể đăng nhập lại.")
    with st.container(border=True, key="hn-account-password"):
        st.subheader("Đặt lại mật khẩu")
        st.caption("Mật khẩu mới cần từ 12 đến 128 ký tự. Mọi phiên cũ sẽ bị thu hồi; việc đặt lại không mở khóa tài khoản.")
        with st.form("admin_account_password", border=False):
            password = st.text_input("Mật khẩu mới *", type="password", max_chars=128, key="admin_account_password",
                                     autocomplete="new-password")
            confirm_password = st.text_input("Nhập lại mật khẩu mới *", type="password", max_chars=128,
                                             key="admin_account_password_confirm", autocomplete="new-password")
            reason = st.text_area("Lý do đặt lại mật khẩu *", max_chars=1000, key="admin_account_password_reason")
            confirmed = st.checkbox("Tôi xác nhận đặt lại mật khẩu và thu hồi các phiên đăng nhập cũ.",
                                     key="admin_account_password_confirmed")
            submitted = st.form_submit_button("Đặt lại mật khẩu", icon=":material/password:",
                                               key="account_password_submit")
        if submitted:
            if password != confirm_password:
                st.error("Hai mật khẩu không trùng khớp.")
            elif not confirmed or not reason.strip():
                st.error("Vui lòng nhập lý do và đánh dấu xác nhận đặt lại mật khẩu.")
            else:
                try:
                    admin_reset_user_password(user["email"], password, reason.strip(), token)
                except ValuationAPIError as error:
                    st.error(str(error))
                else:
                    completed("Đã đặt lại mật khẩu và thu hồi các phiên đăng nhập cũ.")
