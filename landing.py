"""Public HomeNest introduction, shown before the authentication screens."""

from base64 import b64encode
from collections import Counter
from html import escape
from pathlib import Path

import streamlit as st


ASSETS = Path(__file__).parent / "assets"


@st.cache_data(max_entries=4, show_spinner=False)
def photo_uri(filename):
    return "data:image/jpeg;base64," + b64encode(
        (ASSETS / "properties" / filename).read_bytes()).decode("ascii")


def line_icon(name):
    """Small local vector icons, with no external font or network dependency."""
    paths = {
        "home": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/>',
        "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5M8 11h5m-2.5-2.5v5"/>',
        "chart": '<path d="M4 3v17h17M8 15l4-5 4 2 5-7M17 5h4v4"/>',
        "edit": '<path d="M13 5H5a2 2 0 0 0-2 2v13h13a2 2 0 0 0 2-2v-7M15 3l6 6M10 14l2-6 6-6 6 6-6 6-6 2Z"/>',
        "bookmark": '<path d="M6 3h12v18l-6-4-6 4Z"/>',
        "forum": '<path d="M21 11a8 8 0 0 1-8 8H6l-4 3V11a9 9 0 0 1 18-3M7 9h8M7 13h5"/>',
        "dashboard": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="11" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 18h7m-7 3h7"/>',
        "check": '<path d="m5 12 4 4L19 6"/>',
        "arrow": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
        "shield": '<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Z"/><path d="m8 12 3 3 5-6"/>',
    }
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#c65d28" '
            'stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" '
            f'>{paths[name]}</svg>')
    # Streamlit sanitizes inline SVG; an image keeps the local vector intact.
    encoded = b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="hn-lp-icon" alt="" aria-hidden="true" src="data:image/svg+xml;base64,{encoded}">'


def section_title(eyebrow, title, description, anchor):
    st.html(f'<div class="hn-lp-section-title" id="{anchor}">'
            f'<span class="hn-lp-eyebrow">{eyebrow}</span><h2>{title}</h2>'
            f'<p>{description}</p></div>')


