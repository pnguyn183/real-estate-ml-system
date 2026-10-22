"""An illustrated space and property-type collection for HomeNest members."""

from base64 import b64encode
from html import escape
from pathlib import Path

import streamlit as st

from landing import photo_uri


_STORIES = {
    "Biệt thự": {
        "image": "hero-residence.jpg",
        "description": "Cảm hứng từ những khoảng mở, sự kết nối với thiên nhiên và không gian dành riêng cho gia đình.",
        "tags": ("Khoảng mở", "Kết nối trong & ngoài"),
    },
    "Căn hộ": {
        "image": "interior-living.jpg",
        "description": "Gợi ý một nhịp sống gọn gàng với ánh sáng, cách bố trí linh hoạt và những điểm nhấn vừa đủ.",
        "tags": ("Bố trí linh hoạt", "Sắc độ hài hòa"),
    },
    "Nhà phố": {
        "image": "city-residences.jpg",
        "description": "Tìm cảm hứng cho không gian đô thị: mặt đứng có cá tính, công năng rõ ràng và những khoảng thở bên trong.",
        "tags": ("Nhịp sống đô thị", "Dấu ấn kiến trúc"),
    },
    "Đất nền": {
        "image": None,
        "description": "Bắt đầu từ vị trí và không gian xung quanh để hình dung nơi bạn muốn tạo dựng tổ ấm.",
        "tags": ("Vị trí & cảnh quan", "Khởi đầu ý tưởng"),
    },
    "Khác": {
        "image": "garden-home.jpg",
        "description": "Mở rộng góc nhìn với những không gian và loại hình khác đang có trong danh mục HomeNest.",
        "tags": ("Góc nhìn mới", "Cảm hứng riêng"),
    },
}


@st.cache_data(max_entries=1, show_spinner=False)
def _landscape_uri():
    illustration = Path(__file__).parent / "assets/properties/landscape-concept.svg"
    return "data:image/svg+xml;base64," + b64encode(illustration.read_bytes()).decode("ascii")


def _render_hero(navigate):
    st.html('<style>.st-key-hn-studio-hero { --studio-hero-image: url("'
            + photo_uri("modern-home.jpg") + '"); }</style>')
    with st.container(key="hn-studio-hero"):
        with st.container(key="hn-studio-hero-copy"):
            st.html('<span class="hn-studio-eyebrow">THIẾT KẾ & KHÔNG GIAN SỐNG</span>')
            st.title("Không gian đẹp, cảm hứng mỗi ngày.")
            st.write("Khám phá những gợi ý về không gian sống, tìm loại hình phù hợp "
                     "và bắt đầu hình dung tổ ấm của riêng bạn.")
            with st.container(horizontal=True, vertical_alignment="center", key="hn-studio-hero-actions"):
                st.button("Tìm không gian phù hợp", icon=":material/arrow_forward:",
                          type="primary", key="design_hero_explore", on_click=navigate, args=("explore",))
                st.html('<a class="hn-studio-anchor" href="#bo-suu-tap">Khám phá bộ sưu tập <span aria-hidden="true">↓</span></a>',
                        width="content")
        st.html('<div class="hn-studio-hero-caption">Ảnh minh họa HomeNest'
                '<span>Kiến trúc hiện đại · Khoảng xanh kết nối</span></div>')


def _render_intro_strip():
    steps = (
        ("Khám phá loại hình", "Từ căn hộ đến những ngôi nhà có khoảng xanh."),
        ("Tìm dấu ấn riêng", "Gợi ý về ánh sáng, vật liệu và cách sống."),
        ("Kết nối với lựa chọn", "Mở các tin phù hợp trong danh mục HomeNest."),
    )
    st.html('<div class="hn-studio-intro-strip">' + "".join(
        f'<div class="hn-studio-intro-step"><span>{index:02d}</span>'
        f'<div><strong>{title}</strong><p>{description}</p></div></div>'
        for index, (title, description) in enumerate(steps, 1)
    ) + '</div>')


def _cover(kind, story):
    is_landscape = story["image"] is None
    image = _landscape_uri() if is_landscape else photo_uri(story["image"])
    label = "Minh họa cảnh quan" if is_landscape else "Ảnh minh họa"
    st.html('<div class="hn-studio-cover">'
            f'<img src="{image}" alt="{escape(label)} gợi cảm hứng cho {escape(kind)}" loading="lazy">'
            f'<span class="hn-studio-cover-label">{label}</span></div>')


def _kind_content(index, kind, story, records, on_category):
    st.subheader(kind)
    sale = sum(prop.get("status") == "Bán" for prop in records)
    rent = sum(prop.get("status") == "Cho thuê" for prop in records)
    st.html('<div class="hn-studio-card-meta">'
            f'<span>{len(records)} tin trong danh mục</span>'
            f'<span>{sale} tin bán · {rent} tin cho thuê</span></div>')
    st.write(story["description"])
    st.html('<div class="hn-studio-tags">' + "".join(
        f'<span>{escape(tag)}</span>' for tag in story["tags"]
    ) + '</div>')
    if not records:
        st.caption("Chưa có tin thuộc loại hình này trong danh mục.")
    st.button("Xem tin phù hợp", icon=":material/arrow_forward:", width="stretch",
              key=f"design_category_{index}", on_click=on_category, args=(kind,))


