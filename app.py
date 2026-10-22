"""HomeNest: listing management and comparable asking-price analysis."""
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit
from uuid import uuid4

import pandas as pd
import streamlit as st

from api_client import (ValuationAPIError, health as valuation_health,
                        login_account, register_account, update_account_profile)
from csv_export import spreadsheet_safe_csv
from forum_media import MAX_FORUM_IMAGES, prepare_forum_images
from forum_view import format_forum_time, render_forum_header, render_forum_home
from services_view import render_services_page
from design_view import render_design_page
from session_auth import (current_identity, oidc_auth_configured,
                          oidc_id_token_exposed, api_bearer_token)
from storage import (IMAGES_DIR, load_properties, save_properties, load_users,
                     save_users, load_messages, save_messages, load_forum_posts,
                     save_forum_posts, detect_duplicate_properties, DataStoreError)
from valuation import render_valuation
from landing import photo_uri, render_landing
from admin_accounts import render_accounts
import browser_auth

st.set_page_config(page_title="HomeNest | Phân tích bất động sản",
                   page_icon=":material/real_estate_agent:", layout="wide")

st.html("<style>" + (Path(__file__).parent / "assets/style.css").read_text(encoding="utf-8") + "</style>")


def _load_store(loader):
    try:
        return loader()
    except DataStoreError as error:
        st.error(str(error), icon=":material/error_outline:")
        st.stop()


_load_properties, _load_users = load_properties, load_users
_load_messages, _load_forum_posts = load_messages, load_forum_posts


def load_properties():
    return _load_store(_load_properties)


def load_users():
    return _load_store(_load_users)


def load_messages():
    return _load_store(_load_messages)


def load_forum_posts():
    return _load_store(_load_forum_posts)


PROPERTY_ASSETS = Path(__file__).parent / "assets" / "properties"
PROPERTY_PHOTOS = (
    PROPERTY_ASSETS / "modern-home.jpg",
    PROPERTY_ASSETS / "interior-living.jpg",
    PROPERTY_ASSETS / "garden-home.jpg",
)
FORUM_ILLUSTRATIONS = {
    "Bắt đầu": "hero-residence.jpg",
    "Mua bán": "modern-home.jpg",
    "Cho thuê": "interior-living.jpg",
    "Định giá": "garden-home.jpg",
    "Pháp lý": "modern-home.jpg",
    "Khu vực & quy hoạch": "city-residences.jpg",
    "Khác": "interior-living.jpg",
}
HERO_PHOTO = PROPERTY_PHOTOS[0]
LOGIN_PHOTO = PROPERTY_ASSETS / "hero-residence.jpg"
PROPERTY_TYPES = ["Căn hộ", "Nhà phố", "Đất nền", "Biệt thự", "Khác"]
ADMIN_VIEWS = {"overview", "listings", "editor", "detail", "accounts", "duplicates", "reports", "settings", "messages"}
MEMBER_VIEWS = {"overview", "valuation", "explore", "market", "services", "design", "forum", "contact",
                "detail", "mine", "editor", "favorites", "messages", "profile"}


def allowed_view(view):
    return view in (ADMIN_VIEWS if st.session_state.get("role") == "Admin" else MEMBER_VIEWS)


def icon(name):
    return f":material/{name}:"


def money(value, compact=False):
    value = float(value or 0)
    if compact:
        for divisor, unit in [(1e9, "tỷ ₫"), (1e6, "triệu ₫")]:
            if abs(value) >= divisor:
                return f"{value / divisor:.2f}".rstrip("0").rstrip(".").replace(".", ",") + f" {unit}"
    return f"{value:,.0f}".replace(",", ".") + " ₫"


def heading(title, description, symbol="dashboard"):
    with st.container(key="hn-page-heading"):
        copy, visual = st.columns([1, 0.24], gap="medium", vertical_alignment="center")
        with copy:
            st.caption("HOMENEST / KHÔNG GIAN QUẢN TRỊ" if st.session_state.get("role") == "Admin"
                       else "HOMENEST / KHÔNG GIAN BẤT ĐỘNG SẢN")
            st.title(f"{icon(symbol)} {title}")
            st.caption(description)
        with visual:
            image_name = {
                "travel_explore": "city-residences.jpg",
                "query_stats": "garden-home.jpg",
                "monitoring": "city-residences.jpg",
                "inventory_2": "modern-home.jpg",
                "add_home": "modern-home.jpg",
                "forum": "interior-living.jpg",
                "contact_support": "interior-living.jpg",
                "account_circle": "hero-residence.jpg",
                "manage_accounts": "hero-residence.jpg",
                "admin_panel_settings": "city-residences.jpg",
            }.get(symbol, "modern-home.jpg")
            st.image(str(PROPERTY_ASSETS / image_name), width="stretch")


def navigate(view, selected=None):
    if not allowed_view(view):
        st.session_state.flash_error = "Bạn không có quyền truy cập mục này."
        return
    st.session_state.view = view
    st.session_state.selected_id = selected
    st.session_state.pop("contact_mode", None)
    st.session_state.pop("pending_delete", None)
    if view == "editor":
        st.session_state.editor_revision += 1
    write_route_url()


def open_detail(property_id, contact=False):
    st.session_state.return_view = st.session_state.view
    navigate("detail", property_id)
    st.session_state.contact_mode = contact


def open_managed_listing(property_id):
    st.session_state.properties = load_properties()
    open_detail(property_id)


def logout():
    oidc_session = st.session_state.get("auth_mode") == "oidc"
    cookie_session = browser_auth.enabled()
    if cookie_session:
        browser_auth.revoke_current_session()
    for key in list(st.session_state):
        del st.session_state[key]
    clear_route_url()
    if cookie_session:
        browser_auth.queue_cookie_clear()
    if oidc_session:
        st.logout()


def clear_route_url():
    for key in ("view", "id", "back", "topic"):
        st.query_params.pop(key, None)


def write_route_url():
    values = {key: value for key, value in st.query_params.to_dict().items()
              if key not in {"view", "id", "back", "topic"}}
    if st.session_state.get("logged_in"):
        view = st.session_state.get("view", "overview")
        if not allowed_view(view):
            return
        values["view"] = view
        if view in {"detail", "editor"} and st.session_state.get("selected_id") is not None:
            values["id"] = str(st.session_state.selected_id)
        if view == "detail":
            back = st.session_state.get("return_view", "overview")
            values["back"] = back if allowed_view(back) and back != "detail" else "overview"
        if view == "forum" and st.session_state.get("forum_selected_id"):
            values["topic"] = st.session_state.forum_selected_id
    elif st.session_state.get("public_page") == "auth":
        values["view"] = st.session_state.get("auth_screen", "login")
    if st.query_params.to_dict() != values:
        st.query_params.from_dict(values)


def restore_route_url():
    if st.session_state.get("_route_initialized"):
        return
    st.session_state._route_initialized = True
    requested = st.query_params.get("view")
    if not st.session_state.logged_in:
        if requested in {"login", "register"}:
            st.session_state.public_page = "auth"
            st.session_state.auth_screen = requested
        return
    if requested and allowed_view(requested):
        st.session_state.view = requested
        if requested in {"detail", "editor"}:
            try:
                selected = int(st.query_params.get("id", ""))
            except (TypeError, ValueError):
                selected = None
            st.session_state.selected_id = selected if selected is not None and selected > 0 else None
        if requested == "detail":
            back = st.query_params.get("back", "overview")
            st.session_state.return_view = back if allowed_view(back) and back != "detail" else "overview"
        if requested == "forum":
            topic = st.query_params.get("topic", "")
            if len(topic) <= 128 and topic:
                st.session_state.forum_selected_id = topic


def refresh_properties():
    st.session_state.properties = load_properties()
    st.session_state.flash = "Dữ liệu đã được cập nhật."


def can_manage(prop):
    return st.session_state.role == "Admin" or prop.get("user_id") == st.session_state.username


def image_path(filename):
    candidate = (IMAGES_DIR / str(filename)).resolve()
    return candidate if candidate.parent == IMAGES_DIR.resolve() and candidate.is_file() else None


def property_photo(prop):
    images = [image_path(name) for name in prop.get("images", [])]
    images = [path for path in images if path]
    if images:
        return images[0], True
    photo_index = sum(map(ord, str(prop.get("id", 0)))) % len(PROPERTY_PHOTOS)
    return PROPERTY_PHOTOS[photo_index], False


def toggle_favorite(property_id):
    identity = current_identity()
    if not identity or identity["role"] == "Admin":
        return
    favorites = list(st.session_state.favorites)
    if property_id in favorites:
        favorites.remove(property_id)
    else:
        favorites.append(property_id)
    users = load_users()
    users.setdefault(st.session_state.username, {})["favorites"] = favorites
    try:
        save_users(users)
    except OSError:
        st.session_state.flash_error = "Chưa lưu được mục yêu thích. Vui lòng thử lại."
        return
    st.session_state.favorites = favorites


