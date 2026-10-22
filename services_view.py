"""Member tool hub, using the existing HomeNest destinations and account data."""

from html import escape
from pathlib import Path

import streamlit as st

from landing import line_icon, photo_uri


def _render_summary(properties):
    favorites = st.session_state.get("favorites", [])
    username = st.session_state.get("username")
    saved = sum(prop.get("id") in favorites for prop in properties)
    owned = sum(prop.get("user_id") == username for prop in properties)
    items = (
        ("search", len(properties), "Tin trong danh mục", "Khám phá lựa chọn bán và cho thuê"),
        ("bookmark", saved, "Tin bạn đã lưu", "Trở lại những lựa chọn đang quan tâm"),
        ("edit", owned, "Bài đăng của bạn", "Chủ động quản lý thông tin đã đăng"),
    )
    st.html('<div class="hn-tools-summary">' + "".join(
        '<div class="hn-tools-summary-item">'
        f'<span class="hn-tools-stat-icon">{line_icon(symbol)}</span>'
        f'<div><strong>{count:02d}</strong><span>{label}</span>'
        f'<small>{description}</small></div></div>'
        for symbol, count, label, description in items
    ) + '</div>')
    return saved, owned


def _render_hero(navigate):
    with st.container(key="hn-tools-hero"):
        copy, visual = st.columns([1.18, 1], gap="large", vertical_alignment="center")
        with copy, st.container(key="hn-tools-hero-copy"):
            st.html('<span class="hn-tools-eyebrow">TIỆN ÍCH HOMENEST</span>')
            st.title("Tiện ích cho mọi bước đi của bạn.")
            st.write("Tìm nhà, tham khảo giá và quản lý tin đăng trong một không gian "
                     "được thiết kế cho hành trình bất động sản của bạn.")
            with st.container(horizontal=True, key="hn-tools-hero-actions"):
                st.button("Khám phá bất động sản", icon=":material/arrow_forward:",
                          type="primary", key="services_hero_explore",
                          on_click=navigate, args=("explore",))
                st.button("Tham khảo giá", icon=":material/query_stats:",
                          key="services_hero_valuation", on_click=navigate, args=("valuation",))
            st.html('<div class="hn-tools-assurance">' + "".join(
                f'<span>{line_icon("check")}{text}</span>'
                for text in ("Tìm kiếm linh hoạt", "Giá có đối chiếu", "Quản lý chủ động")
            ) + '</div>')
        with visual, st.container(key="hn-tools-hero-visual"):
            st.html(
                '<div class="hn-tools-visual">'
                f'<img src="{photo_uri("hero-residence.jpg")}" alt="Không gian sống hiện đại với ánh sáng tự nhiên; ảnh minh họa HomeNest">'
                '<span class="hn-tools-photo-label">Ảnh minh họa HomeNest</span>'
                '<div class="hn-tools-visual-caption"><small>CHO HÀNH TRÌNH TÌM TỔ ẤM</small>'
                '<h2>Đúng công cụ.<br>Rõ lựa chọn.</h2>'
                '<p>Đồng hành từ nhu cầu đến quyết định.</p></div></div>'
            )


def _tool_cards(saved, owned):
    return [
        {
            "slug": "explore", "icon": "search", "label": "Khám phá", "group": "buy",
            "title": "Tìm bất động sản phù hợp",
            "description": "Thu hẹp lựa chọn từ điều bạn cần, để mỗi lần tìm kiếm đều có định hướng.",
            "features": ["Lọc theo khu vực, loại hình và ngân sách", "Tách riêng nhu cầu mua và thuê"],
            "action": "Tìm bất động sản", "key": "services_explore", "view": "explore",
        },
        {
            "slug": "valuation", "icon": "chart", "label": "Phân tích", "group": "buy",
            "title": "Hiểu giá trước khi lựa chọn",
            "description": "Tham khảo mức giá chào bán từ các tin cùng khu vực và loại bất động sản.",
            "features": ["Xem các tin được dùng để đối chiếu", "Tải báo cáo giá tham khảo"],
            "action": "Mở công cụ định giá", "key": "services_valuation", "view": "valuation",
        },
        {
            "slug": "create", "icon": "home", "label": "Đăng tin", "group": "sell",
            "title": "Giới thiệu bất động sản của bạn",
            "description": "Trình bày rõ thông tin, hình ảnh và nhu cầu để người tìm dễ tiếp cận tin đăng.",
            "features": ["Đăng tin bán hoặc cho thuê", "Đính kèm tối đa 5 ảnh thực tế"],
            "action": "Đăng tin mới", "key": "services_create", "view": "editor",
        },
        {
            "slug": "mine", "icon": "dashboard", "label": "Quản lý", "group": "sell",
            "title": "Chăm sóc từng tin đăng",
            "description": "Tập trung các bài đăng của bạn tại một nơi để theo dõi và cập nhật khi cần.",
            "features": [f"{owned} bài đăng của bạn trong danh mục", "Chỉnh sửa thông tin và xóa tin của bạn"],
            "action": "Quản lý bài đăng", "key": "services_mine", "view": "mine",
        },
        {
            "slug": "favorites", "icon": "bookmark", "label": "Cá nhân", "group": "buy",
            "title": "Giữ lại những lựa chọn ưng ý",
            "description": "Gom các tin bạn quan tâm để thuận tiện xem lại và liên hệ người đăng.",
            "features": [f"{saved} tin đã lưu còn trong danh mục", "Thêm hoặc bỏ lưu khi nhu cầu thay đổi"],
            "action": "Xem tin đã lưu", "key": "services_favorites", "view": "favorites",
        },
        {
            "slug": "market", "icon": "chart", "label": "Thị trường", "group": "buy",
            "title": "Nhìn rõ mặt bằng giá khu vực",
            "description": "Đặt các khu vực cạnh nhau để có thêm góc nhìn trước khi tìm hiểu sâu hơn.",
            "features": ["Trung vị giá chào bán trên mỗi m²", "So sánh các khu vực có tin trong HomeNest"],
            "action": "Khám phá mặt bằng giá", "key": "services_market", "view": "market",
        },
    ]


