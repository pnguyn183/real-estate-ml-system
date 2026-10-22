"""Community forum presentation; publishing and persistence stay in the app."""

from datetime import datetime
from html import escape
from statistics import median

import pandas as pd
import streamlit as st

from landing import photo_uri


def format_forum_time(value):
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y · %H:%M")
    except (TypeError, ValueError):
        return "Thời gian chưa xác định"


def author_markup(author, timestamp, *, team=False):
    author = str(author or "Thành viên")
    initials = "H" if team else "".join(part[0] for part in author.split()[-2:]).upper()
    avatar_class = "hn-forum-avatar hn-forum-avatar-team" if team else "hn-forum-avatar"
    return (
        '<div class="hn-forum-author">'
        f'<span class="{avatar_class}" aria-hidden="true">{escape(initials or "T")}</span>'
        f'<div><strong>{escape(author)}</strong>'
        f'<small>{escape(format_forum_time(timestamp))}</small></div></div>'
    )


def render_forum_header(posts):
    comments = sum(len(post.get("comments", [])) for post in posts)
    st.html(
        '<header class="hn-forum-heading"><div>'
        '<span class="hn-forum-eyebrow">KẾT NỐI · CHIA SẺ · ĐỒNG HÀNH</span>'
        '<h1>Diễn đàn HomeNest<span>.</span></h1>'
        '<p>Một góc gặp gỡ, ngàn câu chuyện về tổ ấm.</p></div>'
        '<div class="hn-forum-heading-stats">'
        f'<span><strong>{len(posts)}</strong> chủ đề</span>'
        f'<span><strong>{comments}</strong> phản hồi</span></div></header>'
    )


def _choose_category(category):
    st.session_state.forum_category = category
    st.session_state.forum_tags = None if category == "Tất cả" else category


def _tag_changed():
    _choose_category(st.session_state.get("forum_tags") or "Tất cả")


def _category_changed():
    category = st.session_state.get("forum_category", "Tất cả")
    st.session_state.forum_tags = None if category == "Tất cả" else category


def _reset_filters(popular=False):
    _choose_category("Tất cả")
    st.session_state.forum_search = ""
    st.session_state.forum_sort = "Nhiều trao đổi" if popular else "Mới nhất"


@st.dialog("Nguyên tắc cộng đồng")
def _show_guidelines():
    st.markdown(
        "Cùng giữ HomeNest là một nơi trao đổi hữu ích.\n\n"
        "- Đặt tiêu đề rõ ràng, chọn danh mục phù hợp.\n"
        "- Nêu khu vực và bối cảnh để mọi người dễ góp ý.\n"
        "- Tôn trọng nhau, chia sẻ thông tin có nguồn hoặc trải nghiệm cụ thể.\n"
        "- Không đăng số điện thoại, giấy tờ hoặc dữ liệu cá nhân.\n"
        "- Kiểm chứng thông tin giá và pháp lý với nguồn độc lập."
    )
    st.caption("Bài ghim của đội ngũ HomeNest là nội dung hướng dẫn, không thay thế tư vấn chuyên môn.")


def _render_left(posts, categories, open_post, navigate):
    with st.container(border=True, key="hn-forum-menu"):
        st.caption("KHÁM PHÁ DIỄN ĐÀN")
        popular = st.session_state.get("forum_sort") == "Nhiều trao đổi"
        st.button("Tất cả thảo luận", icon=":material/forum:", width="stretch",
                  type="tertiary" if popular else "primary", key="forum_show_all",
                  on_click=_reset_filters)
        st.button("Nổi bật nhất", icon=":material/local_fire_department:", width="stretch",
                  type="primary" if popular else "tertiary", key="forum_show_popular",
                  on_click=_reset_filters, args=(True,))
        if st.button("Nguyên tắc cộng đồng", icon=":material/menu_book:",
                     width="stretch", type="tertiary", key="forum_guidelines"):
            _show_guidelines()
        st.button("Hỗ trợ & liên hệ", icon=":material/support_agent:", width="stretch",
                  type="tertiary", key="forum_contact", on_click=navigate, args=("contact",))

    with st.container(border=True, key="hn-forum-tags"):
        st.subheader("Tag nổi bật")
        st.pills("Lọc theo chủ đề", categories, selection_mode="single",
                 format_func=lambda name: f"# {name}", label_visibility="collapsed",
                 key="forum_tags", on_change=_tag_changed, persist_state="session")
        st.caption("Chọn một tag để tìm câu chuyện bạn quan tâm.")

    with st.container(border=True, key="hn-forum-recent"):
        st.subheader("Bình luận gần đây")
        recent = [(post, comment) for post in posts for comment in post.get("comments", [])]
        recent.sort(key=lambda item: item[1].get("created_at", ""), reverse=True)
        if not recent:
            st.caption("Những cuộc trò chuyện hay bắt đầu từ một lời chia sẻ. Hãy để lại phản hồi đầu tiên!")
        for index, (post, comment) in enumerate(recent[:3]):
            content = str(comment.get("content", ""))
            excerpt = content if len(content) <= 130 else content[:127].rstrip() + "…"
            st.html(
                '<article class="hn-forum-recent-item">'
                f'<div class="hn-forum-recent-title">{escape(str(post.get("title", "Chủ đề")))}</div>'
                f'<span class="hn-forum-recent-author">{escape(str(comment.get("author", "Thành viên")))}</span>'
                f'<p>{escape(excerpt)}</p></article>'
            )
            st.button("Vào cuộc trò chuyện", icon=":material/arrow_forward:",
                      type="tertiary", key=f"forum_recent_open_{post['id']}_{index}",
                      on_click=open_post, args=(post["id"],))