def _render_kind(index, kind, properties, on_category, *, wide=False):
    story = _STORIES.get(kind, {
        "image": "interior-living.jpg",
        "description": "Khám phá thêm một loại hình và tìm những không gian phù hợp với nhu cầu của bạn.",
        "tags": ("Góc nhìn mới", "Cảm hứng riêng"),
    })
    records = [prop for prop in properties if prop.get("type", "Khác") == kind]
    with st.container(key=f"hn-studio-kind-{index}", height="stretch"):
        if wide:
            with st.container(key="hn-studio-single"):
                visual, content = st.columns([1.1, 1], gap="large", vertical_alignment="center")
                with visual:
                    _cover(kind, story)
                with content:
                    _kind_content(index, kind, story, records, on_category)
        else:
            _cover(kind, story)
            _kind_content(index, kind, story, records, on_category)


def _render_perspective():
    with st.container(key="hn-studio-perspective"):
        image, copy = st.columns([1, 1], gap="large", vertical_alignment="center")
        with image:
            st.html('<div class="hn-studio-perspective-image">'
                    f'<img src="{photo_uri("garden-home.jpg")}" alt="Ảnh minh họa phòng khách thoáng sáng, vật liệu gỗ và các sắc độ trung tính" loading="lazy">'
                    '<span class="hn-studio-photo-note">Ánh sáng & vật liệu · Ảnh minh họa</span></div>')
        with copy:
            st.html('<span class="hn-studio-eyebrow">GÓC NHÌN KHÔNG GIAN</span>')
            st.subheader("Đẹp trong cảm nhận. Hợp với cách sống.")
            principles = (
                ("Để ánh sáng dẫn lối", "Quan sát ánh sáng tự nhiên và độ thoáng, để từng góc nhà có cảm giác dễ chịu."),
                ("Chọn vật liệu có sự kết nối", "Gỗ ấm, sắc độ trung tính và một vài điểm nhấn có thể tạo nên nhịp điệu riêng."),
                ("Bắt đầu từ sinh hoạt mỗi ngày", "Cân nhắc chỗ lưu trữ, khoảng di chuyển và cách gia đình bạn sử dụng không gian."),
            )
            st.html('<div class="hn-studio-principles">' + "".join(
                f'<article><span>{index:02d}</span><div><strong>{title}</strong><p>{description}</p></div></article>'
                for index, (title, description) in enumerate(principles, 1)
            ) + '</div>')


def _render_cta(navigate):
    with st.container(key="hn-studio-cta"):
        copy, actions = st.columns([1.3, 1], gap="medium", vertical_alignment="center")
        with copy:
            st.html('<div class="hn-studio-cta-copy"><h2>Cùng chia sẻ ý tưởng về tổ ấm.</h2>'
                    '<p>Trao đổi kinh nghiệm với cộng đồng hoặc gửi câu hỏi về cách sử dụng HomeNest.</p></div>')
        with actions, st.container(horizontal=True, key="hn-studio-cta-actions"):
            st.button("Ghé thăm diễn đàn", icon=":material/forum:", type="primary",
                      key="design_forum", on_click=navigate, args=("forum",))
            st.button("Liên hệ HomeNest", icon=":material/arrow_forward:",
                      key="design_contact", on_click=navigate, args=("contact",))


def render_design_page(properties, *, property_types, on_category, navigate):
    st.html("<style>" + (Path(__file__).parent / "assets/design.css").read_text(encoding="utf-8") + "</style>")
    kinds = sorted(set(property_types) | {str(prop.get("type") or "Khác") for prop in properties})
    with st.container(key="hn-studio"):
        with st.container(horizontal=True, horizontal_alignment="distribute", key="hn-studio-intro"):
            st.html('<div class="hn-studio-breadcrumb">HOMENEST <span>/</span> THIẾT KẾ</div>', width="content")
            st.html('<span class="hn-studio-edition">CẢM HỨNG CHO TỔ ẤM</span>', width="content")
        _render_hero(navigate)
        _render_intro_strip()
        st.html('<div class="hn-studio-section-title" id="bo-suu-tap"><div>'
                '<span class="hn-studio-eyebrow">BỘ SƯU TẬP KHÔNG GIAN</span>'
                '<h2>Một loại hình. Nhiều cảm hứng.</h2>'
                '<p>Chọn loại hình bạn yêu thích để khám phá các tin phù hợp.</p></div>'
                f'<span class="hn-studio-count">{len(kinds):02d} LOẠI HÌNH</span></div>')
        options = ["Tất cả"] + kinds
        if st.session_state.get("design_kind") not in (None, *options):
            st.session_state.design_kind = "Tất cả"
        selected = st.pills("Chọn loại hình", options, default="Tất cả", selection_mode="single",
                            label_visibility="collapsed", key="design_kind", persist_state="session")
        indexed = [(index, kind) for index, kind in enumerate(kinds)
                   if selected in (None, "Tất cả") or kind == selected]
        with st.container(key="hn-studio-gallery"):
            if len(indexed) == 1:
                _render_kind(*indexed[0], properties, on_category, wide=True)
            else:
                with st.container(key="hn-studio-featured"):
                    columns = st.columns([1.18, 1], gap="medium")
                    for column, (index, kind) in zip(columns, indexed[:2]):
                        with column:
                            _render_kind(index, kind, properties, on_category)
                for start in range(2, len(indexed), 3):
                    group = indexed[start:start + 3]
                    with st.container(key=f"hn-studio-row-{start}"):
                        columns = st.columns(len(group), gap="medium")
                        for column, (index, kind) in zip(columns, group):
                            with column:
                                _render_kind(index, kind, properties, on_category)
        _render_perspective()
        _render_cta(navigate)
        st.html('<p class="hn-studio-footer">HomeNest · Cảm hứng hôm nay, tổ ấm ngày mai.'
                '<span>Bộ sưu tập sử dụng ảnh và minh họa gợi cảm hứng; không phải ảnh thực tế '
                'của một tin đăng hay bản vẽ kỹ thuật. Xem thông tin cụ thể trong từng tin.</span></p>')
