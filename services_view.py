"""Member tool hub, using the existing HomeNest destinations and account data."""

from html import escape
from pathlib import Path
import unicodedata

import streamlit as st

from landing import line_icon, photo_uri


def _render_summary(properties):
    favorites = st.session_state.get("favorites", [])
    username = st.session_state.get("username")
    saved = sum(prop.get("id") in favorites for prop in properties)
    owned = sum(prop.get("user_id") == username for prop in properties)
    items = (
        ("search", len(properties), "Tin hiện có", "Các lựa chọn bán và cho thuê"),
        ("bookmark", saved, "Tin đã lưu", "Xem lại những lựa chọn bạn quan tâm"),
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
            st.html('<span class="hn-tools-eyebrow">CÔNG CỤ CHO HÀNH TRÌNH CỦA BẠN</span>')
            st.title("Tiện ích bất động sản")
            st.write("Từ tìm một nơi để ở đến quản lý tài sản của bạn. "
                     "Chọn công cụ phù hợp và bắt đầu ngay tại đây.")
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
                f'<img src="{photo_uri("garden-home.jpg")}" alt="Không gian phòng khách hiện đại với ánh sáng tự nhiên; ảnh minh họa HomeNest">'
                '<span class="hn-tools-photo-label">Ảnh minh họa HomeNest</span>'
                '<div class="hn-tools-visual-caption"><small>KHÔNG GIAN SỐNG BẠN MONG MUỐN</small>'
                '<h2>Thêm cơ sở.<br>Rõ lựa chọn.</h2>'
                '<p>Tìm hiểu kỹ hơn trước mỗi quyết định.</p></div></div>'
            )


def _tool_cards(saved, owned):
    return [
        {
            "slug": "explore", "icon": "search", "label": "Khám phá", "group": "buy",
            "title": "Tìm nhà & đất",
            "description": "Tìm tin bán hoặc cho thuê theo khu vực, loại hình và ngân sách của bạn.",
            "features": ["Bộ lọc mua và thuê riêng biệt", "Xem chi tiết và liên hệ người đăng"],
            "keywords": "khám phá tìm kiếm mua nhà thuê nhà căn hộ đất nền",
            "action": "Tìm bất động sản", "key": "services_explore", "view": "explore",
        },
        {
            "slug": "valuation", "icon": "chart", "label": "Phân tích", "group": "buy",
            "title": "Phân tích & định giá",
            "description": "Đối chiếu giá chào bán và xem vị trí bất động sản trên bản đồ.",
            "features": ["Các tin tương đồng trong cùng khu vực", "Tải báo cáo và mở vị trí trên Google Maps"],
            "keywords": "phân tích định giá bản đồ vị trí tọa độ báo cáo",
            "action": "Mở công cụ định giá", "key": "services_valuation", "view": "valuation",
        },
        {
            "slug": "create", "icon": "home", "label": "Đăng tin", "group": "sell",
            "title": "Đăng tin mới",
            "description": "Giới thiệu tài sản với thông tin rõ ràng và hình ảnh do bạn cung cấp.",
            "features": ["Đăng tin bán hoặc cho thuê", "Đính kèm tối đa 5 ảnh thực tế"],
            "action": "Đăng tin mới", "key": "services_create", "view": "editor",
        },
        {
            "slug": "mine", "icon": "dashboard", "label": "Quản lý", "group": "sell",
            "title": "Quản lý tin đăng",
            "description": "Xem lại, chỉnh sửa và cập nhật những bất động sản bạn đã đăng.",
            "features": [f"{owned} bài đăng của bạn trong danh mục", "Chỉnh sửa hoặc xóa tin của bạn"],
            "action": "Quản lý bài đăng", "key": "services_mine", "view": "mine",
        },
        {
            "slug": "favorites", "icon": "bookmark", "label": "Cá nhân", "group": "buy",
            "title": "Tin đã lưu",
            "description": "Trở lại những lựa chọn bạn quan tâm để xem kỹ và liên hệ khi sẵn sàng.",
            "features": [f"{saved} tin đã lưu còn trong danh mục", "Thêm hoặc bỏ lưu khi nhu cầu thay đổi"],
            "keywords": "yêu thích lưu bookmark danh sách cá nhân",
            "action": "Xem tin đã lưu", "key": "services_favorites", "view": "favorites",
        },
        {
            "slug": "market", "icon": "chart", "label": "Thị trường", "group": "buy",
            "title": "Mặt bằng giá khu vực",
            "description": "So sánh đơn giá chào bán giữa các khu vực có tin trong HomeNest.",
            "features": ["Trung vị giá chào bán trên mỗi m²", "So sánh các khu vực có tin trong HomeNest"],
            "action": "Khám phá mặt bằng giá", "key": "services_market", "view": "market",
        },
    ]


