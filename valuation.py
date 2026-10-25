"""Comparable asking-price analysis and independent property-location controls."""

import json
import math
from io import BytesIO
from pathlib import Path
from time import monotonic

import pandas as pd
import streamlit as st

from csv_export import spreadsheet_safe_csv
from prediction import validate_subject
from estimator import estimate, location_key
from api_client import ValuationAPIError, model_info, predict, predict_batch
from session_auth import api_bearer_token
from property_map import reset_map_context, render_property_map
from valuation_view import (money, compact_money, prediction_time, render_valuation_header,
                            render_result_summary, render_valuation_analysis)


def render_batch(properties):
    if st.session_state.get('role') not in {'Manager', 'Admin'}:
        return
    if st.session_state.pop('_open_batch_from_services', False):
        st.session_state.valuation_batch_expander = True
    with st.expander('Định giá hàng loạt bằng CSV', icon=':material/upload_file:',
                     key='valuation_batch_expander', on_change='rerun'):
        st.caption('Dành cho quản lý và quản trị viên · tối đa 500 dòng. '
                   'Mỗi dòng giữ nguyên thứ tự và ID, kể cả khi lỗi.')
        st.write('Cột bắt buộc: location (khu vực), type (loại), area (m²). '
                 'Khu vực và loại cần khớp danh mục hiện có. ID là cột tùy chọn.')
        template = pd.DataFrame([{'id': 'BDS-001', 'location': properties[0].get('location', '') if properties else '',
                                  'type': properties[0].get('type', 'Căn hộ') if properties else 'Căn hộ', 'area': 85}])
        st.download_button('Tải CSV mẫu', spreadsheet_safe_csv(template),
                           'mau_dinh_gia.csv', 'text/csv', key='batch_template')
        upload = st.file_uploader('Danh sách bất động sản CSV', type=['csv'], key='batch_upload')
        # Clear stale results whenever the file or source listings change.
        signature = (upload.getvalue() if upload else None, repr(properties))
        if st.session_state.get('_batch_signature') != signature:
            st.session_state.pop('batch_results', None)
            st.session_state.pop('batch_response_time_ms', None)
            st.session_state['_batch_signature'] = signature
        if st.button('Định giá danh sách', key='batch_submit', disabled=upload is None, type='primary'):
            try:
                if upload.size > 2_000_000:
                    raise ValueError('Tệp CSV không được vượt quá 2 MB.')
                frame = pd.read_csv(BytesIO(upload.getvalue()), dtype=str, keep_default_na=False, encoding='utf-8-sig')
                if not {'location', 'type', 'area'}.issubset(frame.columns):
                    raise ValueError('CSV cần đủ các cột location, type, area.')
                if not 1 <= len(frame) <= 500:
                    raise ValueError('CSV cần từ 1 đến 500 dòng dữ liệu.')
                with st.spinner('Đang định giá từng dòng…'):
                    response = predict_batch(frame.to_dict('records'), api_bearer_token())
                    rows = response['results']
                    timestamp = prediction_time()
                    for row in rows:
                        row['Ngày định giá (UTC+7)'] = timestamp
                    st.session_state.batch_results = rows
                    st.session_state.batch_response_time_ms = response['_client_response_time_ms']
            except (ValueError, UnicodeError, pd.errors.ParserError, OSError,
                    RuntimeError, ValuationAPIError) as error:
                st.error(f'Chưa xử lý được CSV: {error} Hãy sửa tệp và thử lại.')
        if rows := st.session_state.get('batch_results'):
            succeeded = sum(row['Trạng thái'] == 'Thành công' for row in rows)
            st.success(f'Đã xử lý {len(rows)} dòng: {succeeded} thành công, {len(rows) - succeeded} chưa có kết quả.')
            st.caption(f"Thời gian phản hồi API · "
                       f"{st.session_state.get('batch_response_time_ms', '—')} ms")
            frame = pd.DataFrame(rows)
            st.dataframe(frame, hide_index=True, width='stretch')
            st.download_button('Tải kết quả CSV', spreadsheet_safe_csv(frame),
                               'ket_qua_dinh_gia.csv', 'text/csv', key='batch_download')
            st.caption('Khoảng giá là ước lượng heuristic từ mẫu, chưa hiệu chuẩn; không cam kết sai số. '
                       'Kết quả chỉ được giữ trong phiên, không lưu lịch sử trên server.')



def _render_model_controls(properties):
    if st.session_state.get('role') not in {'Manager', 'Admin'}:
        return
    with st.container(key='hn-value-advanced'):
        with st.expander('Thông tin phương pháp định giá', icon=':material/calculate:'):
            st.caption('Phương pháp so sánh theo trung vị đơn giá, không phải mô hình ML được huấn luyện.')
            if st.button('Tải thông tin phương pháp', key='model_info_load', icon=':material/model_training:'):
                st.session_state.pop('model_info', None)
                try:
                    st.session_state.model_info = model_info(api_bearer_token())
                except (RuntimeError, ValuationAPIError) as error:
                    st.error(f'Chưa tải được thông tin phương pháp: {error}')
            if info := st.session_state.get('model_info'):
                st.markdown(f"**{info['name']} · {info['version']}**")
                st.caption(f"Trạng thái · {'Sẵn sàng' if info['ready'] else 'Chưa sẵn sàng'}")
                st.caption(f"Trường sử dụng · {', '.join(info['features_used'])}")
                for limitation in info['limitations']:
                    st.caption(f'• {limitation}')
        render_batch(properties)


