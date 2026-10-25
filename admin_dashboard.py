"""Operational admin pages backed by the current inventory and authenticated data."""

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta
from html import escape
from statistics import median
from urllib.parse import urlsplit

import altair as alt
import pandas as pd
import streamlit as st

from admin_ui import (LOCAL_TIME, admin_heading, display_time, kpis, listing_table,
                      money, normalized, parsed_time, positive, select_filter)
from api_client import ValuationAPIError, admin_list_users
from csv_export import spreadsheet_safe_csv
from session_auth import api_bearer_token
from storage import detect_duplicate_properties


PERIODS = ['Toàn bộ thời gian', '7 ngày gần đây', '30 ngày gần đây', '90 ngày gần đây']
STATUSES = ['Tất cả', 'Bán', 'Cho thuê']
QUALITIES = ['Tất cả', 'Chưa có ảnh', 'Thiếu liên hệ', 'Chưa rõ người đăng']


def inventory_stats(records):
    sales = [prop for prop in records if prop.get('status') == 'Bán']
    prices = [value for prop in sales if (value := positive(prop.get('price'))) is not None]
    rates = [price / area for prop in sales
             if (price := positive(prop.get('price'))) is not None
             and (area := positive(prop.get('area'))) is not None]
    return {'total': len(records), 'sale': len(sales),
            'rent': sum(prop.get('status') == 'Cho thuê' for prop in records),
            'total_sale': sum(prices), 'median_unit': median(rates) if rates else None,
            'missing_images': sum(not prop.get('images') for prop in records),
            'missing_contact': sum(not str(prop.get('contact') or '').strip() for prop in records),
            'missing_owner': sum(not str(prop.get('user_id') or '').strip() for prop in records)}


def filter_inventory(records, *, query='', status='Tất cả', kind='Tất cả', location='Tất cả',
                     quality='Tất cả', period=PERIODS[0], now=None):
    moment = now or datetime.now(LOCAL_TIME)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=LOCAL_TIME)
    start = moment - timedelta(days=int(period.split()[0])) if period in PERIODS[1:] else None
    result = []
    for prop in records:
        text = normalized(' '.join(str(prop.get(field) or '') for field in
                                   ('id', 'title', 'location', 'type', 'user_id')))
        if normalized(query) not in text:
            continue
        if any(value != 'Tất cả' and prop.get(field) != value for field, value in
               (('status', status), ('type', kind), ('location', location))):
            continue
        if quality == 'Chưa có ảnh' and prop.get('images'):
            continue
        if quality == 'Thiếu liên hệ' and str(prop.get('contact') or '').strip():
            continue
        if quality == 'Chưa rõ người đăng' and str(prop.get('user_id') or '').strip():
            continue
        if start is not None:
            created = parsed_time(prop.get('created_at'))
            if created is None or not start <= created <= moment:
                continue
        result.append(prop)
    return result


def _recent_key(prop):
    moment = parsed_time(prop.get('created_at'))
    return moment.timestamp() if moment else 0


def _reset_listing_filters():
    st.session_state.update(manage_search='', admin_listing_status='Tất cả',
                            admin_listing_kind='Tất cả', admin_listing_location='Tất cả',
                            admin_listing_quality='Tất cả', admin_listing_sort='Mới nhất',
                            admin_listing_page=1)


def _review_quality(quality, navigate):
    _reset_listing_filters()
    st.session_state.admin_listing_quality = quality
    navigate('listings')


