"""Presentation and downloadable report for comparable asking-price analysis."""

from datetime import datetime, timedelta, timezone
from html import escape

import altair as alt
import pandas as pd
import streamlit as st


def prediction_time(value=None):
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00')) if value else datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    except (AttributeError, ValueError, TypeError):
        moment = datetime.now(timezone.utc)
    return moment.astimezone(timezone(timedelta(hours=7))).strftime('%d/%m/%Y %H:%M:%S')


def money(value):
    return f'{value:,.0f}'.replace(',', '.') + ' ₫'


def compact_money(value):
    for scale, suffix in ((1_000_000_000, 'tỷ ₫'), (1_000_000, 'triệu ₫')):
        if abs(value) >= scale:
            amount = f'{value / scale:.2f}'.rstrip('0').rstrip('.').replace('.', ',')
            return f'{amount} {suffix}'
    return money(value)


def render_valuation_header():
    with st.container(key='hn-value-heading'):
        st.html('<span class="hn-value-eyebrow">HOMENEST / PHÂN TÍCH TÀI SẢN</span>')
        st.title('Phân tích & định giá')
        st.caption('Hiểu mức giá tham khảo, xem vị trí và đối chiếu các bất động sản cùng khu vực.')
        st.html('<span class="hn-value-method-chip">Nguồn đối chiếu · Tin chào bán trong HomeNest</span>')
    st.html('<div class="hn-value-steps">' + ''.join(
        f'<div><span>{index:02d}</span><div><strong>{title}</strong><small>{description}</small></div></div>'
        for index, (title, description) in enumerate((
            ('Chọn bất động sản', 'Dùng tin có sẵn hoặc nhập thông tin'),
            ('Xác định vị trí', 'Tra địa chỉ hoặc nhập tọa độ'),
            ('Đối chiếu mức giá', 'Xem kết quả và các tin so sánh'),
        ), 1)
    ) + '</div>')


def _report(result, subject, map_info):
    location, kind, area = subject['location'], subject['type'], float(subject['area'])
    timestamp = result['_display_timestamp']
    point = (map_info or {}).get('point')
    map_section = ''
    if point:
        lat, lon = point
        map_section = (f'<h3>Vị trí trên bản đồ</h3><p>{escape(map_info.get("basis_label", "Vị trí tham khảo"))}'
                       f' · {lat:.6f}, {lon:.6f}</p><p><a href="{escape(map_info.get("osm_url", ""), quote=True)}">'
                       'Mở vị trí trên OpenStreetMap</a></p>'
                       '<p>Vị trí bản đồ không được dùng để điều chỉnh giá tham khảo.</p>')
    title = str(subject.get('title') or 'Bất động sản đang phân tích')
    return (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>HomeNest · Báo cáo giá tham khảo</title></head>'
        '<body style="font-family:Arial,sans-serif;max-width:850px;margin:40px auto;padding:24px;color:#172b3a;line-height:1.7">'
        '<p>HOMENEST · BÁO CÁO GIÁ CHÀO BÁN THAM KHẢO</p>'
        f'<h1>{escape(title)}</h1><p>{escape(location)} · {escape(kind)} · {area:g} m²</p>'
        f'<p>Phân tích lúc {timestamp} (UTC+7)</p><h2>{money(result["price"])}</h2>'
        f'<p>Phạm vi theo mẫu: {money(result["low"])} – {money(result["high"])}.</p>'
        f'<p>Trung vị đơn giá: {money(result["unit_price"])}/m² · {len(result["samples"])} tin so sánh.</p>'
        + map_section + '<h3>Tin được dùng để đối chiếu</h3><ul>' + ''.join(
            f'<li>{escape(str(prop.get("title", "")))} · {money(float(prop["price"]))} · {float(prop["area"]):g} m²</li>'
            for prop in result['samples']
        ) + '</ul><h3>Phương pháp và phạm vi sử dụng</h3>'
        '<p>Giá tham khảo = trung vị đơn giá tin chào bán cùng khu vực và cùng loại × diện tích. '
        'Đây không phải giá giao dịch đã xác nhận hoặc chứng thư thẩm định. '
        'Pháp lý, vị trí chi tiết, chất lượng và các thông tin bổ sung chưa được dùng để điều chỉnh giá. '
        'Phạm vi thấp nhất–cao nhất chỉ phản ánh mẫu, không phải khoảng tin cậy thống kê.</p></body></html>'
    )


def render_result_summary(result, subject, map_info):
    with st.container(border=True, key='hn-value-result'):
        st.html('<span class="hn-value-result-label">GIÁ CHÀO BÁN THAM KHẢO</span>')
        amount, unit = compact_money(result['price']).split(' ', 1)
        st.html(f'<div class="hn-value-price"><strong>{escape(amount)}</strong><span>{escape(unit)}</span></div>')
        title = str(subject.get('title') or 'Bất động sản đang phân tích')
        st.html('<div class="hn-value-context">'
                f'<strong>{escape(title)}</strong><span>{escape(subject["location"])} · '
                f'{escape(subject["type"])} · {float(subject["area"]):g} m²</span></div>')
        span = result['high'] - result['low']
        position = (result['price'] - result['low']) / span * 100 if span else 50
        position = min(100, max(0, position))
        st.html('<div class="hn-value-range"><div class="hn-value-range-track">'
                f'<div class="hn-value-range-fill" style="width:{position:.2f}%"></div>'
                f'<span class="hn-value-range-marker" style="left:{position:.2f}%"></span></div>'
                '<div class="hn-value-range-labels">'
                f'<span>Thấp nhất theo mẫu<strong>{escape(compact_money(result["low"]))}</strong></span>'
                f'<span>Cao nhất theo mẫu<strong>{escape(compact_money(result["high"]))}</strong></span>'
                '</div></div>')
        st.caption(f'{len(result["samples"])} tin so sánh · Trung vị đơn giá × {float(subject["area"]):g} m²')
        st.download_button('Tải báo cáo phân tích', _report(result, subject, map_info).encode('utf-8'),
                           'homenest-dinh-gia.html', 'text/html', icon=':material/download:',
                           key='valuation_report', width='stretch')
        st.caption(f'Phân tích lúc {result["_display_timestamp"]} (UTC+7)')