def _valid_number(value, *, integer=False, default=None):
    try:
        number = float(value)
        if isinstance(value, bool) or not math.isfinite(number) or number < 0:
            return default
        if integer and not number.is_integer():
            return default
        return int(number) if integer else number
    except (TypeError, ValueError, OverflowError):
        return default


def _select_valuation_listing(properties):
    selected = st.session_state.get('valuation_listing')
    record = next((prop for prop in properties if prop.get('id') == selected), None)
    st.session_state.pop('_valuation_cached_result', None)
    if record:
        st.session_state.valuation_location = str(record.get('location', '')).strip()
        st.session_state.valuation_type = record.get('type') or 'Khác'
        st.session_state.valuation_area = _valid_number(record.get('area'), default=0.0)
        st.session_state._valuation_previous_location = st.session_state.valuation_location
    for field in ('bedrooms', 'bathrooms', 'floors', 'frontage', 'road_width'):
        st.session_state[f'valuation_{field}'] = _valid_number(
            record.get(field) if record else None, integer=field in {'bedrooms', 'bathrooms', 'floors'})
    for target, field in (('title', 'title'), ('project', 'project_hint'), ('description', 'description')):
        st.session_state[f'valuation_{target}'] = str(record.get(field) or '') if record else ''
    legal = record.get('legal') if record else None
    direction = record.get('direction') if record else None
    st.session_state.valuation_legal = legal if legal in _LEGAL_OPTIONS else 'Chưa cung cấp'
    st.session_state.valuation_direction = direction if direction in _DIRECTION_OPTIONS else 'Chưa cung cấp'
    reset_map_context(record, st.session_state.get('valuation_location', ''))


def _valuation_location_changed():
    # Editing the area of comparison must not retain a different listing's pin.
    st.session_state.valuation_listing = None
    reset_map_context(location=st.session_state.get('valuation_location', ''))


def _refresh_valuation():
    st.session_state.pop('_valuation_cached_result', None)


_LEGAL_OPTIONS = ['Chưa cung cấp', 'Sổ đỏ / sổ hồng', 'Hợp đồng mua bán', 'Khác']
_DIRECTION_OPTIONS = ['Chưa cung cấp', 'Đông', 'Tây', 'Nam', 'Bắc', 'Đông Bắc', 'Đông Nam', 'Tây Bắc', 'Tây Nam']