def render_card(prop, prefix):
    with st.container(border=True, height="stretch", key=f"property-card-{prefix}-{prop['id']}"):
        photo, is_actual_photo = property_photo(prop)
        st.image(str(photo), width="stretch")
        if not is_actual_photo:
            st.caption("Ảnh tham khảo · Chưa có ảnh thực tế")
        with st.container(horizontal=True, key=f"property-tags-{prefix}-{prop['id']}"):
            st.badge(prop.get("status", "Bán"), color="green" if prop.get("status") == "Bán" else "blue")
            st.badge(prop.get("type", "Khác"), color="gray")
        st.subheader(prop.get("title", "Chưa có tiêu đề"))
        st.markdown(f"<span class='hn-property-price'>{money(prop.get('price'), compact=True)}</span>",
                    unsafe_allow_html=True)
        st.caption(f"{icon('location_on')} {prop.get('location', '')}")
        st.caption(f"{icon('square_foot')} {prop.get('area', 0):g} m²  ·  "
                   f"{icon('bed')} {prop.get('bedrooms', 0)} phòng ngủ")
        description = prop.get("description", "")
        st.write(description[:110].rstrip() + ("…" if len(description) > 110 else ""))
        if prop.get("rating", 0) > 0:
            st.caption(f"{icon('star')} {prop['rating']:g}/5 · {prop.get('reviews', 0)} đánh giá")
        with st.container(horizontal=True, gap="xsmall"):
            st.button("Chi tiết", icon=icon("open_in_new"), key=f"{prefix}_detail_{prop['id']}",
                      on_click=open_detail, args=(prop["id"],), type="primary")
            saved = prop["id"] in st.session_state.favorites
            st.button("Đã lưu" if saved else "Lưu tin", icon=icon("bookmark" if saved else "bookmark_add"),
                      key=f"{prefix}_fav_{prop['id']}", on_click=toggle_favorite, args=(prop["id"],))
            st.button("Liên hệ", icon=icon("call"), key=f"{prefix}_contact_{prop['id']}",
                      on_click=open_detail, args=(prop["id"], True))


def render_grid(properties, prefix):
    for start in range(0, len(properties), 3):
        for col, prop in zip(st.columns(3), properties[start:start + 3]):
            with col:
                render_card(prop, prefix)


def table(properties):
    rows = [{"ID": p["id"], "Tin đăng": p["title"], "Khu vực": p["location"],
             "Loại": p["type"], "Giá chào (₫)": p["price"], "Diện tích (m²)": p["area"],
             "Trạng thái": p["status"], "Ngày đăng": p.get("created_at", "")} for p in properties]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        "Giá chào (₫)": st.column_config.NumberColumn(format="%,.0f"),
        "Diện tích (m²)": st.column_config.NumberColumn(format="%.1f"),
        "Tin đăng": st.column_config.TextColumn(width="large"),
    })


def render_metrics(properties):
    sale = [p for p in properties if p.get("status") == "Bán"]
    rates = [p["price"] / p["area"] for p in sale if p.get("area", 0) > 0 and p.get("price", 0) > 0]
    with st.container(horizontal=True, key="hn-kpi-row"):
        st.metric("Tin trong danh mục", len(properties), border=True)
        st.metric("Đang chào bán", len(sale), border=True)
        st.metric("Đang cho thuê", sum(p.get("status") == "Cho thuê" for p in properties), border=True)
        st.metric("Trung vị giá bán / m²", money(median(rates), True) if rates else "—", border=True,
                  help="Trung vị đơn giá các tin bán hợp lệ; chưa điều chỉnh khu vực hoặc loại.")


def render_dashboard_metrics(properties):
    rates = [p["price"] / p["area"] for p in properties
             if p.get("status") == "Bán" and p.get("area", 0) > 0 and p.get("price", 0) > 0]
    if st.session_state.role == "User":
        mine = [p for p in properties if p.get("user_id") == st.session_state.username]
        with st.container(horizontal=True, key="hn-kpi-row"):
            st.metric("Tin của tôi", len(mine), border=True)
            st.metric("Đang bán", sum(p.get("status") == "Bán" for p in mine), border=True)
            st.metric("Tin đã lưu", len(st.session_state.favorites), border=True)
            st.metric("Trung vị thị trường / m²",
                      money(median(rates), True) if rates else "—", border=True,
                      help="Tham khảo từ tin bán hiện có trong HomeNest.")
        return
    render_metrics(properties)


def render_charts(properties):
    left, right = st.columns([1, 1.3])
    with left, st.container(border=True, height="stretch", key="hn-chart-types"):
        st.subheader("Cơ cấu danh mục")
        st.caption("Số tin theo loại bất động sản")
        if properties:
            counts = pd.Series([p["type"] for p in properties]).value_counts()
            st.bar_chart(counts.rename_axis("Loại").rename("Số tin").reset_index(),
                         x="Loại", y="Số tin", horizontal=True, height=270)
        else:
            st.info("Chưa có tin đăng để thống kê.", icon=icon("inventory_2"))
    with right, st.container(border=True, height="stretch", key="hn-chart-prices"):
        st.subheader("Mặt bằng giá chào bán")
        st.caption("Trung vị đơn giá theo khu vực · triệu đồng/m²")
        rows = [{"Khu vực": p["location"], "Triệu đồng/m²": p["price"] / p["area"] / 1e6}
                for p in properties if p.get("status") == "Bán" and p.get("area", 0) > 0 and p.get("price", 0) > 0]
        if rows:
            frame = pd.DataFrame(rows).groupby("Khu vực", as_index=False)["Triệu đồng/m²"].median()
            st.bar_chart(frame, x="Khu vực", y="Triệu đồng/m²", horizontal=True, height=270)
        else:
            st.info("Chưa có tin bán hợp lệ để so sánh.", icon=icon("query_stats"))
    st.caption("Thống kê từ danh mục hiện có, chưa đại diện cho toàn bộ thị trường. Tin cho thuê được tách khỏi phân tích giá bán.")


def render_overview(properties):
    first_name = st.session_state.get("display_name", "").strip().split(" ")[0]
    greeting = f"Chào {first_name}, cùng khám phá nơi bạn muốn gọi là nhà." if first_name else "Khám phá nơi bạn muốn gọi là nhà."
    sale_count = sum(prop.get("status") == "Bán" for prop in properties)
    rent_count = sum(prop.get("status") == "Cho thuê" for prop in properties)
    with st.container(key="hn-member-home"):
        st.html(
            "<style>.st-key-hn-member-home-hero { --hn-home-hero-image: url('"
            + photo_uri("hero-residence.jpg") + "'); }</style>"
        )
        with st.container(key="hn-member-home-hero"):
            with st.container(key="hn-member-home-hero-content"):
                st.caption("HOMENEST · KHÔNG GIAN SỐNG CỦA BẠN")
                st.markdown(f"# {greeting}")
                st.write(
                    "Khám phá những không gian sống nổi bật, tìm tin phù hợp và tham khảo "
                    "mặt bằng giá — tất cả trong một trải nghiệm bất động sản liền mạch."
                )
                with st.container(horizontal=True, key="hn-member-home-actions"):
                    st.button("Khám phá bất động sản", icon=icon("travel_explore"),
                              type="primary", key="home_explore",
                              on_click=navigate, args=("explore",))
                    st.button("Tham khảo giá", icon=icon("query_stats"), key="home_price_guide",
                              on_click=navigate, args=("valuation",))
                with st.container(horizontal=True, key="hn-member-home-stats"):
                    st.metric("Tin đang bán", sale_count)
                    st.metric("Tin cho thuê", rent_count)
                    st.metric("Lựa chọn đang có", len(properties))
                st.caption("Ảnh minh họa · Thông tin và giá tham khảo theo danh mục HomeNest")

        st.markdown("### Dự án & bất động sản tiêu biểu")
        st.caption(
            "Một vài lựa chọn nổi bật từ danh mục HomeNest. Mỗi tin đăng giúp bạn khám phá "
            "thêm một khu vực và phong cách sống khác nhau."
        )
        featured = sorted(
            properties,
            key=lambda prop: (prop.get("rating", 0), prop.get("created_at", "")),
            reverse=True,
        )[:3]
        if featured:
            render_grid(featured, "home_featured")
            with st.container(horizontal=True, key="hn-member-home-project-link"):
                st.caption("Đang tìm một lựa chọn khác? Xem toàn bộ tin và lọc theo nhu cầu.")
                st.button("Xem tất cả bất động sản", icon=icon("arrow_forward"),
                          key="home_all_properties", on_click=navigate, args=("explore",))
        else:
            st.info("Danh mục đang được cập nhật. Hãy quay lại để khám phá các lựa chọn mới.",
                    icon=icon("add_home"))

        with st.container(key="hn-member-home-transparency"):
            st.caption("HOMENEST · THÔNG TIN RÕ RÀNG")
            st.markdown("### Tìm hiểu kỹ hơn trước khi lựa chọn")
            st.write(
                "Hình ảnh giúp bạn hình dung không gian; thông tin trong từng tin đăng "
                "và dữ liệu danh mục giúp bạn có thêm cơ sở để tham khảo."
            )
            trust_columns = st.columns(3)
            trust_items = [
                ("interior-living.jpg", "Hình ảnh để hình dung",
                 "Ảnh minh họa giúp khám phá phong cách không gian; hãy xem ghi chú ảnh và "
                 "thông tin thực tế của từng tin đăng."),
                ("city-residences.jpg", "Thông tin theo từng tin",
                 "Xem loại hình, khu vực, diện tích và trạng thái bán hoặc cho thuê trước khi liên hệ."),
                ("garden-home.jpg", "Giá có ngữ cảnh",
                 "Mức giá dùng để tham khảo từ danh mục HomeNest, không đại diện giá giao dịch "
                 "hay định giá độc lập."),
            ]
            for column, (photo, title, description) in zip(trust_columns, trust_items):
                with column, st.container(key=f"hn-member-home-transparency-{photo}",
                                          border=True, height="stretch"):
                    st.image(str(PROPERTY_ASSETS / photo), width="stretch")
                    st.caption("ẢNH THAM KHẢO")
                    st.subheader(title)
                    st.write(description)

        st.markdown("### Một nền tảng, nhiều cách tìm thấy tổ ấm")
        st.caption(
            "HomeNest kết nối việc khám phá tin đăng, tham khảo giá và trao đổi với cộng đồng "
            "trong cùng một không gian dễ sử dụng."
        )
        story_columns = st.columns(3)
        stories = [
            ("city-residences.jpg", "Khám phá đa dạng",
             "Tìm căn hộ, nhà phố, đất nền và biệt thự theo khu vực, loại hình và ngân sách.",
             "travel_explore", "Tìm bất động sản", "explore"),
            ("modern-home.jpg", "Tham khảo giá rõ ràng",
             "Đối chiếu giá chào bán với các tin tương đồng trong danh mục HomeNest.",
             "query_stats", "Xem phân tích giá", "valuation"),
            ("garden-home.jpg", "Lựa chọn tự tin hơn",
             "Lưu tin yêu thích, trao đổi với người đăng và theo dõi các lựa chọn của bạn.",
             "forum", "Ghé thăm cộng đồng", "forum"),
        ]
        for column, (photo, title, description, symbol, label, destination) in zip(
            story_columns, stories
        ):
            with column, st.container(key=f"hn-member-home-story-{destination}",
                                      border=True, height="stretch"):
                st.image(str(PROPERTY_ASSETS / photo), width="stretch")
                st.subheader(title)
                st.write(description)
                st.button(label, icon=icon(symbol), key=f"home_story_{destination}",
                          on_click=navigate, args=(destination,))

        with st.container(key="hn-member-home-cta"):
            cta_copy, cta_action = st.columns([1, 0.34], gap="medium",
                                               vertical_alignment="center")
            with cta_copy:
                st.caption("BẮT ĐẦU HÀNH TRÌNH CỦA BẠN")
                st.subheader("Ngôi nhà phù hợp có thể bắt đầu từ một lần khám phá.")
                st.write("Dùng bộ lọc HomeNest để tìm lựa chọn gần hơn với mong muốn của bạn.")
            with cta_action:
                st.button("Tìm không gian phù hợp", icon=icon("arrow_forward"),
                          type="primary", key="home_final_explore", width="stretch",
                          on_click=navigate, args=("explore",))