def _render_market(properties, navigate):
    with st.container(border=True, key="hn-forum-market"):
        st.subheader("Góc nhìn thị trường")
        st.caption("Giá chào bán · triệu đồng/m²")
        locations = {}
        for prop in properties:
            if (prop.get("status") == "Bán" and prop.get("price", 0) > 0
                    and prop.get("area", 0) > 0 and prop.get("location")):
                locations.setdefault(prop["location"], []).append(prop["price"] / prop["area"] / 1e6)
        rows = sorted(
            ((name, median(values), len(values)) for name, values in locations.items()),
            key=lambda item: item[1], reverse=True,
        )[:4]
        if rows:
            chart = pd.DataFrame([{"Khu vực": name, "Đơn giá": rate} for name, rate, _ in rows])
            st.bar_chart(chart, x="Khu vực", y="Đơn giá", horizontal=True,
                         color="#c65d28", sort="-Đơn giá", height=190,
                         x_label="", y_label="")
            table_rows = "".join(
                f'<tr><td>{escape(name)}</td><td>{rate:,.1f}</td><td>{count}</td></tr>'
                for name, rate, count in rows
            )
            st.html('<div class="hn-forum-market-table"><table>'
                    '<thead><tr><th>Khu vực</th><th>Giá/m²</th><th>Số tin</th></tr></thead>'
                    f'<tbody>{table_rows}</tbody></table></div>')
            st.caption("Trung vị từ tin bán trong HomeNest; giá chào tham khảo.")
        else:
            st.caption("Chưa có tin bán đủ giá và diện tích để tổng hợp.")
        st.button("Xem mặt bằng giá", icon=":material/arrow_outward:",
                  type="tertiary", width="stretch", key="forum_market",
                  on_click=navigate, args=("market",))


def _render_right(posts, properties, categories, navigate):
    st.html(
        '<aside class="hn-forum-community-banner" style="background-image:'
        f'linear-gradient(0deg,rgba(16,34,52,.93),rgba(16,34,52,.1)),url(\'{photo_uri("city-residences.jpg")}\')">'
        '<span class="hn-forum-community-label">CỘNG ĐỒNG HOMENEST</span>'
        '<h2>Cùng tìm nơi<br>mình thuộc về.</h2>'
        '<p>Kinh nghiệm thực tế. Góc nhìn đa chiều.</p></aside>'
    )
    _render_market(properties, navigate)
    participants = {
        person for post in posts
        for person in [post.get("author_id"), *[comment.get("author_id") for comment in post.get("comments", [])]]
        if person and person != "homenest"
    }
    with st.container(border=True, key="hn-forum-snapshot"):
        st.subheader("Nhịp sống cộng đồng")
        st.html(
            '<div class="hn-forum-stat-grid">'
            f'<div><strong>{len(posts)}</strong><span>Chủ đề</span></div>'
            f'<div><strong>{len(participants)}</strong><span>Thành viên tham gia</span></div>'
            f'<div><strong>{sum(len(p.get("comments", [])) for p in posts)}</strong><span>Phản hồi</span></div></div>'
        )
        for index, category in enumerate(categories):
            count = sum(post.get("category") == category for post in posts)
            if count:
                st.button(f"{category} · {count}", key=f"forum_category_link_{index}",
                          type="tertiary", width="stretch", on_click=_choose_category, args=(category,))
    st.html('<p class="hn-forum-note">HomeNest · Chia sẻ để hiểu hơn<br>về ngôi nhà và cộng đồng của bạn.</p>')