def _pagination(records, prefix):
    with st.container(horizontal=True, vertical_alignment='bottom', key=f'hn-admin-pagination-{prefix}'):
        size = select_filter('Số mục mỗi trang', [20, 50, 100], f'{prefix}_size')
        pages = max(1, (len(records) + size - 1) // size)
        page = select_filter('Trang', list(range(1, pages + 1)), f'{prefix}_page')
        st.caption(f'{len(records)} mục · {pages} trang')
    return records[(page - 1) * size:page * size]


def _bar_chart(rows, x, y, *, color='#244a64', height=250, integer=False):
    chart = alt.Chart(pd.DataFrame(rows)).mark_bar(color=color, cornerRadiusEnd=4, size=22).encode(
        x=alt.X(f'{x}:Q', title=None, axis=alt.Axis(tickMinStep=1) if integer else alt.Axis()),
        y=alt.Y(f'{y}:N', title=None, sort='-x', axis=alt.Axis(labelLimit=155)),
        tooltip=[alt.Tooltip(f'{y}:N'), alt.Tooltip(f'{x}:Q', format=',.0f' if integer else ',.2f')],
    ).properties(height=height).configure_axis(labelFontSize=12, titleFontSize=12).configure_view(stroke=None)
    st.altair_chart(chart, width='stretch')


def render_inventory_charts(records, *, activity=False, prefix='overview'):
    with st.container(key=f'hn-admin-{prefix}-charts'):
        left, right = st.columns([1, 1.3], gap='medium')
        with left, st.container(border=True, key=f'hn-admin-chart-types-{prefix}'):
            st.subheader('Cơ cấu tin đăng', anchor=False)
            st.caption('Số lượng theo loại bất động sản')
            counts = Counter(prop.get('type') or 'Chưa phân loại' for prop in records)
            if counts:
                _bar_chart([{'Loại': kind, 'Số tin': count} for kind, count in counts.items()],
                           'Số tin', 'Loại', integer=True)
            else:
                st.info('Chưa có tin đăng trong phạm vi đã chọn.')
        with right, st.container(border=True, key=f'hn-admin-chart-activity-{prefix}'):
            if activity:
                st.subheader('Nhịp đăng tin', anchor=False)
                st.caption('Theo ngày tạo của các tin trong danh mục hiện tại')
                counts = Counter(created.date() for prop in records
                                 if (created := parsed_time(prop.get('created_at'))) is not None)
                if counts:
                    frame = pd.DataFrame([{'Ngày': day.isoformat(), 'Số tin': count}
                                          for day, count in sorted(counts.items())])
                    chart = alt.Chart(frame).mark_bar(color='#b8582a', cornerRadiusTopLeft=4,
                                                     cornerRadiusTopRight=4).encode(
                        x=alt.X('Ngày:T', title=None, axis=alt.Axis(format='%d/%m', labelAngle=0)),
                        y=alt.Y('Số tin:Q', title=None, axis=alt.Axis(tickMinStep=1)),
                        tooltip=[alt.Tooltip('Ngày:T', format='%d/%m/%Y'), 'Số tin:Q'],
                    ).properties(height=250).configure_axis(labelFontSize=12).configure_view(stroke=None)
                    st.altair_chart(chart, width='stretch')
                else:
                    st.info('Chưa có ngày tạo hợp lệ để thống kê.')
            else:
                st.subheader('Đơn giá chào bán theo khu vực', anchor=False)
                st.caption('Trung vị từ tin bán có giá và diện tích hợp lệ · triệu ₫/m²')
                rows = [{'Khu vực': prop.get('location', ''), 'Triệu ₫/m²': price / area / 1e6}
                        for prop in records if prop.get('status') == 'Bán'
                        and (price := positive(prop.get('price'))) is not None
                        and (area := positive(prop.get('area'))) is not None]
                if rows:
                    grouped = pd.DataFrame(rows).groupby('Khu vực', as_index=False)['Triệu ₫/m²'].median()
                    _bar_chart(grouped.to_dict('records'), 'Triệu ₫/m²', 'Khu vực', color='#b8582a')
                else:
                    st.info('Chưa có tin bán hợp lệ trong phạm vi đã chọn.')


def render_overview(records, *, navigate, refresh, open_detail, messages):
    admin_heading('Tổng quan quản trị', 'Theo dõi danh mục, chất lượng nội dung và các yêu cầu hỗ trợ từ một nơi.')
    with st.container(horizontal=True, vertical_alignment='bottom', key='hn-admin-toolbar'):
        period = select_filter('Phạm vi thống kê tin đăng', PERIODS, 'admin_overview_period')
        st.button('Làm mới dữ liệu', icon=':material/refresh:', key='admin_overview_refresh', on_click=refresh)
        st.button('Đăng tin mới', icon=':material/add_home:', type='primary',
                  key='admin_overview_publish', on_click=navigate, args=('editor',))
    filtered = filter_inventory(records, period=period)
    stats = inventory_stats(filtered)
    groups = detect_duplicate_properties(records)
    support = [item for item in messages if item.get('to') == 'support']
    users = None
    try:
        users = admin_list_users(api_bearer_token())['users']
    except (ValuationAPIError, RuntimeError) as error:
        st.warning('Chưa tải được thống kê tài khoản. ' + str(error))
    kpis([
        ('Tin trong danh mục', stats['total'], 'inventory_2', 'Theo phạm vi thời gian đã chọn.'),
        ('Tài khoản User', len(users) if users is not None else '—', 'group', 'Tài khoản User nội bộ trên toàn hệ thống.'),
        ('Yêu cầu hỗ trợ', len(support), 'support_agent', 'Tổng yêu cầu đã gửi đến hộp thư hỗ trợ; chưa phân loại tiến độ xử lý.'),
        ('Nhóm tin tương tự', len(groups), 'difference', 'Trong toàn bộ danh mục. Cần kiểm tra nội dung trước khi xử lý.'),
    ])
    st.html('<div class="hn-admin-snapshot">'
            f'<span><strong>{stats["sale"]}</strong> tin bán</span>'
            f'<span><strong>{stats["rent"]}</strong> tin cho thuê</span>'
            f'<span>Phạm vi tin đăng: <strong>{escape(period)}</strong></span></div>')
    render_inventory_charts(filtered, activity=True)
    with st.container(key='hn-admin-workspace'):
        left, right = st.columns([1.45, 1], gap='medium')
        with left, st.container(border=True, key='hn-admin-recent'):
            st.subheader('Tin đăng gần đây', anchor=False)
            st.caption('5 tin mới nhất trong phạm vi đang xem')
            recent = sorted(filtered, key=_recent_key, reverse=True)[:5]
            if recent:
                listing_table(recent, key='admin_recent_table')
                selected = select_filter('Mở nhanh tin đăng', [prop['id'] for prop in recent], 'admin_recent_selected')
                st.button('Xem tin đã chọn', key='admin_recent_open', icon=':material/open_in_new:',
                          on_click=open_detail, args=(selected,))
            else:
                st.info('Chưa có tin đăng trong phạm vi đã chọn.')
        with right, st.container(border=True, key='hn-admin-attention'):
            st.subheader('Nội dung cần rà soát', anchor=False)
            st.caption('Kiểm tra toàn bộ danh mục trước khi cập nhật hoặc xóa tin.')
            all_stats = inventory_stats(records)
            for field, label, quality in (
                ('missing_images', 'Tin chưa đính kèm ảnh', 'Chưa có ảnh'),
                ('missing_contact', 'Tin thiếu thông tin liên hệ', 'Thiếu liên hệ'),
                ('missing_owner', 'Tin chưa rõ người đăng', 'Chưa rõ người đăng'),
            ):
                st.html(f'<div class="hn-admin-attention-row"><span>{label}</span><strong>{all_stats[field]}</strong></div>')
                st.button('Rà soát danh sách', key=f'admin_review_{field}', type='tertiary',
                          icon=':material/arrow_forward:', on_click=_review_quality, args=(quality, navigate))
    with st.container(key='hn-admin-shortcuts'):
        for symbol, title, description, view, key in (
            ('inventory_2', 'Quản lý tin đăng', 'Tìm, lọc và cập nhật toàn bộ danh mục.', 'listings', 'admin_shortcut_listings'),
            ('manage_accounts', 'Tài khoản User', 'Hồ sơ, quyền truy cập và nhật ký quản trị.', 'accounts', 'admin_overview_accounts'),
            ('difference', 'Đối chiếu tin trùng', 'Xem các nhóm tương tự để kiểm tra nội dung.', 'duplicates', 'admin_shortcut_duplicates'),
            ('assessment', 'Báo cáo & xuất dữ liệu', 'Tổng hợp và tải danh mục theo bộ lọc.', 'reports', 'admin_shortcut_reports'),
        ):
            with st.container(border=True, key=f'hn-admin-shortcut-{view}'):
                st.badge(title, icon=f':material/{symbol}:', color='gray')
                st.write(description)
                st.button('Mở quản lý', key=key, icon=':material/arrow_forward:', on_click=navigate, args=(view,))


def render_listings(records, *, navigate, refresh, open_detail, request_delete):
    admin_heading('Quản lý tin đăng', 'Tìm đúng tin, kiểm tra nội dung và cập nhật danh mục.', 'TIN ĐĂNG')
    stats = inventory_stats(records)
    kpis([('Tổng tin đăng', len(records), 'inventory_2', ''),
          ('Tin bán', stats['sale'], 'sell', ''), ('Tin cho thuê', stats['rent'], 'key', ''),
          ('Chưa đính kèm ảnh', stats['missing_images'], 'image', '')])
    with st.container(horizontal=True, key='hn-admin-toolbar'):
        st.button('Thêm tin đăng', key='admin_listing_create', icon=':material/add_home:',
                  type='primary', on_click=navigate, args=('editor',))
        st.button('Tải lại danh sách', key='admin_listing_refresh', icon=':material/refresh:', on_click=refresh)
        st.button('Xóa bộ lọc', key='admin_listing_reset', icon=':material/filter_alt_off:', on_click=_reset_listing_filters)
    with st.container(border=True, key='hn-admin-list-filters'):
        a, b, c = st.columns([1.7, 1, 1], gap='small')
        st.session_state.setdefault('manage_search', '')
        query = a.text_input('Tìm trong danh sách', value=None, key='manage_search',
                             placeholder='Mã tin, tiêu đề, khu vực hoặc người đăng', persist_state='session')
        with b:
            status = select_filter('Nhu cầu', STATUSES, 'admin_listing_status')
        with c:
            kind = select_filter('Loại bất động sản', ['Tất cả'] + sorted({prop['type'] for prop in records}), 'admin_listing_kind')
        a, b, c = st.columns([1.7, 1, 1], gap='small')
        with a:
            location = select_filter('Khu vực', ['Tất cả'] + sorted({prop['location'] for prop in records}), 'admin_listing_location')
        with b:
            quality = select_filter('Nội dung cần rà soát', QUALITIES, 'admin_listing_quality')
        with c:
            order = select_filter('Sắp xếp', ['Mới nhất', 'Giá tăng dần', 'Giá giảm dần', 'Diện tích lớn nhất'], 'admin_listing_sort')
    filtered = filter_inventory(records, query=query, status=status, kind=kind, location=location, quality=quality)
    sort_key, reverse = {'Mới nhất': (_recent_key, True),
                         'Giá tăng dần': (lambda prop: prop.get('price', 0), False),
                         'Giá giảm dần': (lambda prop: prop.get('price', 0), True),
                         'Diện tích lớn nhất': (lambda prop: prop.get('area', 0), True)}[order]
    filtered.sort(key=sort_key, reverse=reverse)
    with st.container(border=True, key='hn-admin-list-table'):
        st.subheader('Danh sách tin đăng', anchor=False)
        st.caption(f'{len(filtered)} / {len(records)} tin phù hợp với bộ lọc')
        if not filtered:
            st.info('Chưa có tin phù hợp. Thử thay đổi từ khóa hoặc dùng “Xóa bộ lọc”.')
            return
        page = _pagination(filtered, 'admin_listing')
        listing_table(page, key='admin_listings_table')
    with st.container(border=True, key='hn-admin-record-actions'):
        selected = select_filter('Chọn tin', [prop['id'] for prop in page], 'manage_selected')
        chosen = next(prop for prop in page if prop['id'] == selected)
        st.write(f'**#{selected} · {chosen["title"]}**')
        st.caption(f'{chosen["location"]} · {chosen["status"]} · {money(chosen["price"])}')
        with st.container(horizontal=True):
            st.button('Xem chi tiết', key='manage_detail', icon=':material/open_in_new:', on_click=open_detail, args=(selected,))
            st.button('Chỉnh sửa', key='manage_edit', icon=':material/edit_note:', type='primary', on_click=navigate, args=('editor', selected))
            st.button('Xóa tin', key='manage_delete', icon=':material/delete_outline:', on_click=request_delete, args=(selected,))
        st.caption('Xóa tin cần xác nhận riêng. Hãy kiểm tra đúng mã tin trước khi thực hiện.')


def render_duplicates(records, *, open_detail):
    admin_heading('Kiểm tra tin trùng', 'Đối chiếu các nhóm tin tương tự trước khi quyết định cập nhật hoặc xóa.', 'CHẤT LƯỢNG NỘI DUNG')
    groups = detect_duplicate_properties(records)
    kpis([('Nhóm tương tự', len(groups), 'difference', ''),
          ('Tin cần đối chiếu', sum(map(len, groups)), 'fact_check', ''),
          ('Tổng danh mục', len(records), 'inventory_2', '')])
    with st.container(border=True, key='hn-admin-duplicates'):
        st.subheader('Các nhóm cần kiểm tra', anchor=False)
        st.caption('Đối chiếu theo loại hình, khu vực, tiêu đề, giá và diện tích. Đây là dấu hiệu tương tự, chưa xác nhận tin trùng.')
        query = st.text_input('Tìm nhóm tin', key='admin_duplicate_search', placeholder='Tiêu đề hoặc khu vực', persist_state='session')
        filtered = [(number, group) for number, group in enumerate(groups, 1)
                    if normalized(query) in normalized(' '.join(f'{prop["title"]} {prop["location"]}' for prop in group))]
        if not groups:
            st.success('Chưa phát hiện nhóm tin trùng trong danh mục.', icon=':material/fact_check:')
        elif not filtered:
            st.info('Không có nhóm phù hợp với từ khóa đang tìm.')
        for number, group in filtered:
            with st.expander(f'Nhóm {number} · {len(group)} tin · {group[0]["location"]}', expanded=number == filtered[0][0]):
                listing_table(group, key=f'admin_duplicate_table_{number}')
                selected = select_filter('Chọn tin để đối chiếu', [prop['id'] for prop in group], f'admin_duplicate_selected_{number}')
                st.button('Xem nội dung tin', key=f'admin_duplicate_open_{number}', icon=':material/open_in_new:', on_click=open_detail, args=(selected,))


def render_reports(records):
    admin_heading('Báo cáo danh mục', 'Theo dõi nguồn cung, so sánh giá chào bán và xuất đúng phạm vi dữ liệu cần dùng.', 'BÁO CÁO')
    with st.container(border=True, key='hn-admin-report-filters'):
        a, b, c, d = st.columns(4, gap='small')
        with a:
            period = select_filter('Thời gian tạo tin', PERIODS, 'admin_report_period')
        with b:
            status = select_filter('Nhu cầu', STATUSES, 'admin_report_status')
        with c:
            kind = select_filter('Loại bất động sản', ['Tất cả'] + sorted({prop['type'] for prop in records}), 'admin_report_kind')
        with d:
            location = select_filter('Khu vực', ['Tất cả'] + sorted({prop['location'] for prop in records}), 'admin_report_location')
    filtered = filter_inventory(records, period=period, status=status, kind=kind, location=location)
    stats = inventory_stats(filtered)
    kpis([('Tin phù hợp', len(filtered), 'inventory_2', 'Theo bộ lọc báo cáo.'),
          ('Tổng giá chào bán', money(stats['total_sale']), 'payments', 'Chỉ cộng tin bán có giá hợp lệ, không cộng giá cho thuê.'),
          ('Trung vị đơn giá bán / m²', money(stats['median_unit']) if stats['median_unit'] is not None else '—', 'square_foot', 'Giá chào bán chia diện tích từ các tin hợp lệ.'),
          ('Tin cho thuê', stats['rent'], 'key', 'Số tin cho thuê trong phạm vi đang chọn.')])
    st.caption('Giá chào bán trong danh mục không phải doanh thu hay giá giao dịch đã xác nhận. Tin thuê được tách khỏi tổng giá bán.')
    render_inventory_charts(filtered, prefix='report')
    with st.container(border=True, key='hn-admin-report-data'):
        with st.container(horizontal=True, horizontal_alignment='distribute', vertical_alignment='center'):
            st.subheader('Dữ liệu báo cáo', anchor=False)
            st.download_button('Xuất danh mục CSV', data=spreadsheet_safe_csv(pd.DataFrame(filtered)),
                               file_name=f'homenest_{datetime.now(LOCAL_TIME):%Y%m%d}.csv', mime='text/csv',
                               key='admin_report_export', icon=':material/download:', type='primary', disabled=not filtered)
        st.caption(f'{len(filtered)} / {len(records)} tin · CSV xuất toàn bộ kết quả phù hợp, gồm các trang chưa hiển thị.')
        if filtered:
            listing_table(_pagination(sorted(filtered, key=_recent_key, reverse=True), 'admin_report'), key='admin_report_table')
        else:
            st.info('Không có dữ liệu cho bộ lọc này. Thử thay đổi thời gian, nhu cầu hoặc khu vực.')


def render_support(messages):
    admin_heading('Hộp thư hỗ trợ', 'Đọc và tra cứu các yêu cầu gửi đến HomeNest cùng tin nhắn dành cho tài khoản Admin.', 'HỖ TRỢ')
    scoped = [item for item in messages if item.get('to') in {'support', st.session_state.username}]
    support = [item for item in scoped if item.get('to') == 'support']
    kpis([('Yêu cầu hỗ trợ', len(support), 'support_agent', 'Tổng yêu cầu đã gửi, chưa phân loại tiến độ xử lý.'),
          ('Tin gửi riêng cho bạn', len(scoped) - len(support), 'inbox', ''),
          ('Người gửi yêu cầu', len({item.get('from') for item in support}), 'group', '')])
    with st.container(border=True, key='hn-admin-support-filters'):
        a, b, c = st.columns([1.6, 1, 1], gap='small')
        query = a.text_input('Tìm trong hộp thư', key='admin_support_search',
                             placeholder='Người gửi, tiêu đề hoặc nội dung', persist_state='session')
        with b:
            channel = select_filter('Loại tin nhắn', ['Tất cả', 'Yêu cầu hỗ trợ', 'Gửi riêng cho bạn'], 'admin_support_channel')
        with c:
            order = select_filter('Sắp xếp', ['Mới nhất', 'Cũ nhất'], 'admin_support_sort')
    filtered = [item for item in scoped
                if normalized(query) in normalized(' '.join(str(item.get(field) or '') for field in ('from', 'subject', 'content')))
                and (channel == 'Tất cả' or (item.get('to') == 'support') == (channel == 'Yêu cầu hỗ trợ'))]
    filtered.sort(key=_recent_key, reverse=order == 'Mới nhất')
    st.caption(f'{len(filtered)} / {len(scoped)} tin nhắn phù hợp · Thời gian hiển thị theo giờ Việt Nam.')
    with st.container(key='hn-admin-messages'):
        if not filtered:
            st.info('Chưa có tin nhắn phù hợp. Bạn có thể đổi từ khóa hoặc loại tin nhắn.')
        else:
            for number, item in enumerate(_pagination(filtered, 'admin_support')):
                with st.container(border=True, key=f'hn-admin-message-{number}'):
                    with st.container(horizontal=True, horizontal_alignment='distribute'):
                        st.badge('Yêu cầu hỗ trợ' if item.get('to') == 'support' else 'Gửi riêng cho bạn', color='gray')
                        st.caption(display_time(item.get('created_at')))
                    st.subheader(item.get('subject') or 'Tin nhắn không có tiêu đề', anchor=False)
                    st.caption('Người gửi · ' + str(item.get('from') or 'Chưa xác định'))
                    st.write(item.get('content') or 'Chưa có nội dung.')


def render_settings(records, *, navigate, refresh):
    admin_heading('Cài đặt dữ liệu', 'Đồng bộ danh mục và truy cập các công cụ quản trị hệ thống.', 'CÀI ĐẶT')
    stats = inventory_stats(records)
    kpis([('Tin trong phiên', len(records), 'inventory_2', 'Số tin đang được nạp trong phiên làm việc.'),
          ('Có ảnh đính kèm', len(records) - stats['missing_images'], 'photo_library', ''),
          ('Có thông tin liên hệ', len(records) - stats['missing_contact'], 'contact_phone', '')])
    with st.container(key='hn-admin-settings-grid'):
        with st.container(border=True, key='hn-admin-settings-card-data'):
            st.badge('Danh mục', icon=':material/database:', color='gray')
            st.subheader('Đồng bộ dữ liệu', anchor=False)
            st.write('Nạp lại danh mục để nhận các cập nhật từ phiên làm việc khác.')
            st.button('Tải lại dữ liệu', key='admin_settings_refresh', icon=':material/sync:', on_click=refresh, type='primary')
            st.caption('Thao tác này nạp lại dữ liệu đã lưu, không xóa hoặc khôi phục dữ liệu mẫu.')
        with st.container(border=True, key='hn-admin-settings-card-accounts'):
            st.badge('Tài khoản', icon=':material/manage_accounts:', color='gray')
            st.subheader('Tài khoản và phân quyền', anchor=False)
            st.write('Quản lý hồ sơ, khóa hoặc khôi phục quyền truy cập và xem nhật ký của tài khoản User.')
            st.button('Mở quản lý tài khoản User', key='settings_accounts', icon=':material/manage_accounts:', on_click=navigate, args=('accounts',))
            st.caption('Việc cấp vai trò Manager hoặc Admin thuộc quyền người vận hành hệ thống.')
        with st.container(border=True, key='hn-admin-settings-card-export'):
            st.badge('Dữ liệu', icon=':material/download:', color='gray')
            st.subheader('Xuất danh mục tin đăng', anchor=False)
            st.write('Chọn phạm vi trong báo cáo để tải CSV phục vụ đối chiếu và lưu trữ danh mục.')
            st.button('Mở báo cáo & xuất CSV', key='admin_settings_reports', icon=':material/assessment:', on_click=navigate, args=('reports',))
            st.caption('CSV danh mục không bao gồm tài khoản, mật khẩu hoặc nội dung hộp thư.')
        with st.container(border=True, key='hn-admin-settings-card-access'):
            st.badge('Truy cập', icon=':material/security:', color='gray')
            st.subheader('Phạm vi quản trị', anchor=False)
            st.write('Admin quản lý toàn bộ tin đăng, tài khoản User, báo cáo và hộp thư hỗ trợ. '
                     'Các thao tác xóa tin, khóa tài khoản và đặt lại mật khẩu có bước xác nhận riêng.')
            try:
                roles = st.secrets.get('roles', {})
            except FileNotFoundError:
                roles = {}
            url = roles.get('admin_console_url') if isinstance(roles, Mapping) else None
            if url:
                parsed = urlsplit(str(url))
                if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
                    st.link_button('Mở trang quản trị tài khoản OIDC', str(url), icon=':material/open_in_new:')
                else:
                    st.error('roles.admin_console_url phải là URL HTTPS hợp lệ.')