def render_admin_overview(properties):
    heading("Tổng quan quản trị", "Quản lý tài khoản, nội dung đăng tải và các yêu cầu hỗ trợ trong HomeNest.", "admin_panel_settings")
    with st.container(border=True, key="hn-admin-welcome"):
        hero_copy, hero_image = st.columns([1.1, 0.9], gap="large", vertical_alignment="center")
        with hero_copy:
            st.caption("HOMENEST · KHÔNG GIAN QUẢN TRỊ")
            st.subheader("Vận hành danh mục bất động sản")
            st.write("Theo dõi hoạt động, quản lý tin đăng và hỗ trợ thành viên từ một không gian thống nhất.")
            with st.container(horizontal=True, key="hn-admin-hero-actions"):
                st.button("Quản lý tài khoản User", icon=icon("manage_accounts"), type="primary",
                          key="admin_overview_accounts", on_click=navigate, args=("accounts",))
                st.button("Đăng tin mới", icon=icon("add_home"), key="admin_overview_publish",
                          on_click=navigate, args=("editor",))
        with hero_image:
            st.image(str(HERO_PHOTO), width="stretch")
            st.caption("Không gian sống HomeNest · Ảnh tham khảo")
    with st.container(horizontal=True, key="hn-admin-kpis"):
        st.metric("Tin trong danh mục", len(properties), border=True)
        st.metric("Tin bán", sum(p.get("status") == "Bán" for p in properties), border=True)
        st.metric("Tin cho thuê", sum(p.get("status") == "Cho thuê" for p in properties), border=True)
        st.metric("Yêu cầu hỗ trợ", sum(m.get("to") == "support" for m in load_messages()), border=True)
    shortcuts = [
        ("inventory_2", "Quản lý nội dung", "Xem, chỉnh sửa và xóa các tin trong danh mục.", "listings"),
        ("manage_accounts", "Quản lý người dùng", "Hồ sơ, hoạt động, trạng thái truy cập và nhật ký tài khoản User.", "accounts"),
        ("difference", "Rà soát tin trùng", "Đối chiếu các nhóm tin có dấu hiệu tương tự trước khi xử lý.", "duplicates"),
        ("assessment", "Báo cáo danh mục", "Theo dõi số liệu vận hành và xuất dữ liệu tin đăng.", "reports"),
    ]
    for start in range(0, len(shortcuts), 2):
        for column, (symbol, title, description, view) in zip(st.columns(2), shortcuts[start:start + 2]):
            with column, st.container(border=True, height="stretch"):
                st.subheader(f"{icon(symbol)} {title}")
                st.write(description)
                st.button("Mở quản lý", icon=icon("arrow_forward"), key=f"admin_shortcut_{view}",
                          on_click=navigate, args=(view,))
    st.subheader("Tin đăng gần đây")
    if properties:
        table(sorted(properties, key=lambda p: p.get("created_at", ""), reverse=True)[:5])
    else:
        st.info("Danh mục chưa có tin đăng.")


def render_explore(properties):
    heading("Khám phá bất động sản", "Tìm tin theo nhu cầu và lưu những bất động sản bạn quan tâm.", "travel_explore")
    with st.container(border=True):
        st.markdown(f"**{icon('tune')} Bộ lọc tìm kiếm**")
        a, b, c = st.columns([2, 1, 1])
        search = a.text_input("Từ khóa", placeholder="Tiêu đề, khu vực hoặc mô tả", key="search_query", persist_state="session")
        types = sorted(set(PROPERTY_TYPES) | {p["type"] for p in properties})
        kind = b.selectbox("Loại bất động sản", ["Tất cả"] + types, key="search_type", persist_state="session")
        status = c.selectbox("Nhu cầu", ["Tất cả", "Bán", "Cho thuê"], key="search_status", persist_state="session")
        a, b = st.columns([2, 1])
        max_price = a.number_input("Ngân sách tối đa (VNĐ)", min_value=0, value=0, step=100000000,
                                   help="Để 0 để xem mọi mức giá.", key="search_budget", persist_state="session")
        order = b.selectbox("Sắp xếp", ["Mới nhất", "Giá tăng dần", "Giá giảm dần", "Diện tích lớn nhất"], key="search_sort", persist_state="session")
    query = search.strip().casefold()
    filtered = [p for p in properties if
                (not query or query in " ".join(str(p.get(k, "")) for k in ("title", "location", "description")).casefold())
                and (kind == "Tất cả" or p["type"] == kind)
                and (status == "Tất cả" or p["status"] == status)
                and (max_price == 0 or p["price"] <= max_price)]
    sort_key = {"Mới nhất": "created_at", "Giá tăng dần": "price", "Giá giảm dần": "price", "Diện tích lớn nhất": "area"}[order]
    filtered.sort(key=lambda p: p.get(sort_key, ""), reverse=order != "Giá tăng dần")
    st.subheader(f"{len(filtered)} bất động sản phù hợp")
    if not filtered:
        st.info("Chưa có kết quả. Hãy thử từ khóa khác hoặc nới bộ lọc.", icon=icon("search_off"))
        return
    page_count = (len(filtered) - 1) // 9 + 1
    if st.session_state.get("search_page", 1) > page_count:
        st.session_state.search_page = 1
    page = st.selectbox("Trang kết quả", range(1, page_count + 1), key="search_page", persist_state="session")
    render_grid(filtered[(page - 1) * 9:page * 9], "search")


def request_delete(property_id):
    st.session_state.pending_delete = property_id


def clear_delete():
    st.session_state.pop("pending_delete", None)


@st.dialog("Xóa tin đăng", icon=":material/delete:", on_dismiss=clear_delete)
def confirm_delete(property_id):
    current = load_properties()
    prop = next((p for p in current if p["id"] == property_id), None)
    if not prop or not can_manage(prop):
        st.error("Tin không còn tồn tại hoặc bạn không có quyền xóa.")
        return
    st.write(f"Bạn muốn xóa tin “{prop['title']}” khỏi danh mục?")
    left, right = st.columns(2)
    if left.button("Xóa tin", type="primary", icon=icon("delete"), key="confirm_delete"):
        try:
            updated = [p for p in current if p["id"] != property_id]
            save_properties(updated)
        except OSError:
            st.error("Chưa xóa được tin. Vui lòng thử lại.")
            return
        st.session_state.properties = updated
        st.session_state.flash = "Tin đăng đã được xóa."
        navigate("listings" if st.session_state.role == "Admin" else "mine")
        st.rerun()
    if right.button("Giữ lại", icon=icon("close"), key="cancel_delete"):
        clear_delete()
        st.rerun()