def render_landing(properties, on_login):
    """Keep authentication actions native so they share the existing session flow."""
    st.html("<style>" + (ASSETS / "landing.css").read_text(encoding="utf-8") + "</style>")
    sale_count = sum(p.get("status") == "Bán" for p in properties)
    rent_count = sum(p.get("status") == "Cho thuê" for p in properties)

    with st.container(key="hn-landing", gap=None):
        with st.container(horizontal=True, vertical_alignment="center",
                          horizontal_alignment="distribute", key="hn-lp-header"):
            st.html(f'<a class="hn-lp-brand" href="#homenest" aria-label="HomeNest — Trang giới thiệu">'
                    f'<span class="hn-lp-brand-symbol">{line_icon("home")}</span>'
                    '<span><strong>HomeNest<span>.</span></strong>'
                    '<small>KHÔNG GIAN BẤT ĐỘNG SẢN</small></span></a>')
            st.html('<nav class="hn-lp-nav" aria-label="Điều hướng trang giới thiệu">'
                    '<a class="hn-lp-nav-active" href="#homenest">Trang chủ</a>'
                    '<a href="#tinh-nang">Tính năng</a><a href="#cach-hoat-dong">Cách hoạt động</a>'
                    '<a href="#cau-hoi">Hỏi đáp</a></nav>')
            st.button("Đăng nhập", icon=":material/login:", type="primary",
                      key="landing_login", on_click=on_login, args=("login",))

        st.html("<style>.st-key-hn-lp-project-hero { --hn-project-image: url('"
                + photo_uri("modern-home.jpg") + "'); }</style>")
        with st.container(key="hn-lp-project-hero"):
            left, center, right = st.columns(
                [0.95, 1.25, 0.55], gap="medium", vertical_alignment="center",
            )
            with left:
                with st.container(key="hn-lp-project-signup"):
                    st.caption("THÀNH VIÊN HOMENEST")
                    st.subheader("Tìm nơi chốn phù hợp với bạn")
                    st.markdown(
                        "- Khám phá tin mua bán và cho thuê\n"
                        "- Lọc theo khu vực, loại hình và ngân sách\n"
                        "- Tham khảo giá từ tin đăng tương đồng"
                    )
                    st.button(
                        "Tạo tài khoản miễn phí", icon=":material/person_add:",
                        type="primary", width="stretch", key="landing_project_register",
                        on_click=on_login, args=("register",),
                    )
                    st.button(
                        "Đã có tài khoản? Đăng nhập", icon=":material/login:",
                        width="stretch", key="landing_start",
                        on_click=on_login, args=("login",),
                    )
                    st.caption("Ảnh minh họa · Thông tin giá dựa trên tin đăng HomeNest")
            with center:
                st.html(
                    '<div class="hn-lp-project-copy" id="homenest">'
                    '<span>KHÔNG GIAN BẤT ĐỘNG SẢN</span>'
                    '<h1>Mở lối đến<br>không gian sống<br>bạn mong muốn.</h1>'
                    '<p>Tìm kiếm, đăng tin và khám phá giá tham khảo trong một trải nghiệm liền mạch.</p>'
                    '</div>'
                )
            with right:
                st.html(
                    '<aside class="hn-lp-project-count">'
                    '<span>DANH MỤC HOMENEST</span>'
                    f'<strong>{len(properties):,}</strong>'
                    '<b>tin bất động sản</b>'
                    '<small>Mua bán · Cho thuê</small>'
                    '</aside>'
                )
            st.html(
                '<div class="hn-lp-project-strip">'
                '<span>TỔNG QUAN DANH MỤC</span>'
                f'<strong>{sale_count:,} tin bán</strong>'
                '<i></i>'
                f'<strong>{rent_count:,} tin cho thuê</strong>'
                '<i></i>'
                '<a href="#tinh-nang">Khám phá HomeNest</a>'
                '</div>'
            )

        with st.container(key="hn-lp-features"):
            section_title("HomeNest", "Các công cụ trong ứng dụng",
                          "Tìm tin, tham khảo giá và quản lý thông tin bất động sản tại một nơi.", "tinh-nang")
            features = [
                ("search", "Tìm tin bất động sản", "Lọc tin mua bán và cho thuê theo từ khóa, khu vực, loại hình và ngân sách.", "Khám phá", "explore"),
                ("chart", "Tham khảo giá", "Xem trung vị giá chào bán/m² từ các tin tương đồng trong danh mục HomeNest.", "Phân tích & định giá", "valuation"),
                ("edit", "Quản lý tin đăng", "Đăng tin kèm ảnh, mô tả và thông tin liên hệ; cập nhật các tin do bạn đăng.", "Tin của tôi", "mine"),
                ("bookmark", "Lưu tin quan tâm", "Lưu lại tin để xem sau và liên hệ người đăng khi cần.", "Tin đã lưu", "favorites"),
                ("dashboard", "Theo dõi danh mục", "Xem số tin bán, cho thuê và thống kê theo loại hình hoặc khu vực.", "Tổng quan", "overview"),
                ("forum", "Trao đổi và hỗ trợ", "Đăng chủ đề, phản hồi trong diễn đàn hoặc gửi yêu cầu hỗ trợ.", "Diễn đàn & hỗ trợ", "forum"),
            ]
            for start in range(0, len(features), 3):
                for column, (symbol, title, description, label, destination) in zip(
                    st.columns(3), features[start:start + 3]
                ):
                    with column, st.container(key=f"hn-lp-feature-card-{destination}"):
                        st.html(
                            '<article class="hn-lp-feature"><div class="hn-lp-feature-top">'
                            f'<span class="hn-lp-feature-icon">{line_icon(symbol)}</span></div>'
                            f'<h3>{title}</h3><p>{description}</p></article>'
                        )
                        st.button(
                            label, icon=":material/arrow_forward:",
                            key=f"landing_feature_{destination}",
                            on_click=on_login, args=("login", destination),
                        )

        with st.container(key="hn-lp-method"):
            left, right = st.columns([1, 1.05], gap="large", vertical_alignment="center")
            with left:
                render_catalog_snapshot(properties)
            with right:
                st.html('<div class="hn-lp-method-copy" id="cach-hoat-dong">'
                        '<span class="hn-lp-eyebrow">Tham khảo giá</span>'
                        '<h2>Giá tham khảo được tính như thế nào?</h2>'
                        '<p>Kết quả dựa trên các tin đăng hiện có, không phải giá giao dịch thực tế.</p>'
                        '<ol class="hn-lp-method-steps">'
                        '<li><span>01</span><div><h3>Chọn bất động sản cần tìm hiểu</h3>'
                        '<p>Nhập khu vực, loại hình và diện tích tài sản bạn quan tâm.</p></div></li>'
                        '<li><span>02</span><div><h3>Đối chiếu các tin tương đồng</h3>'
                        '<p>Hệ thống chọn các tin bán phù hợp trong danh mục, loại dữ liệu không hợp lệ và tin trùng.</p></div></li>'
                        '<li><span>03</span><div><h3>Hiểu kết quả, chủ động quyết định</h3>'
                        '<p>Xem giá tham khảo từ trung vị đơn giá, số mẫu và bảng so sánh chi tiết.</p></div></li></ol>'
                        '<div class="hn-lp-method-note">Giá tham khảo dựa trên tin chào bán trong HomeNest; '
                        'chưa phải giá giao dịch hoặc kết quả thẩm định chuyên nghiệp.</div></div>')

        st.html('<section class="hn-lp-roles" id="danh-cho-ban">'
                '<div class="hn-lp-role-intro"><span class="hn-lp-eyebrow">Tài khoản</span>'
                '<h2>Công cụ theo vai trò</h2></div>'
                '<div class="hn-lp-role"><span>User</span><h3>Tìm và quản lý tin</h3>'
                '<p>Lọc tin, lưu lựa chọn, đăng bài và cập nhật các tin cá nhân.</p></div>'
                '<div class="hn-lp-role"><span>Manager</span><h3>Phân tích và định giá</h3>'
                '<p>Xem phương pháp tham khảo giá và tải lên CSV để định giá hàng loạt.</p></div>'
                '<div class="hn-lp-role"><span>Admin</span><h3>Quản trị danh mục</h3>'
                '<p>Quản lý tài khoản User, tin đăng, báo cáo và yêu cầu hỗ trợ.</p></div>'
                '</section>')

        with st.container(key="hn-lp-faq"):
            section_title("Thông tin", "Câu hỏi thường gặp",
                          "Một số thông tin về tài khoản và cách HomeNest hiển thị giá tham khảo.", "cau-hoi")
            questions = [
                ("HomeNest giúp tôi làm gì?", "HomeNest kết nối tìm kiếm tin bán/cho thuê, quản lý bài đăng, lưu yêu thích, phân tích giá tham khảo và trao đổi trên diễn đàn. Các biểu đồ phản ánh danh mục hiện có trong ứng dụng."),
                ("Tôi bắt đầu sử dụng như thế nào?", "Bấm Đăng nhập hoặc Bắt đầu cùng HomeNest để mở trang tài khoản. Nếu chưa có tài khoản, chọn Tạo tài khoản, nhập họ tên, email và mật khẩu tối thiểu 12 ký tự. Đăng ký thành công sẽ mở không gian HomeNest của bạn."),
                ("Giá tham khảo được tính ra sao?", "HomeNest lấy trung vị đơn giá chào bán/m² từ các tin cùng khu vực và loại hình, rồi nhân với diện tích cần tìm hiểu. Kết quả có số mẫu và dữ liệu đối chiếu. Khi thiếu mẫu phù hợp, hệ thống thông báo thay vì tự suy đoán. Giá này chưa điều chỉnh đầy đủ pháp lý, chất lượng hay tiện ích."),
                ("Tôi có thể đăng tin bán và cho thuê không?", "Có. Sau khi đăng nhập, chọn Đăng tin mới để nhập thông tin, nhu cầu bán hoặc cho thuê và tải tối đa 5 ảnh. Bạn có thể chỉnh sửa hoặc xóa các tin do mình đăng trong Bài đăng của tôi."),
                ("Tài khoản mới có những quyền nào?", "Tài khoản đăng ký mới có vai trò User: tìm tin, lưu tin, đăng và quản lý tin cá nhân, tham khảo giá, sử dụng diễn đàn và hộp thư. Vai trò Manager/Admin do người vận hành cấp riêng, với công cụ và phạm vi quản lý tương ứng."),
            ]
            for question, answer in questions:
                with st.expander(question):
                    st.write(answer)

        with st.container(key="hn-lp-cta", horizontal_alignment="center"):
            st.html('<div class="hn-lp-cta-copy"><span class="hn-lp-eyebrow">HomeNest</span>'
                    '<h2>Bắt đầu tìm kiếm</h2>'
                    '<p>Đăng nhập để xem tin đăng và các công cụ trong ứng dụng.</p></div>')
            st.button("Đăng nhập", icon=":material/arrow_forward:",
                      type="primary", key="landing_cta", on_click=on_login, args=("login",))
            st.caption("Chưa có tài khoản? Bạn có thể tạo tài khoản User miễn phí.")

        st.html(f'<footer class="hn-lp-footer"><div><a class="hn-lp-brand" href="#homenest">'
                f'<span class="hn-lp-brand-symbol">{line_icon("home")}</span>'
                '<span><strong>HomeNest<span>.</span></strong><small>Không gian bất động sản</small></span></a>'
                '<p>Tin đăng và giá tham khảo từ danh mục HomeNest.</p></div>'
                '<div class="hn-lp-footer-links"><a href="#tinh-nang">Tính năng</a>'
                '<a href="#cach-hoat-dong">Cách hoạt động</a><a href="#cau-hoi">Hỏi đáp</a></div>'
                '<div class="hn-lp-footer-note">HomeNest · Không gian bất động sản<br>'
                'Ảnh minh họa. Thống kê từ danh mục hiện có.</div></footer>')