def _render_valuation_inputs(properties):
    locations = {}
    for prop in properties:
        location = str(prop.get('location') or '').strip()
        if location:
            locations.setdefault(location_key(location), location)
    listings = {prop['id']: prop for prop in properties}
    selected = st.session_state.get('valuation_listing')
    if selected is not None and selected not in listings:
        st.session_state.valuation_listing = None
        _select_valuation_listing(properties)
    with st.container(border=True, key='valuation_inputs'):
        st.subheader('Thông tin bất động sản', anchor=False)
        selected = st.selectbox(
            'Chọn tin có sẵn hoặc tự nhập', [None, *listings],
            format_func=lambda value: 'Nhập thông tin mới' if value is None else
                f"{listings[value].get('title', 'Tin đăng')} · {listings[value].get('location', '')}",
            key='valuation_listing', on_change=_select_valuation_listing,
            args=(properties,), persist_state='session',
        )
        record = listings.get(selected)
        if not locations:
            st.info('Chưa có dữ liệu so sánh. Thêm tin có khu vực, giá bán và diện tích để bắt đầu.',
                    icon=':material/dataset:')
            return None, record
        options = sorted(locations.values())
        current = st.session_state.get('valuation_location')
        if current not in (None, *options):
            canonical = locations.get(location_key(current))
            st.session_state.valuation_location = canonical or options[0]
        location = st.selectbox('Khu vực', options, key='valuation_location',
                                on_change=_valuation_location_changed, persist_state='session')
        local_types = sorted({prop['type'] for prop in properties
                              if prop.get('type') and prop.get('status') == 'Bán'
                              and location_key(prop.get('location', '')) == location_key(location)})
        types = sorted({'Căn hộ', 'Nhà phố', 'Đất nền', 'Biệt thự', 'Khác'} |
                       {prop['type'] for prop in properties if prop.get('type')})
        if st.session_state.get('_valuation_previous_location') != location:
            if local_types and st.session_state.get('valuation_type') not in local_types:
                st.session_state.valuation_type = local_types[0]
            st.session_state._valuation_previous_location = location
        if st.session_state.get('valuation_type') not in (None, *types):
            st.session_state.valuation_type = types[0]
        st.session_state.setdefault('valuation_area', 85.0)
        with st.container(key='hn-value-basic-fields'):
            kind_column, area_column = st.columns([1.3, 1], gap='small')
            property_type = kind_column.selectbox('Loại bất động sản', types,
                                                  key='valuation_type', persist_state='session')
            area = area_column.number_input('Diện tích (m²)', min_value=0.0, value=None,
                                             step=1.0, key='valuation_area', persist_state='session')
        subject = {'location': location, 'type': property_type, 'area': area}
        with st.expander('Thông tin bổ sung của bất động sản', icon=':material/tune:'):
            st.caption('Thông tin bổ sung để bạn đối chiếu; chưa được dùng để điều chỉnh giá tham khảo.')
            subject['title'] = st.text_input('Tiêu đề bất động sản', key='valuation_title', persist_state='session')
            columns = st.columns(3, gap='small')
            for column, field, label in zip(columns, ('bedrooms', 'bathrooms', 'floors'),
                                             ('Phòng ngủ', 'Phòng tắm', 'Số tầng')):
                subject[field] = column.number_input(label, min_value=0, value=None, step=1,
                                                     key=f'valuation_{field}', persist_state='session')
            columns = st.columns(2, gap='small')
            for column, field, label in zip(columns, ('frontage', 'road_width'),
                                             ('Mặt tiền (m)', 'Độ rộng đường (m)')):
                subject[field] = column.number_input(label, min_value=0.0, value=None, step=0.5,
                                                     key=f'valuation_{field}', persist_state='session')
                if field in (errors := validate_subject(subject)):
                    column.error(errors[field])
            columns = st.columns(2, gap='small')
            subject['legal'] = columns[0].selectbox('Pháp lý', _LEGAL_OPTIONS,
                                                   key='valuation_legal', persist_state='session')
            subject['direction'] = columns[1].selectbox('Hướng', _DIRECTION_OPTIONS,
                                                       key='valuation_direction', persist_state='session')
            subject['project_hint'] = st.text_input('Dự án', key='valuation_project', persist_state='session')
            subject['description'] = st.text_area('Mô tả', key='valuation_description', persist_state='session')
        st.button('Cập nhật phân tích', icon=':material/refresh:', type='primary',
                  key='valuation_refresh', on_click=_refresh_valuation, width='stretch')
        st.caption('Kết quả cập nhật theo thông tin bạn chọn. Các thay đổi ở đây không sửa tin đăng.')
        return subject, record


def _load_valuation_result(subject, properties):
    token = api_bearer_token()
    signature = (json.dumps(subject, sort_keys=True, ensure_ascii=False, allow_nan=False),
                 repr(properties), token)
    cached = st.session_state.get('_valuation_cached_result')
    if cached and cached['signature'] == signature and monotonic() - cached['created'] < 60:
        return cached['result']
    result = predict(subject, token)
    if not isinstance(result.get('samples'), list):
        raise ValuationAPIError('Dịch vụ định giá trả về kết quả chưa hợp lệ. Hãy thử cập nhật phân tích.')
    if result['samples']:
        for field in ('price', 'low', 'high', 'unit_price'):
            value = result.get(field)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValuationAPIError('Dịch vụ định giá trả về mức giá chưa hợp lệ. Hãy thử cập nhật phân tích.')
    result['_display_timestamp'] = prediction_time(result.get('generated_at'))
    st.session_state._valuation_cached_result = {'signature': signature, 'created': monotonic(), 'result': result}
    return result


def render_valuation(properties):
    st.html('<style>' + (Path(__file__).parent / 'assets/valuation.css').read_text(encoding='utf-8') + '</style>')
    with st.container(key='hn-value'):
        render_valuation_header()
        _render_model_controls(properties)
        with st.container(key='hn-value-workspace'):
            left, right = st.columns([1.12, 1], gap='medium')
            with left, st.container(key='hn-value-left'):
                subject, record = _render_valuation_inputs(properties)
                result_slot = st.container(key='hn-value-result-slot')
            with right, st.container(key='hn-value-right'):
                map_info = render_property_map(subject['location'] if subject else '', record)
        if subject is None:
            return
        errors = validate_subject(subject)
        if errors:
            with result_slot:
                for field, message in errors.items():
                    if field not in {'frontage', 'road_width'}:
                        st.error(message)
            return
        try:
            with result_slot, st.spinner('Đang phân tích các tin so sánh…'):
                result = _load_valuation_result(subject, properties)
        except (RuntimeError, ValuationAPIError, ValueError) as error:
            st.session_state.pop('_valuation_cached_result', None)
            with result_slot:
                st.error(str(error), icon=':material/cloud_off:')
            return
        if not result['samples']:
            with result_slot:
                st.info('Chưa có tin bán hợp lệ cho loại bất động sản này tại khu vực đã chọn. '
                        'Chọn khu vực hoặc loại khác để xem kết quả.', icon=':material/search_off:')
            return
        with result_slot:
            render_result_summary(result, subject, map_info)
        render_valuation_analysis(result, subject)