def render_detail(properties):
    prop = next((p for p in properties if p["id"] == st.session_state.selected_id), None)
    st.button("Quay lại danh sách", icon=icon("arrow_back"), on_click=navigate,
              args=(st.session_state.get("return_view", "overview"),), key="detail_back")
    if prop is None:
        st.info("Tin đăng không còn trong danh mục.", icon=icon("info"))
        return
    heading(prop["title"], prop["location"], "home_work")
    left, right = st.columns([2, 1])
    with left:
        images = [path for name in prop.get("images", []) if (path := image_path(name))]
        if images:
            st.image(str(images[0]), width="stretch")
            if len(images) > 1:
                with st.expander(f"Xem thêm {len(images) - 1} ảnh", icon=icon("photo_library")):
                    st.image([str(path) for path in images[1:]], width="stretch")
        else:
            st.image(str(property_photo(prop)[0]), width="stretch")
            st.caption("Ảnh tham khảo HomeNest · Người đăng chưa cung cấp ảnh thực tế.")
        st.subheader("Thông tin bất động sản")
        with st.container(horizontal=True):
            st.metric("Diện tích", f"{prop['area']:g} m²", border=True)
            st.metric("Phòng ngủ", prop["bedrooms"], border=True)
            st.metric("Phòng tắm", prop["bathrooms"], border=True)
        st.subheader("Mô tả chi tiết")
        st.write(prop.get("description") or "Chưa có mô tả.")
    with right, st.container(border=True):
        st.badge(prop["status"], color="green")
        st.subheader(money(prop["price"], True))
        st.caption(money(prop["price"]))
        if prop.get("area", 0) > 0:
            st.caption(f"Đơn giá · {money(prop['price'] / prop['area'])}/m²")
        st.markdown(f"**{icon('call')} {'Thông tin liên hệ' if st.session_state.role == 'Admin' else 'Liên hệ người đăng'}**")
        st.write(prop.get("contact") or "Chưa có thông tin liên hệ")
        owner = prop.get("user_id")
        users = load_users()
        st.caption(f"Người đăng · {users.get(owner, {}).get('full_name') or owner or 'Ẩn danh'}")
        st.caption(f"Ngày đăng · {prop.get('created_at', 'Chưa có thông tin')}")
        if st.session_state.get("contact_mode"):
            st.info("Bạn có thể liên hệ trực tiếp qua số điện thoại ở trên.", icon=icon("contact_phone"))
        if st.session_state.role != "Admin":
            saved = prop["id"] in st.session_state.favorites
            st.button("Bỏ lưu tin" if saved else "Lưu vào yêu thích", icon=icon("bookmark"),
                      on_click=toggle_favorite, args=(prop["id"],), width="stretch", key="detail_favorite")
        if can_manage(prop):
            st.button("Chỉnh sửa tin", icon=icon("edit_note"), on_click=navigate,
                      args=("editor", prop["id"]), width="stretch", key="detail_edit")
            st.button("Xóa tin đăng", icon=icon("delete_outline"), width="stretch", key="detail_delete",
                      on_click=request_delete, args=(prop["id"],))


def render_listing_management(properties, mine=False):
    heading("Bài đăng của tôi" if mine else "Quản lý tin đăng",
            "Theo dõi, cập nhật nội dung và quản lý các bất động sản trong danh mục.", "inventory_2")
    records = [p for p in properties if not mine or p.get("user_id") == st.session_state.username]
    st.button("Thêm tin đăng", icon=icon("add_home"), type="primary", on_click=navigate, args=("editor",))
    query = st.text_input("Tìm trong danh sách", placeholder="Tiêu đề hoặc khu vực", key="manage_search")
    records = [p for p in records if not query or query.casefold() in f"{p['title']} {p['location']}".casefold()]
    records.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    if not records:
        st.info("Chưa có tin phù hợp trong danh sách.", icon=icon("inventory_2"))
        return
    st.caption(f"{len(records)} tin đăng")
    table(records)
    with st.container(border=True):
        st.markdown("**Thao tác với tin đăng**")
        selected = st.selectbox("Chọn tin", [p["id"] for p in records],
                                format_func=lambda pid: next(f"#{p['id']} · {p['title']}" for p in records if p["id"] == pid),
                                key="manage_selected")
        with st.container(horizontal=True):
            st.button("Xem chi tiết", icon=icon("open_in_new"), on_click=open_detail, args=(selected,), key="manage_detail")
            st.button("Chỉnh sửa", icon=icon("edit_note"), on_click=navigate, args=("editor", selected), key="manage_edit")
            st.button("Xóa tin", icon=icon("delete_outline"), key="manage_delete", on_click=request_delete, args=(selected,))


def reset_editor():
    st.session_state.editor_revision += 1