def render_catalog_snapshot(properties):
    """A small, accessible chart derived from the same catalogue as the dashboard."""
    counts = Counter(p.get("type", "Khác") for p in properties)
    largest = max(counts.values(), default=1)
    bars = "".join(
        f'<li><span>{escape(str(kind))}</span><div class="hn-lp-bar-track">'
        f'<div style="width:{count / largest * 100:.2f}%"></div></div><strong>{count}</strong></li>'
        for kind, count in counts.most_common(5))
    if not bars:
        bars = '<li class="hn-lp-chart-empty">Danh mục đang chờ những tin đăng đầu tiên.</li>'
    sale_count = sum(p.get("status") == "Bán" for p in properties)
    rent_count = sum(p.get("status") == "Cho thuê" for p in properties)
    st.html('<div class="hn-lp-snapshot-wrap"><div class="hn-lp-snapshot">'
            '<div class="hn-lp-snapshot-top"><span>Danh mục HomeNest</span>'
            f'<span class="hn-lp-snapshot-icon">{line_icon("dashboard")}</span></div>'
            f'<h3>Tin đăng theo loại hình</h3><div class="hn-lp-snapshot-number">{len(properties):,}'
            '<span>tin hiện có</span></div>'
            '<div class="hn-lp-chart-heading">Cơ cấu theo loại hình <span>Số tin</span></div>'
            f'<ul class="hn-lp-bars" aria-label="Số tin theo loại bất động sản">{bars}</ul>'
            '<div class="hn-lp-snapshot-bottom">'
            f'<span><i></i> {sale_count} tin bán</span><span><i></i> {rent_count} tin cho thuê</span></div>'
            '<p>Thống kê từ danh mục HomeNest; không đại diện toàn bộ thị trường.</p></div></div>')