def _search_text(value):
    normalized = unicodedata.normalize('NFKD', str(value).casefold().replace('đ', 'd'))
    return ' '.join(''.join(char for char in normalized if not unicodedata.combining(char)).split())


def _filtered_tools(cards, goal, query):
    phrase = _search_text(query)
    tokens = phrase.split()
    group = {'Tìm & chọn nhà': 'buy', 'Đăng & quản lý': 'sell'}.get(goal)
    indexed = [(card, _search_text(' '.join((
        card['title'], card['description'], card['action'], card['label'],
        card.get('keywords', ''), *card['features'])))) for card in cards]
    # Prefer a named feature such as "bản đồ" over unrelated occurrences of
    # "bán" and "động" elsewhere in a real-estate description.
    matches = [card for card, text in indexed if phrase in text]
    if not matches:
        matches = [card for card, text in indexed if all(token in text for token in tokens)]
    return [card for card in matches if group is None or card['group'] == group]


def _reset_tool_filters():
    st.session_state.services_search = ''
    st.session_state.services_goal = 'Tất cả'


def _render_featured_tool(navigate):
    with st.container(border=True, key='hn-tools-featured'):
        copy, action = st.columns([1.7, 1], gap='medium', vertical_alignment='center')
        with copy:
            st.html('<span class="hn-tools-eyebrow">GỢI Ý KHÁM PHÁ</span>')
            st.subheader('Xem mức giá. Hiểu vị trí.', anchor=False)
            st.write('Phân tích tin chào bán, xem bản đồ và tải báo cáo trong cùng một công cụ.')
            st.html('<div class="hn-tools-featured-points"><span>Giá tham khảo</span>'
                    '<span>Bản đồ vị trí</span><span>Báo cáo tải về</span></div>')
        with action:
            st.button('Phân tích một bất động sản', key='services_featured_valuation',
                      icon=':material/near_me:', type='primary', width='stretch',
                      on_click=navigate, args=('valuation',))
            st.caption('Dựa trên các tin chào bán cùng khu vực và loại hình trong HomeNest.')


def _render_tool_card(card, navigate):
    with st.container(border=True, key=f"hn-tools-card-{card['slug']}"):
        st.html('<div class="hn-tools-card-top">'
                f'<span class="hn-tools-tool-icon">{line_icon(card["icon"])}</span>'
                f'<span class="hn-tools-card-label">{card["label"]}</span></div>')
        st.subheader(card["title"], anchor=False)
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
        all_cards = _tool_cards(saved, owned)
        st.session_state.setdefault('services_search', '')
        st.session_state.setdefault('services_goal', 'Tất cả')
        heading = st.empty()
        with st.container(key='hn-tools-filters'):
            query = st.text_input('Tìm tiện ích', value=None, placeholder='Ví dụ: định giá, bản đồ, tin đã lưu…',
                                  key='services_search', max_chars=100, icon=':material/search:',
                                  persist_state='session')
            goal = st.segmented_control(
                "Nhu cầu của bạn", ["Tất cả", "Tìm & chọn nhà", "Đăng & quản lý"],
                selection_mode="single", label_visibility="collapsed",
                key="services_goal", persist_state="session", width="stretch",
            )
        cards = _filtered_tools(all_cards, goal, query or '')
        heading.html('<div class="hn-tools-section-title" id="cong-cu-homenest"><div>'
                     '<span class="hn-tools-eyebrow">BỘ CÔNG CỤ CỦA BẠN</span>'
                     '<h2>Bạn muốn bắt đầu từ đâu?</h2>'
                     '<p>Tìm nhanh một công cụ hoặc chọn theo nhu cầu của bạn.</p></div>'
                     f'<span class="hn-tools-section-number">{len(cards):02d} TIỆN ÍCH</span></div>')
        with st.container(horizontal=True, horizontal_alignment='distribute', key='hn-tools-filter-status'):
            st.html(f'<p class="hn-tools-results" role="status" aria-live="polite">'
                    f'Đang hiển thị <strong>{len(cards)} / {len(all_cards)}</strong> tiện ích</p>', width='content')
            if query or goal not in (None, 'Tất cả'):
                st.button('Xóa bộ lọc', key='services_search_clear', icon=':material/filter_alt_off:',
                          type='tertiary', on_click=_reset_tool_filters)
        with st.container(key="hn-tools-grid"):
            for card in cards:
                _render_tool_card(card, navigate)
        if not cards:
            with st.container(border=True, key='hn-tools-empty'):
                st.subheader('Chưa tìm thấy tiện ích phù hợp', anchor=False)
                st.write('Thử từ khóa ngắn hơn hoặc chọn “Tất cả” để xem thêm công cụ. '
                         'Bạn cũng có thể dùng nút “Xóa bộ lọc” để bắt đầu lại.')
        _render_featured_tool(navigate)
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