def render_editor(properties):
    selected = st.session_state.selected_id
    prop = next((p for p in properties if p["id"] == selected), None) if selected is not None else None
    if selected is not None and (prop is None or not can_manage(prop)):
        st.error("Tin đăng không tồn tại hoặc bạn không có quyền chỉnh sửa.")
        return
    heading("Chỉnh sửa tin đăng" if prop else "Đăng bất động sản mới",
            "Thông tin rõ ràng giúp người mua dễ so sánh và liên hệ. Các trường có * là bắt buộc.", "edit_note" if prop else "add_home")
    prefix = f"editor_{st.session_state.editor_revision}"
    defaults = prop or {}
    types = list(dict.fromkeys(PROPERTY_TYPES + ([defaults["type"]] if defaults.get("type") else [])))
    with st.form(prefix, border=True):
        st.subheader("Thông tin cơ bản")
        title = st.text_input("Tiêu đề *", value=defaults.get("title", ""), key=f"{prefix}_title",
                              placeholder="Ví dụ: Căn hộ 2 phòng ngủ, ban công rộng")
        a, b = st.columns(2)
        with a:
            kind = st.selectbox("Loại bất động sản *", types, index=types.index(defaults.get("type", "Căn hộ")), key=f"{prefix}_type")
            location = st.text_input("Khu vực / địa chỉ *", value=defaults.get("location", ""), key=f"{prefix}_location")
            price = st.number_input("Giá chào (VNĐ) *", min_value=0, value=int(defaults.get("price", 0)), step=1000000, key=f"{prefix}_price")
            status = st.selectbox("Nhu cầu *", ["Bán", "Cho thuê"], index=0 if defaults.get("status", "Bán") == "Bán" else 1, key=f"{prefix}_status")
        with b:
            area = st.number_input("Diện tích (m²) *", min_value=0.0, value=float(defaults.get("area", 0)), step=1.0, key=f"{prefix}_area")
            bedrooms = st.number_input("Phòng ngủ", min_value=0, value=int(defaults.get("bedrooms", 0)), step=1, key=f"{prefix}_bedrooms")
            bathrooms = st.number_input("Phòng tắm", min_value=0, value=int(defaults.get("bathrooms", 0)), step=1, key=f"{prefix}_bathrooms")
            contact = st.text_input("Số điện thoại liên hệ *", value=defaults.get("contact", ""), key=f"{prefix}_contact")
        description = st.text_area("Mô tả chi tiết *", value=defaults.get("description", ""), height=150, key=f"{prefix}_description")
        st.subheader("Hình ảnh bất động sản")
        existing = defaults.get("images", [])
        replace_images = st.checkbox("Thay thế toàn bộ ảnh hiện tại", value=False, key=f"{prefix}_replace") if existing else False
        if existing:
            st.caption(f"Tin đang có {len(existing)} ảnh. Ảnh mới sẽ được bổ sung nếu bạn không chọn thay thế.")
        files = st.file_uploader("Ảnh thực tế (tổng cộng tối đa 5 ảnh)", type=["jpg", "jpeg", "png"],
                                 accept_multiple_files=True, key=f"{prefix}_images")
        submitted = st.form_submit_button("Lưu thay đổi" if prop else "Đăng tin", type="primary",
                                          icon=icon("save" if prop else "publish"), width="stretch", key="submit_editor")
    with st.container(horizontal=True):
        st.button("Khôi phục nội dung" if prop else "Xóa form", icon=icon("restart_alt"), on_click=reset_editor, key="reset_editor")
        st.button("Về danh sách", icon=icon("arrow_back"), on_click=navigate,
                  args=("listings" if st.session_state.role == "Admin" else "mine",), key="editor_back")
    if not submitted:
        return
    missing = [label for label, value in [("Tiêu đề", title), ("Khu vực", location), ("Liên hệ", contact), ("Mô tả", description)] if not value.strip()]
    if price <= 0:
        missing.append("Giá phải lớn hơn 0")
    if area <= 0:
        missing.append("Diện tích phải lớn hơn 0")
    images = [] if replace_images else list(existing)
    if len(images) + len(files) > 5:
        missing.append("Tổng số ảnh không được vượt quá 5")
    if missing:
        st.error("Vui lòng kiểm tra: " + "; ".join(missing) + ".", icon=icon("error_outline"))
        return
    current = load_properties()
    latest = next((p for p in current if p["id"] == selected), None) if prop else None
    if prop and (latest is None or not can_manage(latest)):
        st.error("Tin đã thay đổi hoặc bị xóa. Hãy tải lại danh sách.")
        return
    pid = prop["id"] if prop else max((p["id"] for p in current), default=0) + 1
    written = []
    try:
        for upload in files:
            filename = f"{pid}_{uuid4().hex}{Path(upload.name).suffix.lower()}"
            target = IMAGES_DIR / filename
            written.append(target)
            target.write_bytes(upload.getbuffer())
            images.append(filename)
        record = {**(latest or {}), "id": pid, "title": title.strip(), "type": kind,
                  "location": location.strip(), "price": int(price), "area": float(area),
                  "bedrooms": int(bedrooms), "bathrooms": int(bathrooms), "status": status,
                  "description": description.strip(), "contact": contact.strip(), "images": images}
        if not prop:
            record.update(user_id=st.session_state.username, rating=0, reviews=0,
                          created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        else:
            record["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updated = [record if p["id"] == pid else p for p in current] if prop else current + [record]
        save_properties(updated)
    except OSError:
        for path in written:
            path.unlink(missing_ok=True)
        st.error("Chưa lưu được tin đăng. Nội dung nhập vẫn được giữ để bạn thử lại.")
        return
    st.session_state.properties = updated
    st.session_state.flash = "Đã cập nhật tin đăng." if prop else "Tin đăng của bạn đã được lưu thành công."
    st.session_state.return_view = "listings" if st.session_state.role == "Admin" else "mine"
    navigate("detail", pid)
    st.rerun()


def render_favorites(properties):
    heading("Bất động sản đã lưu", "Danh sách quan tâm được lưu theo tài khoản để bạn tiếp tục theo dõi.", "bookmarks")
    records = [p for p in properties if p["id"] in st.session_state.favorites]
    if records:
        render_grid(records, "saved")
    else:
        st.info("Bạn chưa lưu tin nào. Chọn Lưu tin khi khám phá bất động sản.", icon=icon("bookmark_add"))


def render_messages():
    is_admin = st.session_state.role == "Admin"
    heading("Hộp thư hỗ trợ" if is_admin else "Hộp thư",
            "Yêu cầu hỗ trợ và tin nhắn nhận được." if is_admin
            else "Các tin nhắn gửi đến tài khoản của bạn.", "inbox")
    messages = [
        message for message in load_messages()
        if message.get("to") == st.session_state.username
        or (is_admin and message.get("to") == "support")
    ]
    if not messages:
        st.info("Chưa có tin nhắn mới.", icon=icon("mail"))
    for msg in sorted(messages, key=lambda m: m.get("created_at", ""), reverse=True)[:20]:
        with st.container(border=True):
            sender = msg.get("from", "Ẩn danh")
            subject = msg.get("subject")
            st.markdown(f"**{icon('account_circle')} {sender}**")
            if subject:
                st.subheader(subject)
            st.caption(msg.get("created_at", ""))
            st.write(msg.get("content", ""))


def render_profile():
    heading("Hồ sơ cá nhân", "Cập nhật thông tin giúp người quan tâm dễ kết nối với bạn.", "account_circle")
    username = st.session_state.username
    user = load_users().get(username, {})
    if st.session_state.get("auth_mode") == "internal":
        user = {"full_name": st.session_state.display_name, "email": username, **user}
    with st.form("profile_form"):
        a, b = st.columns(2)
        full_name = a.text_input("Họ và tên", value=user.get("full_name", ""))
        phone = b.text_input("Số điện thoại", value=user.get("phone", ""))
        email = st.text_input("Email", value=user.get("email", ""))
        bio = st.text_area("Giới thiệu", value=user.get("bio", ""))
        if st.form_submit_button("Lưu hồ sơ", type="primary", icon=icon("save")):
            if st.session_state.get("auth_mode") == "internal":
                try:
                    account = update_account_profile({"full_name": full_name.strip(), "phone": phone.strip(),
                                                       "contact_email": email.strip(), "bio": bio.strip()},
                                                      api_bearer_token())
                except (ValuationAPIError, RuntimeError) as error:
                    st.error(str(error))
                else:
                    st.session_state.display_name = account["user"]["full_name"]
                    st.success("Hồ sơ đã được cập nhật.", icon=icon("check_circle"))
                return
            users = load_users()
            users[username] = {**users.get(username, {}), "full_name": full_name.strip(),
                               "email": email.strip(), "phone": phone.strip(), "bio": bio.strip()}
            try:
                save_users(users)
                st.success("Hồ sơ đã được cập nhật.", icon=icon("check_circle"))
            except OSError:
                st.error("Chưa lưu được hồ sơ. Vui lòng thử lại.")


def render_duplicates(properties):
    heading("Kiểm tra tin trùng", "Rà soát các nhóm có loại, khu vực, tiêu đề, giá và diện tích gần nhau.", "difference")
    groups = detect_duplicate_properties(properties)
    st.caption("Đây là các tin có dấu hiệu tương tự; cần đối chiếu nội dung trước khi xóa.")
    if not groups:
        st.success("Chưa phát hiện nhóm tin trùng trong danh mục.", icon=icon("fact_check"))
    for number, group in enumerate(groups, 1):
        with st.container(border=True):
            st.subheader(f"Nhóm {number} · {len(group)} tin")
            table(group)


def render_reports(properties):
    heading("Báo cáo danh mục", "Tổng hợp nguồn cung, giá chào bán và xuất dữ liệu phục vụ phân tích.", "assessment")
    render_metrics(properties)
    sale = [p for p in properties if p.get("status") == "Bán"]
    total = sum(p["price"] for p in sale)
    with st.container(horizontal=True):
        st.metric("Tổng giá chào bán", money(total, True), border=True, help=money(total))
        st.metric("Giá chào bán trung bình", money(total / len(sale), True) if sale else "—", border=True)
        st.metric("Tin có dấu hiệu trùng", sum(len(g) for g in detect_duplicate_properties(properties)), border=True)
    render_charts(properties)
    st.download_button("Xuất danh mục CSV", data=spreadsheet_safe_csv(pd.DataFrame(properties)),
                       file_name=f"homenest_{datetime.now(timezone(timedelta(hours=7))):%Y%m%d}.csv", mime="text/csv",
                       icon=icon("download"), type="primary")
    if properties:
        table(properties)


def render_settings(properties):
    heading("Cài đặt dữ liệu", "Kiểm tra tình trạng danh mục và đồng bộ các cập nhật mới nhất.", "settings")
    with st.container(border=True):
        st.subheader("Dữ liệu ứng dụng")
        st.write(f"Danh mục hiện có {len(properties)} tin đăng.")
        st.caption("Tải lại để nhận những thay đổi từ phiên làm việc khác.")
        st.button("Tải lại dữ liệu", icon=icon("sync"), on_click=refresh_properties, type="primary")
    with st.container(border=True):
        st.subheader("Tài khoản và phân quyền")
        st.write("Tài khoản email/mật khẩu có role User, Manager hoặc Admin. "
                 "Chỉ người vận hành máy chủ mới tạo Manager/Admin bằng create_account.py; "
                 "form đăng ký công khai luôn gán User.")
        st.button("Mở quản lý tài khoản User", icon=icon("manage_accounts"),
                  key="settings_accounts", on_click=navigate, args=("accounts",))
        try:
            roles = st.secrets.get("roles", {})
        except FileNotFoundError:
            roles = {}
        admin_url = roles.get("admin_console_url") if isinstance(roles, Mapping) else None
        if admin_url:
            parsed_url = urlsplit(str(admin_url))
            if parsed_url.scheme == "https" and parsed_url.netloc:
                st.link_button("Mở trang quản trị tài khoản OIDC", str(admin_url),
                               icon=icon("open_in_new"))
            else:
                st.error("roles.admin_console_url phải là URL HTTPS hợp lệ.")
        else:
            st.caption("Tạo tài khoản nội bộ có role Manager/Admin bằng create_account.py từ terminal máy chủ.")


@st.cache_data(ttl=15, show_spinner=False)
def cached_valuation_health():
    return valuation_health()


def establish_internal_session(account):
    username = account["email"]
    st.session_state.update(
        logged_in=True,
        _clear_auth_form=True,
        auth_mode="internal",
        auth_token=account["access_token"],
        auth_expires_at=account["expires_at"],
        username=username,
        display_name=account["full_name"],
        role=account["role"],
        view="overview",
        auth_screen="login",
        favorites=load_users().get(username, {}).get("favorites", []),
        selected_id=None,
        editor_revision=0,
    )
    destination = st.session_state.pop("_post_auth_view", None)
    if destination:
        if allowed_view(destination):
            st.session_state.view = destination
        else:
            st.session_state.flash_error = "Mục này không khả dụng với vai trò tài khoản của bạn."
    browser_auth.remember_login(account)
    write_route_url()
    st.rerun()


def render_user_registration():
    with st.form("register_form", border=False):
        full_name = st.text_input("Họ và tên", max_chars=120, key="register_full_name")
        email = st.text_input("Email", max_chars=254, key="register_email")
        password = st.text_input(
            "Mật khẩu", type="password", max_chars=128,
            help="Dùng tối thiểu 12 ký tự.", key="register_password")
        password_confirm = st.text_input(
            "Nhập lại mật khẩu", type="password", max_chars=128,
            key="register_password_confirm")
        submitted = st.form_submit_button(
            "Tạo tài khoản User", icon=icon("person_add"),
            type="primary", width="stretch", key="register_submit")
    if not submitted:
        return
    if password != password_confirm:
        st.error("Hai mật khẩu không trùng khớp.", icon=icon("error_outline"))
        return
    if len(password) < 12:
        st.error("Mật khẩu cần có ít nhất 12 ký tự.", icon=icon("password"))
        return
    try:
        with st.spinner("Đang tạo tài khoản an toàn…"):
            account = register_account(email.strip(), full_name.strip(), password)
    except ValuationAPIError as error:
        st.error(str(error), icon=icon("error_outline"))
        return
    if account.get("role") != "User":
        st.error("Máy chủ trả về role đăng ký không hợp lệ. Hãy liên hệ quản trị viên.")
        return
    establish_internal_session(account)


def render_internal_login():
    with st.form("account_login_form", border=False):
        email = st.text_input("Email", max_chars=254, key="login_email")
        password = st.text_input(
            "Mật khẩu", type="password", max_chars=128, key="login_password")
        submitted = st.form_submit_button(
            "Đăng nhập", icon=icon("login"), type="primary",
            width="stretch", key="account_login_submit")
    if not submitted:
        return
    try:
        with st.spinner("Đang xác thực tài khoản…"):
            account = login_account(email.strip(), password)
    except ValuationAPIError as error:
        st.error(str(error), icon=icon("error_outline"))
        return
    establish_internal_session(account)


def set_auth_screen(screen, destination_view=None):
    st.session_state.public_page = "auth"
    st.session_state.auth_screen = screen
    if destination_view:
        st.session_state._post_auth_view = destination_view
    else:
        st.session_state.pop("_post_auth_view", None)
    write_route_url()


def show_landing():
    st.session_state.public_page = "landing"
    st.session_state.auth_screen = "login"
    st.session_state.pop("_post_auth_view", None)
    clear_route_url()
    for key in ("login_password", "register_password", "register_password_confirm"):
        st.session_state.pop(key, None)


def render_login(properties):
    if notice := st.session_state.pop("auth_notice", None):
        st.warning(notice)
    is_register = st.session_state.get("auth_screen", "login") == "register"
    with st.container(key="hn-auth-shell"):
        with st.container(horizontal=True, vertical_alignment="center",
                          horizontal_alignment="distribute", key="hn-auth-topbar"):
            st.html('<div class="hn-brand-lockup"><span class="hn-brand-icon">H</span>'
                    '<span><strong>HomeNest</strong><small>KHÔNG GIAN BẤT ĐỘNG SẢN</small></span></div>')
            st.button("Về trang giới thiệu", icon=icon("arrow_back"),
                      key="back_to_landing", on_click=show_landing)
        with st.container(key="hn-auth-workspace-label"):
            st.caption("HOMENEST / KHÔNG GIAN THÀNH VIÊN")
            st.markdown(
                f"## {'Tạo tài khoản và bắt đầu hành trình' if is_register else 'Chào mừng bạn trở lại'}"
            )
            st.caption(
                "Tạo tài khoản User miễn phí để lưu tin, đăng bất động sản và khám phá HomeNest."
                if is_register else
                "Đăng nhập để tiếp tục khám phá bất động sản, tin đã lưu và các công cụ HomeNest."
            )
        left, right = st.columns([1.2, 0.88], gap="large", vertical_alignment="center")
        with left, st.container(key="hn-auth-showcase"):
            st.image(str(LOGIN_PHOTO), width="stretch")
            st.caption("TỔNG QUAN HOMENEST · KHÁM PHÁ VÀ KẾT NỐI")
            st.markdown("### Không gian sống phù hợp, bắt đầu từ lựa chọn đúng.")
            st.write(
                "Tìm kiếm tin đăng, tham khảo giá từ danh mục hiện có và quản lý những "
                "lựa chọn bạn quan tâm trong một không gian thống nhất."
            )
            with st.container(horizontal=True, key="hn-auth-gallery"):
                st.image(str(PROPERTY_ASSETS / "modern-home.jpg"),
                         caption="Kiến trúc hiện đại", width="stretch")
                st.image(str(PROPERTY_ASSETS / "garden-home.jpg"),
                         caption="Sống gần thiên nhiên", width="stretch")
            with st.container(horizontal=True, key="hn-auth-highlights"):
                st.badge(f"{len(properties)} tin trong danh mục", icon=icon("domain"), color="orange")
                st.badge("Tham khảo giá theo dữ liệu", icon=icon("query_stats"), color="gray")
        with right, st.container(border=True, key="hn-auth-card"):
            st.caption("CỔNG THÀNH VIÊN HOMENEST")
            st.subheader("Tạo tài khoản" if is_register else "Đăng nhập")
            st.caption("Đăng ký tài khoản User miễn phí." if is_register
                       else "Đăng nhập để tiếp tục khám phá và quản lý bất động sản.")
            if is_register:
                st.button("Quay lại đăng nhập", icon=icon("arrow_back"),
                          on_click=set_auth_screen, args=("login",),
                          key="back_to_login")
                st.caption("Mật khẩu cần dài ít nhất 12 ký tự.")
                render_user_registration()
            else:
                render_internal_login()
                with st.container(horizontal=True, key="hn-register-prompt"):
                    st.caption("Chưa có tài khoản?")
                    st.button("Tạo tài khoản", icon=icon("person_add"),
                              on_click=set_auth_screen, args=("register",),
                              key="show_register")

        if oidc_auth_configured():
            with right:
                st.divider()
                st.caption("Dành cho nhân viên và quản trị viên được cấp quyền.")
                if not oidc_id_token_exposed():
                    st.error("Cấu hình OIDC cần expose_tokens = [\"id\"] để gửi bearer token tới API.")
                elif st.button("Đăng nhập nhân viên / quản trị viên",
                               icon=icon("admin_panel_settings"), key="oidc_login"):
                    st.login()


def render_brandbar():
    role_labels = {"Admin": "Quản trị viên", "Manager": "Quản lý", "User": "Người dùng"}
    with st.container(key="hn-brandbar", horizontal=True,
                      vertical_alignment="center", horizontal_alignment="distribute"):
        brand_caption = "Không gian quản trị" if st.session_state.role == "Admin" else "Không gian bất động sản"
        st.html('<div class="hn-brand-lockup hn-brand-lockup-compact">'
                '<span class="hn-brand-icon">H</span>'
                f'<span><strong>HomeNest</strong><small>{brand_caption}</small></span></div>')
        with st.container(horizontal=True, vertical_alignment="center", key="hn-brandbar-user"):
            st.badge(role_labels.get(st.session_state.role, "Người dùng"),
                     icon=icon("verified_user"), color="green")
            st.caption(st.session_state.get("display_name", st.session_state.username))


def render_primary_nav():
    items = [
        ("admin_home", "Tổng quan", "overview", "dashboard"),
        ("admin_listings", "Tin đăng", "listings", "inventory_2"),
        ("admin_accounts", "Tài khoản", "accounts", "manage_accounts"),
        ("admin_reports", "Báo cáo", "reports", "assessment"),
        ("admin_messages", "Hỗ trợ", "messages", "support_agent"),
    ] if st.session_state.role == "Admin" else [
        ("home", "Trang chủ", "overview", "home"),
        ("location", "Vị trí", "explore", "location_on"),
        ("plan", "Mặt bằng", "market", "monitoring"),
        ("amenities", "Tiện ích", "services", "apartment"),
        ("design", "Thiết kế", "design", "chair"),
        ("forum", "Diễn đàn", "forum", "forum"),
        ("contact", "Liên hệ", "contact", "support_agent"),
    ]
    active_view = st.session_state.view
    if active_view == "detail":
        active_view = st.session_state.get("return_view", "overview")
    with st.container(horizontal=True, key="hn-primary-nav",
                      horizontal_alignment="distribute", vertical_alignment="center"):
        for key, label, view, symbol in items:
            st.button(
                label,
                icon=icon(symbol),
                key=f"primary_nav_{key}",
                width="stretch",
                type="primary" if active_view == view else "tertiary",
                help=f"Mở mục {label.lower()}",
                on_click=navigate,
                args=(view,),
            )


def render_market_overview(properties):
    heading("Mặt bằng giá", "Tham khảo giá chào bán theo khu vực từ các tin hiện có trong HomeNest.",
            "monitoring")
    sale = [
        prop for prop in properties
        if prop.get("status") == "Bán"
        and prop.get("price", 0) > 0
        and prop.get("area", 0) > 0
        and prop.get("location")
    ]
    rates = [prop["price"] / prop["area"] for prop in sale]
    locations = pd.DataFrame([
        {
            "Khu vực": prop["location"],
            "Số tin": 1,
            "Giá chào (₫)": prop["price"],
            "Đơn giá (triệu ₫/m²)": prop["price"] / prop["area"] / 1e6,
        }
        for prop in sale
    ])
    with st.container(horizontal=True, key="hn-market-kpis"):
        st.metric("Tin bán hợp lệ", len(sale), border=True)
        st.metric("Khu vực có dữ liệu", locations["Khu vực"].nunique() if not locations.empty else 0,
                  border=True)
        st.metric("Trung vị đơn giá", money(median(rates), True) if rates else "—", border=True)
    if locations.empty:
        st.info("Chưa có tin bán đủ giá và diện tích để tổng hợp.", icon=icon("query_stats"))
    else:
        summary = locations.groupby("Khu vực", as_index=False).agg(
            **{
                "Số tin": ("Khu vực", "size"),
                "Trung vị đơn giá (triệu ₫/m²)": ("Đơn giá (triệu ₫/m²)", "median"),
                "Giá chào thấp nhất (₫)": ("Giá chào (₫)", "min"),
                "Giá chào cao nhất (₫)": ("Giá chào (₫)", "max"),
            }
        ).sort_values("Trung vị đơn giá (triệu ₫/m²)", ascending=False)
        left, right = st.columns([1.15, 0.85])
        with left, st.container(border=True, key="hn-market-chart"):
            st.subheader("Đơn giá theo khu vực")
            st.caption("Trung vị giá chào bán trên mỗi m², triệu đồng.")
            chart_data = summary[["Khu vực", "Trung vị đơn giá (triệu ₫/m²)"]]
            st.bar_chart(chart_data, x="Khu vực", y="Trung vị đơn giá (triệu ₫/m²)",
                         horizontal=True, height=340)
        with right, st.container(border=True, key="hn-market-table"):
            st.subheader("So sánh nhanh")
            st.dataframe(
                summary,
                hide_index=True,
                width="stretch",
                column_config={
                    "Trung vị đơn giá (triệu ₫/m²)": st.column_config.NumberColumn(format="%.1f"),
                    "Giá chào thấp nhất (₫)": st.column_config.NumberColumn(format="%,.0f"),
                    "Giá chào cao nhất (₫)": st.column_config.NumberColumn(format="%,.0f"),
                },
            )
    st.caption("Đây là thống kê mô tả từ giá chào trong danh mục hiện tại, không phải giá giao dịch hoặc báo giá được thẩm định.")
    st.button("Tự định giá một bất động sản", icon=icon("query_stats"),
              type="primary", key="market_to_valuation",
              on_click=navigate, args=("valuation",))


def render_services():
    render_services_page(st.session_state.properties, navigate=navigate,
                         on_batch=open_services_batch)


def open_services_batch():
    if st.session_state.get("role") != "Manager":
        return
    st.session_state._open_batch_from_services = True
    navigate("valuation")


def open_property_category(category):
    st.session_state.search_type = category
    st.session_state.search_page = 1
    navigate("explore")


def render_design_catalog(properties):
    render_design_page(properties, property_types=PROPERTY_TYPES,
                       on_category=open_property_category, navigate=navigate)


FORUM_CATEGORIES = [
    "Bắt đầu", "Mua bán", "Cho thuê", "Định giá", "Pháp lý", "Khu vực & quy hoạch", "Khác",
]


def forum_post_images(post):
    images = [
        (path, False)
        for filename in post.get("images", [])
        if (path := image_path(filename)) is not None
    ]
    if images or not post.get("pinned"):
        return images
    illustration = FORUM_ILLUSTRATIONS.get(post.get("category"), "hero-residence.jpg")
    return [(PROPERTY_ASSETS / illustration, True)]


def open_forum_post(post_id):
    st.session_state.forum_selected_id = post_id
    write_route_url()


def toggle_forum_like(post_id):
    identity = current_identity()
    if not identity or identity["role"] == "Admin":
        return
    posts = load_forum_posts()
    post = next((item for item in posts if item.get("id") == post_id), None)
    if post is None:
        return
    likes = list(post.get("likes", []))
    username = st.session_state.username
    if username in likes:
        likes.remove(username)
    else:
        likes.append(username)
    post["likes"] = likes
    save_forum_posts(posts)


def render_forum_detail(post, posts):
    if st.button("Quay lại diễn đàn", icon=icon("arrow_back"), key="forum_back"):
        st.session_state.pop("forum_selected_id", None)
        st.rerun()

    with st.container(border=True, key="hn-forum-detail"):
        with st.container(horizontal=True, vertical_alignment="center",
                          key="hn-forum-detail-meta"):
            st.badge(post.get("category", "Khác"), icon=icon("forum"), color="green")
            if post.get("pinned"):
                st.badge("Chủ đề ghim", icon=icon("push_pin"), color="orange")
            st.caption(format_forum_time(post.get("created_at")))
        st.title(post.get("title", "Chủ đề"))
        st.caption(f"Đăng bởi {post.get('author', 'Thành viên')}")
        st.markdown(post.get("content", ""))
        images = forum_post_images(post)
        if images:
            st.subheader("Hình ảnh" if not images[0][1] else "Ảnh minh họa")
            columns = st.columns(min(len(images), 3))
            for column, (path, is_illustration) in zip(columns, images):
                with column:
                    st.image(
                        str(path),
                        caption="Ảnh minh họa HomeNest" if is_illustration else None,
                        width="stretch",
                    )
        with st.container(horizontal=True, vertical_alignment="center",
                          key="hn-forum-detail-actions"):
            username = st.session_state.username
            likes = post.get("likes", [])
            st.button(
                f"{'Bỏ thích' if username in likes else 'Thích'} · {len(likes)}",
                icon=icon("favorite" if username not in likes else "heart_check"),
                key=f"forum_detail_like_{post['id']}",
                on_click=toggle_forum_like,
                args=(post["id"],),
            )
            st.caption(f"{len(post.get('comments', []))} phản hồi")

    comments = post.setdefault("comments", [])
    st.subheader(f"Trao đổi ({len(comments)})")
    if comments:
        for comment in comments:
            with st.container(border=True, key=f"hn-forum-comment-{comment.get('id', uuid4().hex)}"):
                with st.container(horizontal=True, vertical_alignment="center",
                                  key=f"hn-forum-comment-meta-{comment.get('id', uuid4().hex)}"):
                    st.markdown(f"**{comment.get('author', 'Thành viên')}**")
                    st.caption(format_forum_time(comment.get("created_at")))
                st.write(comment.get("content", ""))
    else:
        st.info("Chưa có phản hồi. Bạn có thể là người đầu tiên chia sẻ góc nhìn.", icon=icon("forum"))

    with st.container(border=True, key="hn-forum-comment-compose"):
        st.subheader("Viết phản hồi")
        st.caption("Chia sẻ thông tin có ích, tránh đăng dữ liệu cá nhân.")
        with st.form(f"forum_comment_form_{post['id']}", clear_on_submit=True):
            comment_text = st.text_area(
                "Nội dung phản hồi", max_chars=1200,
                placeholder="Viết ý kiến hoặc kinh nghiệm của bạn...",
                key=f"forum_comment_text_{post['id']}"
            )
            submitted = st.form_submit_button(
                "Gửi phản hồi", icon=icon("send"), type="primary",
                key=f"forum_submit_comment_{post['id']}",
            )
    if submitted:
        if len(comment_text.strip()) < 3:
            st.error("Phản hồi cần có ít nhất 3 ký tự.")
        else:
            comments.append({
                "id": uuid4().hex,
                "author": st.session_state.get("display_name", st.session_state.username),
                "author_id": st.session_state.username,
                "content": comment_text.strip(),
                "created_at": datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="minutes"),
            })
            save_forum_posts(posts)
            st.rerun()


def render_forum_compose(posts):
    with st.container(border=True, key="hn-forum-compose"):
        st.subheader("Tạo chủ đề")
        st.caption("Mô tả rõ câu hỏi; có thể đính kèm ảnh để bổ sung bối cảnh.")
        post_images = st.file_uploader(
            "Ảnh đính kèm (không bắt buộc)",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            max_upload_size=5,
            help=f"Tối đa {MAX_FORUM_IMAGES} ảnh, mỗi ảnh không quá 5 MB. Ảnh sẽ được chuẩn hóa và gỡ metadata.",
            key="forum_new_images",
        )
        if post_images:
            preview_columns = st.columns(min(len(post_images), MAX_FORUM_IMAGES))
            for column, upload in zip(preview_columns, post_images[:MAX_FORUM_IMAGES]):
                with column:
                    st.image(upload, caption=upload.name, width="stretch")
            if len(post_images) > MAX_FORUM_IMAGES:
                st.warning(f"Mỗi chủ đề được đính kèm tối đa {MAX_FORUM_IMAGES} ảnh.")
        with st.form("forum_new_post_form", clear_on_submit=True):
            post_category = st.selectbox(
                "Danh mục", FORUM_CATEGORIES, key="forum_new_category"
            )
            post_title = st.text_input(
                "Tiêu đề", max_chars=120, placeholder="Ví dụ: Cần lưu ý gì khi thuê căn hộ?",
                key="forum_new_title"
            )
            post_content = st.text_area(
                "Nội dung", max_chars=4000, height=180,
                placeholder="Nêu bối cảnh, khu vực và điều bạn muốn hỏi...",
                key="forum_new_content"
            )
            st.caption("Tối thiểu 8 ký tự cho tiêu đề và 20 ký tự cho nội dung.")
            submitted = st.form_submit_button(
                "Đăng chủ đề", icon=icon("add_comment"),
                type="primary", width="stretch", key="forum_submit_post",
            )
        if submitted:
            if len(post_title.strip()) < 8:
                st.error("Tiêu đề cần có ít nhất 8 ký tự.")
            elif len(post_content.strip()) < 20:
                st.error("Nội dung cần có ít nhất 20 ký tự.")
            else:
                try:
                    image_data = prepare_forum_images(post_images or [])
                except ValueError as error:
                    st.error(str(error), icon=icon("error_outline"))
                    return
                saved_images = []
                written_images = []
                try:
                    for data in image_data:
                        filename = f"forum_{uuid4().hex}.jpg"
                        target = IMAGES_DIR / filename
                        written_images.append(target)
                        target.write_bytes(data)
                        saved_images.append(filename)
                    post = {
                            "id": uuid4().hex,
                            "category": post_category,
                            "title": post_title.strip(),
                            "content": post_content.strip(),
                            "author": st.session_state.get("display_name", st.session_state.username),
                            "author_id": st.session_state.username,
                            "created_at": datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="minutes"),
                            "images": saved_images,
                            "pinned": False,
                            "likes": [],
                            "comments": [],
                    }
                    posts.append(post)
                    save_forum_posts(posts)
                except (OSError, DataStoreError) as error:
                    for path in written_images:
                        path.unlink(missing_ok=True)
                    st.error(f"Chưa đăng được chủ đề. {error}", icon=icon("error_outline"))
                    return
                st.session_state.flash = "Đã đăng chủ đề. Cùng bắt đầu cuộc trò chuyện!"
                st.rerun()


