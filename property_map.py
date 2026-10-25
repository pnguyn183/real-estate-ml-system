"""Location lookup and a map panel for property valuation.

Address lookups run only after an explicit search. Photon returns suggestions,
not evidence that a property occupies a particular parcel or building.
"""

import math
import os
import threading
import time
from collections.abc import Mapping
from urllib.parse import urlencode, urlsplit

import requests
import streamlit as st


DEFAULT_GEOCODER_URL = 'https://photon.komoot.io/api/'
MAX_RESULTS = 5
MERCATOR_LIMIT = 85.05112878
VIETNAM_BBOX = (102.0, 8.0, 110.0, 24.0)


class GeocodingError(RuntimeError):
    """A recoverable location-search error safe to show to the user."""


def validate_coordinates(latitude, longitude):
    """Return a finite (latitude, longitude) pair, or None; zero is valid."""
    if (latitude is None or longitude is None
            or isinstance(latitude, bool) or isinstance(longitude, bool)):
        return None
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError, OverflowError):
        return None
    if (not math.isfinite(latitude) or not math.isfinite(longitude)
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        return None
    return latitude, longitude


def listing_coordinates(record):
    """Read complete coordinate pairs without mixing unrelated field families."""
    if not isinstance(record, Mapping):
        return None
    for latitude_key, longitude_key in (
        ('latitude', 'longitude'), ('lat', 'lon'), ('lat', 'lng'),
    ):
        if latitude_key in record and longitude_key in record:
            point = validate_coordinates(record[latitude_key], record[longitude_key])
            if point is not None:
                return point
    return None


def _number(value):
    return f'{value:.7f}'.rstrip('0').rstrip('.') or '0'


def _point_bbox(latitude, longitude, radius):
    # The OSM slippy map cannot show the poles. Clamp only the viewport, while
    # retaining the supplied geographic coordinate in the marker and links.
    center_lat = max(-MERCATOR_LIMIT, min(MERCATOR_LIMIT, latitude))
    west = max(-180.0, longitude - radius)
    east = min(180.0, longitude + radius)
    south = max(-MERCATOR_LIMIT, center_lat - radius)
    north = min(MERCATOR_LIMIT, center_lat + radius)
    return west, south, east, north


def osm_embed_url(latitude=None, longitude=None, *, area=False):
    """Build a fixed-origin embed URL, with no invented marker on the overview."""
    point = validate_coordinates(latitude, longitude)
    params = {'bbox': ','.join(_number(n) for n in VIETNAM_BBOX), 'layer': 'mapnik'}
    if point is not None:
        latitude, longitude = point
        params['bbox'] = ','.join(_number(n) for n in _point_bbox(
            latitude, longitude, 0.05 if area else 0.008))
        params['marker'] = f'{_number(latitude)},{_number(longitude)}'
    return 'https://www.openstreetmap.org/export/embed.html?' + urlencode(params)


def google_maps_url(latitude=None, longitude=None, query=''):
    """An official Maps search URL; all address text stays in its query value."""
    point = validate_coordinates(latitude, longitude)
    search = (f'{_number(point[0])},{_number(point[1])}' if point is not None
              else str(query or '').strip()[:300] or 'Việt Nam')
    return 'https://www.google.com/maps/search/?' + urlencode({'api': '1', 'query': search})


def osm_map_url(latitude=None, longitude=None):
    point = validate_coordinates(latitude, longitude)
    if point is None:
        return 'https://www.openstreetmap.org/#map=5/16/106'
    latitude, longitude = point
    return ('https://www.openstreetmap.org/?' + urlencode({
        'mlat': _number(latitude), 'mlon': _number(longitude),
    }) + f'#map=16/{_number(latitude)}/{_number(longitude)}')


def _geocoder_endpoint():
    # Configuration is server owned, never derived from a form or URL argument.
    endpoint = os.environ.get('HOMENEST_GEOCODER_URL', DEFAULT_GEOCODER_URL).strip()
    try:
        parsed = urlsplit(endpoint)
    except ValueError as error:
        raise GeocodingError('Tra cứu địa chỉ hiện chưa sẵn sàng. Bạn có thể nhập tọa độ hoặc mở Google Maps.') from error
    if (parsed.scheme not in {'https', 'http'} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.hostname.lower() == 'nominatim.openstreetmap.org'):
        raise GeocodingError('Tra cứu địa chỉ hiện chưa sẵn sàng. Bạn có thể nhập tọa độ hoặc mở Google Maps.')
    return endpoint


@st.cache_resource
def _geocoder_gate():
    """One shared request gate per app process, across all user sessions."""
    return {'lock': threading.Lock(), 'last_started': None}


def _text(value):
    return str(value).strip()[:120] if isinstance(value, (str, int, float)) else ''


def _parse_candidates(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('features'), list):
        raise GeocodingError('Dịch vụ bản đồ trả về dữ liệu chưa hợp lệ. Vui lòng thử lại sau.')
    candidates, seen = [], set()
    coarse_types = {
        'country', 'state', 'region', 'province', 'city', 'town', 'village',
        'suburb', 'quarter', 'neighbourhood', 'district', 'county', 'street',
        'road', 'residential', 'administrative', 'locality', 'municipality', 'hamlet',
    }
    for feature in payload['features'][:50]:
        if not isinstance(feature, dict):
            continue
        geometry, props = feature.get('geometry'), feature.get('properties')
        if not isinstance(geometry, dict) or not isinstance(props, dict):
            continue
        coords = geometry.get('coordinates')
        if (geometry.get('type') != 'Point' or not isinstance(coords, (list, tuple))
                or len(coords) < 2):
            continue
        # GeoJSON uses longitude first.
        point = validate_coordinates(coords[1], coords[0])
        if point is None:
            continue
        address = ' '.join(filter(None, [_text(props.get('housenumber')),
                                        _text(props.get('street'))]))
        parts = [address] if address else []
        for field in ('name', 'district', 'city', 'county', 'state', 'country'):
            value = _text(props.get(field))
            if value and value not in parts:
                parts.append(value)
        label = ', '.join(parts)[:500] or f'{_number(point[0])}, {_number(point[1])}'
        signature = (point, label)
        if signature in seen:
            continue
        seen.add(signature)
        has_number = bool(_text(props.get('housenumber')))
        area = (not has_number and (_text(props.get('osm_value')) in coarse_types
                or props.get('osm_key') == 'highway'
                or _text(props.get('type')) in coarse_types))
        candidates.append({'latitude': point[0], 'longitude': point[1],
                           'label': label, 'kind': 'area' if area else 'address'})
        if len(candidates) == MAX_RESULTS:
            break
    return candidates


@st.cache_data(ttl=86400, max_entries=256, show_spinner=False)
def _geocode_cached(query, endpoint):
    gate = _geocoder_gate()
    try:
        # Hold the gate through the request, so both simultaneous users and
        # repeated searches obey the same one-request-per-second maximum.
        with gate['lock']:
            if gate['last_started'] is not None:
                delay = 1.0 - (time.monotonic() - gate['last_started'])
                if delay > 0:
                    time.sleep(delay)
            gate['last_started'] = time.monotonic()
            response = requests.get(
                # Use the server's local-name default. The public instance
                # rejects lang=vi even though Vietnamese places are searchable.
                endpoint, params={'q': query, 'limit': MAX_RESULTS, 'countrycode': 'VN'},
                headers={'User-Agent': 'HomeNest/1.0 (property-location lookup)',
                         'Accept': 'application/json'}, timeout=(3, 8),
                allow_redirects=False,
            )
            if response.status_code == 429:
                raise GeocodingError('Tra cứu đang có nhiều lượt truy cập. Bạn hãy thử lại sau ít phút hoặc nhập tọa độ.')
            if 300 <= response.status_code < 400:
                raise GeocodingError('Dịch vụ tra cứu tạm thời chưa sẵn sàng. Vui lòng thử lại sau.')
            response.raise_for_status()
            payload = response.json()
    except requests.Timeout as error:
        raise GeocodingError('Tra cứu địa chỉ mất nhiều thời gian hơn dự kiến. Bạn hãy thử lại hoặc nhập tọa độ.') from error
    except requests.RequestException as error:
        raise GeocodingError('Chưa kết nối được dịch vụ bản đồ. Bạn có thể thử lại, nhập tọa độ hoặc mở Google Maps.') from error
    except (ValueError, TypeError) as error:
        raise GeocodingError('Dịch vụ bản đồ trả về dữ liệu chưa hợp lệ. Vui lòng thử lại sau.') from error
    return _parse_candidates(payload)


def geocode_address(query):
    """Look up at most five suggestions; cache successful and empty lookups."""
    query = ' '.join(str(query or '').split())
    if not 3 <= len(query) <= 300:
        raise GeocodingError('Nhập địa chỉ từ 3 đến 300 ký tự, kèm phường, quận hoặc tỉnh thành để tra cứu.')
    return _geocode_cached(query, _geocoder_endpoint())


def _context_identity(record, location):
    record = record if isinstance(record, Mapping) else {}
    return (str(record.get('id', '')), str(record.get('title', '')),
            str(record.get('address', '')), str(record.get('location', '')),
            str(location or ''), listing_coordinates(record))


def clear_map_point():
    """Widget callback: clear a prior selection without changing the address."""
    st.session_state['valuation_latitude'] = None
    st.session_state['valuation_longitude'] = None
    st.session_state['valuation_map_candidate'] = None
    st.session_state['_valuation_map_candidates'] = []
    st.session_state['_valuation_map_source'] = None
    st.session_state.pop('_valuation_map_error', None)
    st.session_state.pop('_valuation_map_searched', None)


def reset_map_context(record=None, location=''):
    """Reset stale pins before map widgets render or inside upstream callbacks."""
    record = record if isinstance(record, Mapping) else {}
    clear_map_point()
    address = str(record.get('address') or record.get('location') or location or '').strip()
    st.session_state['valuation_map_address'] = address[:300]
    st.session_state['_valuation_map_context'] = _context_identity(record, location)
    if point := listing_coordinates(record):
        st.session_state['valuation_latitude'], st.session_state['valuation_longitude'] = point
        st.session_state['_valuation_map_source'] = 'record'


def _manual_coordinate_changed():
    st.session_state['_valuation_map_source'] = 'manual'
    st.session_state['valuation_map_candidate'] = None
    # Preserve the list, allowing the user to explicitly pick it again.


def _candidate_changed():
    index = st.session_state.get('valuation_map_candidate')
    candidates = st.session_state.get('_valuation_map_candidates', [])
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(candidates):
        st.session_state['valuation_latitude'] = None
        st.session_state['valuation_longitude'] = None
        st.session_state['_valuation_map_source'] = 'geocode'


def _search_address():
    # Callback executes before number inputs/selectbox are instantiated again.
    clear_map_point()
    try:
        st.session_state['_valuation_map_candidates'] = geocode_address(
            st.session_state.get('valuation_map_address', ''))
        st.session_state['_valuation_map_searched'] = True
    except GeocodingError as error:
        st.session_state['_valuation_map_error'] = str(error)


def render_property_map(location, record=None):
    """Render independent location controls and return the currently shown pin."""
    from property_map_view import render_map_html

    if st.session_state.get('_valuation_map_context') != _context_identity(record, location):
        reset_map_context(record, location)
    with st.container(border=True, key='hn-value-map'):
        st.subheader('Vị trí bất động sản', anchor=False)
        st.caption('Tra cứu địa chỉ, chọn gợi ý phù hợp hoặc nhập tọa độ để xác định vị trí trên bản đồ.')
        address = st.text_input('Địa chỉ hoặc khu vực', key='valuation_map_address',
                                max_chars=300, on_change=clear_map_point,
                                placeholder='Số nhà, tên đường, phường / xã, tỉnh / thành',
                                persist_state='session')
        st.button('Tìm trên bản đồ', key='valuation_map_search',
                  icon=':material/search:', on_click=_search_address, width='stretch')
        if error := st.session_state.get('_valuation_map_error'):
            st.warning(error)
        candidates = st.session_state.get('_valuation_map_candidates', [])
        if candidates:
            st.selectbox('Chọn vị trí gợi ý', options=list(range(len(candidates))),
                         index=None, key='valuation_map_candidate',
                         format_func=lambda index: candidates[index]['label'],
                         placeholder='Chọn kết quả phù hợp với bất động sản',
                         on_change=_candidate_changed, persist_state='session')
        elif st.session_state.get('_valuation_map_searched'):
            st.info('Chưa tìm thấy vị trí phù hợp. Hãy thêm tên đường, phường / xã hoặc nhập tọa độ.')
        with st.expander('Nhập tọa độ', icon=':material/my_location:'):
            st.caption('Dùng tọa độ từ hồ sơ vị trí hoặc ghim bạn đã kiểm tra trên bản đồ.')
            with st.container(horizontal=True):
                st.number_input('Vĩ độ', min_value=-90.0, max_value=90.0, value=None,
                                format='%.6f', key='valuation_latitude',
                                on_change=_manual_coordinate_changed, width=180,
                                persist_state='session')
                st.number_input('Kinh độ', min_value=-180.0, max_value=180.0, value=None,
                                format='%.6f', key='valuation_longitude',
                                on_change=_manual_coordinate_changed, width=180,
                                persist_state='session')
        latitude = st.session_state.get('valuation_latitude')
        longitude = st.session_state.get('valuation_longitude')
        point = validate_coordinates(latitude, longitude)
        basis, area = 'overview', False
        if latitude is not None or longitude is not None:
            if point is None:
                st.warning('Cần nhập đủ vĩ độ và kinh độ hợp lệ để hiển thị ghim vị trí.')
            else:
                basis = st.session_state.get('_valuation_map_source') or 'manual'
        elif candidates:
            index = st.session_state.get('valuation_map_candidate')
            if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(candidates):
                candidate = candidates[index]
                point = validate_coordinates(candidate['latitude'], candidate['longitude'])
                if point is not None:
                    area = candidate['kind'] == 'area'
                    basis = 'area' if area else 'geocode'
        labels = {'record': 'Tọa độ trong tin đăng', 'manual': 'Tọa độ bạn cung cấp',
                  'geocode': 'Vị trí tra cứu gần đúng', 'area': 'Khu vực tham khảo',
                  'overview': 'Tổng quan Việt Nam · chưa xác định vị trí'}
        st.caption(labels.get(basis, labels['overview']))
        if point is not None:
            st.caption(f'{_number(point[0])}, {_number(point[1])} · '
                       'Hãy đối chiếu vị trí với tin đăng trước khi đi xem nhà.')
            if basis in {'area', 'geocode'}:
                st.caption('Gợi ý từ địa chỉ chỉ có tính tham khảo, chưa xác nhận đúng căn nhà hoặc thửa đất.')
        st.iframe(render_map_html(*(point or (None, None)), area=area),
                  height=350, width='stretch', tab_index=0)
        with st.container(horizontal=True):
            st.link_button('OpenStreetMap', osm_map_url(*(point or (None, None))),
                           icon=':material/open_in_new:')
            st.link_button('Google Maps', google_maps_url(*(point or (None, None)), query=address),
                           icon=':material/directions:')
            st.button('Xóa vị trí', key='valuation_map_clear', on_click=clear_map_point,
                      icon=':material/location_off:')
        st.caption('Nền [OpenTopoMap](https://opentopomap.org/about) / [Top-O-Map](https://top-o-map.com/)'
                   ' · Dữ liệu © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), SRTM'
                   ' · Tra cứu [Photon](https://photon.komoot.io/)')
    return {'point': point, 'basis': basis, 'basis_label': labels.get(basis, labels['overview']),
            'latitude': point[0] if point is not None else None,
            'longitude': point[1] if point is not None else None,
            'osm_url': osm_map_url(*(point or (None, None))),
            'google_url': google_maps_url(*(point or (None, None)), query=address)}