def render_forum_home(posts, properties, *, categories, post_images, open_post,
                      like_post, create_post, navigate):
    """Render the three-column forum with working native Streamlit controls."""
    with st.container(key="hn-forum-layout"):
        left, main, right = st.columns([0.95, 2.45, 1.05], gap="medium")
        with left, st.container(key="hn-forum-left"):
            _render_left(posts, categories, open_post, navigate)

        with main, st.container(key="hn-forum-main"):
            with st.container(key="hn-forum-prompt"):
                with st.expander("Viết bài, chia sẻ, đặt câu hỏi…", icon=":material/edit_square:"):
                    create_post()

            featured = sorted(posts, key=lambda post: (
                len(post.get("comments", [])) + len(post.get("likes", [])),
                bool(post.get("pinned")), post.get("created_at", ""),
            ), reverse=True)[:3]
            if featured:
                st.html('<div class="hn-forum-section-heading"><h2>Đang được quan tâm</h2>'
                        f'<span>{len(featured)} chủ đề gợi ý</span></div>')
                with st.container(key="hn-forum-featured-row"):
                    columns = st.columns(len(featured), gap="small", wrap=False)
                    for column, post in zip(columns, featured):
                        with column, st.container(border=True, key=f"hn-forum-featured-{post['id']}"):
                            images = post_images(post)
                            if images:
                                path, illustration = images[0]
                                st.image(str(path), width="stretch",
                                         caption="Ảnh minh họa HomeNest" if illustration else None)
                            else:
                                st.image(photo_uri("city-residences.jpg"), width="stretch",
                                         caption="Ảnh minh họa HomeNest")
                            st.html(author_markup(post.get("author"), post.get("created_at"),
                                                  team=post.get("author_id") == "homenest"))
                            st.subheader(post.get("title", "Chủ đề"))
                            st.button("Đọc bài", icon=":material/arrow_forward:", type="tertiary",
                                      key=f"forum_featured_open_{post['id']}",
                                      on_click=open_post, args=(post["id"],))

            st.html('<div class="hn-forum-section-heading"><h2>Thảo luận cộng đồng</h2>'
                    '<span>Lắng nghe & sẻ chia</span></div>')
            with st.container(horizontal=True, key="hn-forum-filters"):
                query = st.text_input("Tìm kiếm", placeholder="Tìm trong diễn đàn…",
                                      icon=":material/search:", label_visibility="collapsed",
                                      key="forum_search", persist_state="session").strip().casefold()
                category = st.selectbox("Danh mục", ["Tất cả"] + categories,
                                        label_visibility="collapsed", key="forum_category",
                                        on_change=_category_changed, persist_state="session")
                sort_by = st.selectbox("Sắp xếp", ["Mới nhất", "Nhiều trao đổi"],
                                       label_visibility="collapsed", key="forum_sort", persist_state="session")
            filtered = [post for post in posts
                        if (category == "Tất cả" or post.get("category") == category)
                        and (not query or query in " ".join((post.get("title", ""),
                             post.get("content", ""), post.get("author", ""))).casefold())]
            filtered.sort(key=lambda post: (
                bool(post.get("pinned")),
                len(post.get("likes", [])) + len(post.get("comments", [])) if sort_by == "Nhiều trao đổi" else 0,
                post.get("created_at", ""),
            ), reverse=True)
            st.caption(f"{len(filtered)} chủ đề · Chia sẻ điều bạn biết, hỏi điều bạn cần")
            if not filtered:
                st.info("Chưa tìm thấy chủ đề phù hợp. Thử từ khóa khác hoặc mở một cuộc thảo luận mới.",
                        icon=":material/search_off:")
                st.button("Xóa bộ lọc", key="forum_clear_filters", on_click=_reset_filters)
            for post in filtered:
                with st.container(border=True, key=f"hn-forum-feed-{post['id']}"):
                    st.html(author_markup(post.get("author"), post.get("created_at"),
                                          team=post.get("author_id") == "homenest"))
                    with st.container(horizontal=True, vertical_alignment="center"):
                        st.badge(post.get("category", "Khác"), color="gray")
                        if post.get("pinned"):
                            st.badge("Chủ đề ghim", icon=":material/push_pin:", color="orange")
                    st.subheader(post.get("title", "Chủ đề"))
                    excerpt = post.get("content", "").strip()
                    st.write(excerpt if len(excerpt) <= 280 else excerpt[:277].rstrip() + "…")
                    images = post_images(post)
                    if images:
                        path, illustration = images[0]
                        st.image(str(path), width="stretch",
                                 caption="Ảnh minh họa HomeNest" if illustration else None)
                        if len(images) > 1:
                            st.caption(f"+ {len(images) - 1} ảnh đính kèm")
                    with st.container(horizontal=True, vertical_alignment="center",
                                      horizontal_alignment="distribute", key=f"hn-forum-feed-actions-{post['id']}"):
                        likes = post.get("likes", [])
                        liked = st.session_state.username in likes
                        st.button(f"{'Bỏ thích' if liked else 'Thích'} · {len(likes)}",
                                  icon=":material/favorite:" if liked else ":material/favorite_border:",
                                  type="tertiary", key=f"forum_feed_like_{post['id']}",
                                  on_click=like_post, args=(post["id"],))
                        st.button(f"{len(post.get('comments', []))} phản hồi",
                                  icon=":material/chat_bubble_outline:", type="tertiary",
                                  help="Mở chủ đề để đọc và viết phản hồi", key=f"forum_open_{post['id']}",
                                  on_click=open_post, args=(post["id"],))

        with right, st.container(key="hn-forum-right"):
            _render_right(posts, properties, categories, navigate)