def render_forum():
    posts = load_forum_posts()
    st.html("<style>" + (Path(__file__).parent / "assets/forum.css").read_text(encoding="utf-8") + "</style>")
    with st.container(key="hn-community"):
        render_forum_header(posts)
        selected_id = st.session_state.get("forum_selected_id")
        selected_post = next((post for post in posts if post.get("id") == selected_id), None)
        if selected_post:
            render_forum_detail(selected_post, posts)
            return
        st.session_state.pop("forum_selected_id", None)
        render_forum_home(
            posts, st.session_state.properties,
            categories=FORUM_CATEGORIES, post_images=forum_post_images,
            open_post=open_forum_post, like_post=toggle_forum_like,
            create_post=lambda: render_forum_compose(posts), navigate=navigate,
        )


def render_contact():
    if st.session_state.pop("_clear_support_form", False):
        st.session_state.support_subject = ""
        st.session_state.support_content = ""
    heading("Liên hệ HomeNest", "Gửi yêu cầu hỗ trợ; đội ngũ quản trị có thể tiếp nhận trong hộp thư hỗ trợ.",
            "contact_support")
    left, right = st.columns([0.8, 1.2])
    with left, st.container(border=True, key="hn-contact-info"):
        st.markdown("### Chúng tôi có thể hỗ trợ")
        st.write("Hướng dẫn sử dụng, hỗ trợ tin đăng, tài khoản và cách đọc kết quả tham khảo giá.")
        st.caption(f"Tài khoản gửi · {st.session_state.get('display_name', st.session_state.username)}")
        st.caption("Yêu cầu được lưu trong hộp thư nội bộ HomeNest.")
    with right, st.container(border=True, key="hn-contact-form-card"):
        st.subheader("Gửi yêu cầu")
        feedback = st.empty()
        with st.form("support_contact_form"):
            topic = st.selectbox(
                "Chủ đề",
                ["Hỗ trợ tài khoản", "Tin đăng bất động sản", "Kết quả định giá", "Góp ý khác"],
                key="support_topic",
            )
            subject = st.text_input("Tiêu đề", max_chars=120, key="support_subject")
            content = st.text_area("Nội dung", max_chars=2000, height=150, key="support_content")
            submitted = st.form_submit_button(
                "Gửi yêu cầu", icon=icon("send"), type="primary", width="stretch",
                key="support_submit",
            )
        if submitted:
            st.session_state.pop("support_request_sent", None)
            if not subject.strip() or not content.strip():
                feedback.error("Vui lòng nhập tiêu đề và nội dung yêu cầu.", icon=icon("error_outline"))
            else:
                try:
                    messages = load_messages()
                    messages.append({
                        "from": st.session_state.username,
                        "to": "support",
                        "subject": f"{topic} · {subject.strip()}",
                        "content": content.strip(),
                        "created_at": datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="minutes"),
                    })
                    save_messages(messages)
                except OSError:
                    feedback.error("Chưa gửi được yêu cầu. Vui lòng thử lại.", icon=icon("error_outline"))
                else:
                    st.session_state.support_request_sent = True
                    st.session_state._clear_support_form = True
                    st.rerun()
        if st.session_state.get("support_request_sent"):
            feedback.success(
                "Đã gửi yêu cầu thành công. Yêu cầu của bạn đã được chuyển đến hộp thư hỗ trợ HomeNest.",
                icon=icon("check_circle"),
            )