def _render_tool_card(card, navigate):
    with st.container(border=True, key=f"hn-tools-card-{card['slug']}"):
        st.html('<div class="hn-tools-card-top">'
                f'<span class="hn-tools-tool-icon">{line_icon(card["icon"])}</span>'
                f'<span class="hn-tools-card-label">{card["label"]}</span></div>')
        st.subheader(card["title"])
        st.write(card["description"])
        st.html('<ul class="hn-tools-features">' + "".join(
            f'<li>{escape(feature)}</li>' for feature in card["features"]
        ) + '</ul>')
        st.button(card["action"], icon=":material/arrow_forward:",
                  width="stretch", key=card["key"], on_click=navigate, args=(card["view"],))


def _render_next_steps(navigate):
    with st.container(key="hn-tools-next"):
        journey, support = st.columns([1.3, 1], gap="large")
        with journey, st.container(key="hn-tools-journey"):
            st.html('<span class="hn-tools-eyebrow">BẮT ĐẦU THẬT ĐƠN GIẢN</span>')
            st.subheader("Một hành trình rõ ràng hơn")
            steps = (
                ("Xác định điều bạn cần", "Chọn khu vực, loại bất động sản và khoảng ngân sách phù hợp."),
                ("Đối chiếu trước khi chọn", "Lưu tin bạn quan tâm và tham khảo mức giá trong cùng khu vực."),
                ("Chủ động bước tiếp theo", "Liên hệ người đăng hoặc tạo và quản lý tin của riêng bạn."),
            )
            st.html('<ol class="hn-tools-steps">' + "".join(
                f'<li><span>{index:02d}</span><div><strong>{title}</strong><p>{description}</p></div></li>'
                for index, (title, description) in enumerate(steps, 1)
            ) + '</ol>')
        with support, st.container(key="hn-tools-support"):
            st.html(f'<span class="hn-tools-support-icon">{line_icon("forum")}</span>')
            st.subheader("Bạn cần thêm hướng dẫn?")
            st.write("Gửi câu hỏi cho HomeNest hoặc cùng cộng đồng chia sẻ những kinh nghiệm thực tế.")
            st.button("Liên hệ HomeNest", icon=":material/arrow_forward:", width="stretch",
                      type="primary", key="services_contact", on_click=navigate, args=("contact",))
            st.button("Ghé thăm diễn đàn", icon=":material/forum:", width="stretch",
                      type="tertiary", key="services_forum", on_click=navigate, args=("forum",))


def render_services_page(properties, *, navigate, on_batch):
    st.html("<style>" + (Path(__file__).parent / "assets/services.css").read_text(encoding="utf-8") + "</style>")
    with st.container(key="hn-tools"):
        with st.container(horizontal=True, horizontal_alignment="distribute", key="hn-tools-intro"):
            st.html('<div class="hn-tools-breadcrumb">HOMENEST <span>/</span> TIỆN ÍCH</div>', width="content")
            member_space = "Không gian quản lý" if st.session_state.role == "Manager" else "Không gian thành viên"
            st.html(f'<span class="hn-tools-access">{member_space}</span>', width="content")
        _render_hero(navigate)
        saved, owned = _render_summary(properties)
        st.html('<div class="hn-tools-section-title" id="cong-cu-homenest"><div>'
                '<span class="hn-tools-eyebrow">BỘ CÔNG CỤ CỦA BẠN</span>'
                '<h2>Bạn muốn bắt đầu từ đâu?</h2>'
                '<p>Chọn tiện ích phù hợp với nhu cầu hiện tại.</p></div>'
                '<span class="hn-tools-section-number">06 TIỆN ÍCH</span></div>')
        goal = st.segmented_control(
            "Nhu cầu của bạn", ["Tất cả", "Tìm & chọn nhà", "Đăng & quản lý"],
            default="Tất cả", selection_mode="single", label_visibility="collapsed",
            key="services_goal", persist_state="session", width="stretch",
        )
        cards = [card for card in _tool_cards(saved, owned)
                 if goal in (None, "Tất cả") or card["group"] == (
                     "buy" if goal == "Tìm & chọn nhà" else "sell")]
        with st.container(key="hn-tools-grid"):
            for start in range(0, len(cards), 2):
                with st.container(key=f"hn-tools-row-{start // 2}"):
                    columns = st.columns(2, gap="medium")
                    for column, card in zip(columns, cards[start:start + 2]):
                        with column:
                            _render_tool_card(card, navigate)
        if st.session_state.role == "Manager":
            with st.container(key="hn-tools-manager"):
                st.html('<div class="hn-tools-manager-copy"><span class="hn-tools-eyebrow">DÀNH CHO QUẢN LÝ</span>'
                        '<h3>Nhiều tài sản. Một lượt phân tích.</h3>'
                        '<p>Tải danh sách CSV để tham khảo giá cho tối đa 500 bất động sản, '
                        'với kết quả riêng cho từng dòng.</p></div>')
                st.button("Mở định giá hàng loạt", icon=":material/table_view:",
                          key="services_batch", on_click=on_batch)
        _render_next_steps(navigate)
        st.html('<p class="hn-tools-footer-note">HomeNest · Đồng hành cùng lựa chọn của bạn.'
                '<span>Giá tham khảo được tổng hợp từ tin chào bán trong danh mục HomeNest.</span></p>')
