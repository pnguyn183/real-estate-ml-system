"""Shared presentation and data formatting for the HomeNest admin workspace."""

from datetime import datetime, timedelta, timezone
from html import escape
import math
import unicodedata

import pandas as pd
import streamlit as st


LOCAL_TIME = timezone(timedelta(hours=7))


def admin_heading(title, description, section='TỔNG QUAN'):
    with st.container(key='hn-admin-heading'):
        st.html('<div class="hn-admin-eyebrow">HOMENEST <span>/</span> QUẢN TRỊ '
                f'<span>/</span> {escape(section)}</div>')
        st.title(title, anchor=False)
        st.caption(description)


def normalized(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold().replace('đ', 'd'))
    return ' '.join(''.join(char for char in text if not unicodedata.combining(char)).split())


def parsed_time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (moment.replace(tzinfo=LOCAL_TIME) if moment.tzinfo is None
                else moment.astimezone(LOCAL_TIME))
    except (TypeError, ValueError):
        return None


def display_time(value):
    moment = parsed_time(value)
    return moment.strftime('%d/%m/%Y · %H:%M') if moment else 'Chưa có thông tin'


def positive(value):
    try:
        number = float(value)
        return number if not isinstance(value, bool) and math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def money(value, compact=True):
    amount = float(value or 0)
    if compact:
        for divisor, unit in ((1e9, 'tỷ ₫'), (1e6, 'triệu ₫')):
            if abs(amount) >= divisor:
                return f'{amount / divisor:.2f}'.rstrip('0').rstrip('.').replace('.', ',') + ' ' + unit
    return f'{amount:,.0f}'.replace(',', '.') + ' ₫'


def select_filter(label, options, key):
    """Persistent filters retain a valid option, without default/state conflicts."""
    if st.session_state.get(key) not in options:
        st.session_state[key] = options[0]
    return st.selectbox(label, options, index=None, key=key, persist_state='session')


def listing_table(records, *, key=None):
    rows = [{'ID': prop['id'], 'Tin đăng': prop.get('title', ''),
             'Khu vực': prop.get('location', ''), 'Loại': prop.get('type', ''),
             'Nhu cầu': prop.get('status', ''), 'Giá chào (₫)': prop.get('price', 0),
             'Diện tích (m²)': prop.get('area', 0),
             'Người đăng': prop.get('user_id') or 'Chưa xác định',
             'Ngày đăng': display_time(prop.get('created_at'))} for prop in records]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch', key=key,
                 column_config={
                     'Tin đăng': st.column_config.TextColumn(width='large'),
                     'Giá chào (₫)': st.column_config.NumberColumn(format='%,.0f'),
                     'Diện tích (m²)': st.column_config.NumberColumn(format='%.1f'),
                 })


def kpis(items, key='hn-admin-kpis'):
    with st.container(horizontal=True, key=key):
        for item in items:
            label, value, symbol, help_text = item
            st.metric(label, value, border=True, icon=f':material/{symbol}:', help=help_text or None)