def render_sidebar():
    is_admin = st.session_state.role == "Admin"
    with st.sidebar:
        role_labels = {"Admin": "Quản trị viên", "Manager": "Quản lý", "User": "Người dùng"}
        role_icons = {"Admin": "admin_panel_settings", "Manager": "manage_accounts", "User": "person"}
        with st.container(key="hn-sidebar-brand"):
            st.html(
                '<div class="hn-brand-lockup hn-sidebar-lockup">'
                '<span class="hn-brand-icon">H</span>'
                '<span><strong>HomeNest</strong><small>KHÔNG GIAN BẤT ĐỘNG SẢN</small></span></div>'
            )
        with st.container(key="hn-sidebar-profile"):
            st.caption("KHÔNG GIAN QUẢN TRỊ" if is_admin else "KHÔNG GIAN THÀNH VIÊN")
            st.markdown(f"**{st.session_state.get('display_name', st.session_state.username)}**")
            st.badge(role_labels.get(st.session_state.role, "Người dùng"),
                     icon=icon(role_icons.get(st.session_state.role, "person")), color="orange")
        groups = ({"QUẢN TRỊ": [("overview", "Tổng quan quản trị", "admin_panel_settings"),
                                ("accounts", "Quản lý tài khoản User", "manage_accounts")],
                  "NỘI DUNG": [("listings", "Quản lý tin đăng", "inventory_2"), ("duplicates", "Kiểm tra tin trùng", "difference"),
                                 ("reports", "Báo cáo & xuất dữ liệu", "assessment"),
                                 ("messages", "Hộp thư hỗ trợ", "inbox")],
                  "HỆ THỐNG": [("settings", "Cài đặt dữ liệu", "settings")]}
                  if is_admin else {
                      "PHÂN TÍCH": [("overview", "Tổng quan", "space_dashboard"), ("valuation", "Phân tích & định giá", "query_stats")],
                      "DANH MỤC": [("explore", "Khám phá bất động sản", "travel_explore"), ("mine", "Bài đăng của tôi", "inventory_2"),
                                   ("favorites", "Tin đã lưu", "bookmarks"), ("messages", "Hộp thư", "inbox")],
                      "TÀI KHOẢN": [("profile", "Hồ sơ cá nhân", "account_circle")]})
        group_keys = {"QUẢN TRỊ": "admin", "NỘI DUNG": "content", "HỆ THỐNG": "system",
                      "PHÂN TÍCH": "analytics", "DANH MỤC": "catalog", "TÀI KHOẢN": "account"}
        for group, entries in groups.items():
            with st.container(key=f"hn-sidebar-group-{group_keys[group]}"):
                st.caption(group)
                for view, label, symbol in entries:
                    st.button(label, icon=icon(symbol), key=f"nav_{view}", width="stretch",
                              type="primary" if st.session_state.view == view else "tertiary",
                              on_click=navigate, args=(view,))
        with st.container(key="hn-sidebar-publish"):
            st.button("Đăng tin mới", icon=icon("add_home"), width="stretch",
                      on_click=navigate, args=("editor",), key="nav_editor")
        with st.container(key="hn-sidebar-system"):
            st.caption("TRẠNG THÁI HỆ THỐNG")
        try:
            status = cached_valuation_health()
            api_online = status.get("api_ready") is True
            estimator_ready = status.get("estimator_ready") is True
            with st.container(key="hn-sidebar-health"):
                st.badge("API đang hoạt động" if api_online else "API chưa sẵn sàng",
                         icon=icon("cloud_done" if api_online else "cloud_off"),
                         color="green" if api_online else "red")
                if not is_admin:
                    st.caption("Bộ ước tính sẵn sàng" if estimator_ready else "Bộ ước tính chưa sẵn sàng")
                st.caption(f"Phản hồi API · {status['_client_response_time_ms']} ms")
                if not is_admin:
                    st.caption(f"Tin so sánh hợp lệ · {status.get('comparable_listing_count', 0)}")
                if not status.get("catalog_ready"):
                    st.caption("Danh mục chưa có dữ liệu.")
        except ValuationAPIError:
            with st.container(key="hn-sidebar-health"):
                st.badge("Không kết nối được API", icon=icon("cloud_off"), color="red")
                st.caption("Hãy kiểm tra backend và thử lại.")
        with st.container(key="hn-sidebar-logout"):
            st.button("Đăng xuất", icon=icon("logout"), on_click=logout,
                      width="stretch", key="logout")