def render_valuation_analysis(result, subject):
    samples, area = result['samples'], float(subject['area'])
    with st.container(horizontal=True, key='hn-value-metrics'):
        st.metric('Giá chào bán tham khảo', compact_money(result['price']),
                  help=f'Giá trị đầy đủ: {money(result["price"])}. Trung vị đơn giá × diện tích.',
                  icon=':material/real_estate_agent:', border=True)
        st.metric('Trung vị đơn giá / m²', compact_money(result['unit_price']),
                  help=f'Giá trị đầy đủ: {money(result["unit_price"])}/m².',
                  icon=':material/square_foot:', border=True)
        st.metric('Tin so sánh hợp lệ', len(samples),
                  help='Tin bán cùng khu vực và cùng loại; đã loại giá, diện tích không hợp lệ và tin trùng.',
                  icon=':material/dataset:', border=True)
    st.html('<p class="hn-value-quality">Kết quả từ giá chào bán trong danh mục; '
            'phạm vi theo mẫu không phải khoảng tin cậy thống kê hoặc kết quả thẩm định.</p>')
    if len(samples) < 3:
        st.warning(f'Chỉ có {len(samples)} tin so sánh. Dữ liệu còn ít để đại diện cho mức giá khu vực.',
                   icon=':material/info:')
    latest = max((str(prop.get('created_at', '')) for prop in samples), default='')
    st.caption(f'{len(samples)} mẫu hợp lệ · {result.get("skipped", 0)} mẫu bị loại · '
               f'Tin mới nhất trong mẫu: {latest or "Chưa có thông tin"}')
    with st.container(key='hn-value-analysis'):
        if len(samples) > 1:
            with st.container(border=True, key='hn-value-chart'):
                st.subheader('So sánh đơn giá')
                st.caption('Giá chào bán trên mỗi m² · Đường nét đứt là trung vị của mẫu')
                frame = pd.DataFrame([
                    {'Tin đăng': f'{index + 1:02d}. {prop.get("title", "Chưa có tiêu đề")}',
                     'Đơn giá (triệu ₫/m²)': prop['unit_price'] / 1_000_000,
                     'Diện tích (m²)': float(prop['area']), 'Giá chào bán': money(float(prop['price']))}
                    for index, prop in enumerate(samples)
                ])
                bars = alt.Chart(frame).mark_bar(color='#c65d28', cornerRadiusEnd=5, size=22).encode(
                    x=alt.X('Đơn giá (triệu ₫/m²):Q', title='Triệu ₫ / m²'),
                    y=alt.Y('Tin đăng:N', sort='-x', title=None, axis=alt.Axis(labelLimit=190)),
                    tooltip=['Tin đăng:N', alt.Tooltip('Đơn giá (triệu ₫/m²):Q', format='.2f'),
                             'Diện tích (m²):Q', 'Giá chào bán:N'],
                )
                reference = alt.Chart(pd.DataFrame({'Trung vị': [result['unit_price'] / 1_000_000]})).mark_rule(
                    color='#547081', strokeDash=[5, 4],
                ).encode(x='Trung vị:Q', tooltip=[alt.Tooltip('Trung vị:Q', format='.2f')])
                st.altair_chart((bars + reference).properties(height=min(440, max(230, len(samples) * 42))),
                               width='stretch')
                st.caption(f'Với {area:g} m², phạm vi theo mẫu: {money(result["low"])} – {money(result["high"])}.')
        with st.container(border=True, key='hn-value-table'):
            st.subheader('Các bất động sản dùng để đối chiếu')
            st.caption(f'{len(samples)} tin bán · {subject["location"]} · {subject["type"]}')
            rows = [{'Tin đăng': prop.get('title', ''), 'Khu vực': prop['location'],
                     'Diện tích (m²)': float(prop['area']), 'Giá chào bán (₫)': float(prop['price']),
                     'Đơn giá (₫/m²)': prop['unit_price'], 'Ngày đăng': prop.get('created_at', '')}
                    for prop in samples]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch', column_config={
                'Tin đăng': st.column_config.TextColumn(width='large'),
                'Diện tích (m²)': st.column_config.NumberColumn(format='%.1f'),
                'Giá chào bán (₫)': st.column_config.NumberColumn(format='%,.0f'),
                'Đơn giá (₫/m²)': st.column_config.NumberColumn(format='%,.0f'),
            })
    with st.expander('Phương pháp và phạm vi sử dụng', icon=':material/calculate:'):
        st.markdown('**Giá tham khảo = trung vị đơn giá các tin × diện tích cần định giá**')
        st.write('Chỉ chọn tin chào bán cùng khu vực, cùng loại và có giá, diện tích hợp lệ. '
                 'Các tin trùng nội dung, giá, diện tích và liên hệ được loại khỏi mẫu.')
        st.write('Vị trí trên bản đồ và các thông tin bổ sung giúp bạn xem xét tài sản; '
                 'chưa được dùng để điều chỉnh phép tính. Dữ liệu hiện chưa có lịch sử giá để dự báo xu hướng.')
        st.caption('Giá chào có thể khác giá giao dịch. Khoảng thấp nhất–cao nhất chỉ phản ánh mẫu quan sát; '
                   'không phải khoảng tin cậy thống kê hoặc chứng thư thẩm định.')