if st.session_state.pop("_clear_auth_form", False):
    for key in ("login_password", "register_password", "register_password_confirm"):
        st.session_state.pop(key, None)

browser_auth.restore_login()
identity = current_identity()
if identity is None and st.session_state.get("auth_notice") and browser_auth.enabled():
    browser_auth.queue_cookie_clear()
if identity:
    if st.session_state.get("auth_mode") == "internal":
        st.session_state.update(logged_in=True, username=identity["username"],
                                display_name=identity["display_name"], role=identity["role"])
        st.session_state.setdefault("logged_in", True)
        st.session_state.setdefault("view", "overview")
        st.session_state.setdefault("favorites", load_users().get(identity["username"], {}).get("favorites", []))
        st.session_state.setdefault("selected_id", None)
        st.session_state.setdefault("editor_revision", 0)
    elif st.session_state.get("oidc_subject") != identity["subject"]:
        destination = st.session_state.get("_post_auth_view")
        for key in list(st.session_state):
            del st.session_state[key]
        st.session_state.update(
            logged_in=True, auth_mode="oidc", oidc_subject=identity["subject"],
            username=identity["username"], display_name=identity["display_name"],
            role=identity["role"], view="overview",
            favorites=load_users().get(identity["username"], {}).get("favorites", []),
            selected_id=None, editor_revision=0)
        if destination:
            if allowed_view(destination):
                st.session_state.view = destination
            else:
                st.session_state.flash_error = "Mục này không khả dụng với vai trò tài khoản của bạn."
    else:
        st.session_state.update(
            logged_in=True, auth_mode="oidc", username=identity["username"],
            display_name=identity["display_name"], role=identity["role"])
else:
    if st.session_state.get("auth_mode") in {"oidc", "demo"}:
        for key in list(st.session_state):
            del st.session_state[key]
    for name, value in {
        "logged_in": False,
        "view": "overview",
        "favorites": [],
        "selected_id": None,
        "editor_revision": 0,
        "auth_screen": "login",
        "public_page": "landing",
    }.items():
        st.session_state.setdefault(name, value)
if "properties" not in st.session_state:
    st.session_state.properties = load_properties()
properties = st.session_state.properties
restore_route_url()
browser_auth.sync_cookie()

if not st.session_state.logged_in:
    if st.session_state.public_page == "auth":
        render_login(properties)
    else:
        render_landing(properties, on_login=set_auth_screen)
else:
    render_brandbar()
    render_primary_nav()
    render_sidebar()
    if flash := st.session_state.pop("flash", None):
        st.success(flash, icon=icon("check_circle"))
    if flash := st.session_state.pop("flash_error", None):
        st.error(flash, icon=icon("error_outline"))
    view = st.session_state.view
    if not allowed_view(view):
        st.error("Bạn không có quyền truy cập mục này.")
    elif view == "overview":
        if st.session_state.role == "Admin":
            render_admin_overview(properties)
        else:
            render_overview(properties)
    elif view == "accounts":
        render_accounts(on_open_listing=open_managed_listing)
    elif view == "valuation":
        render_valuation(properties)
    elif view == "explore":
        render_explore(properties)
    elif view == "market":
        render_market_overview(properties)
    elif view == "services":
        render_services()
    elif view == "design":
        render_design_catalog(properties)
    elif view == "forum":
        render_forum()
    elif view == "contact":
        render_contact()
    elif view == "detail":
        render_detail(properties)
    elif view in {"listings", "mine"}:
        render_listing_management(properties, mine=view == "mine")
    elif view == "editor":
        render_editor(properties)
    elif view == "favorites":
        render_favorites(properties)
    elif view == "messages":
        render_messages()
    elif view == "profile":
        render_profile()
    elif view == "duplicates":
        render_duplicates(properties)
    elif view == "reports":
        render_reports(properties)
    elif view == "settings":
        render_settings(properties)
    if st.session_state.get("pending_delete") is not None:
        confirm_delete(st.session_state.pending_delete)

write_route_url()
