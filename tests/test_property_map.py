import math
import os
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests

import property_map
from property_map import (
    GeocodingError, geocode_address, google_maps_url, listing_coordinates,
    osm_embed_url, reset_map_context, validate_coordinates,
)


def feature(latitude=10.78, longitude=106.70, **props):
    return {'type': 'Feature', 'geometry': {'type': 'Point',
            'coordinates': [longitude, latitude]}, 'properties': props}


class CoordinateTests(unittest.TestCase):
    def test_zero_and_numeric_strings_are_valid(self):
        self.assertEqual(validate_coordinates(0, 0), (0.0, 0.0))
        self.assertEqual(validate_coordinates('10.78', '106.7'), (10.78, 106.7))

    def test_rejects_partial_nonfinite_boolean_and_outside_bounds(self):
        for pair in [(None, 106), (10, None), (True, 0), (0, False),
                     (math.nan, 1), (1, math.inf), (91, 106), (1, -181),
                     ('north', 106), ([], 106)]:
            with self.subTest(pair=pair):
                self.assertIsNone(validate_coordinates(*pair))

    def test_record_coordinate_pair_precedence_and_aliases(self):
        self.assertEqual(listing_coordinates({'latitude': 10, 'longitude': 106,
                                             'lat': 11, 'lon': 107}), (10, 106))
        self.assertEqual(listing_coordinates({'lat': 0, 'lon': 0, 'lng': 8}), (0, 0))
        self.assertEqual(listing_coordinates({'lat': 11, 'lng': 107}), (11, 107))
        self.assertEqual(listing_coordinates({'latitude': 'bad', 'longitude': 106,
                                             'lat': 11, 'lng': 107}), (11, 107))
        for record in [{'latitude': 10, 'lon': 106}, {'lat': 10, 'longitude': 106},
                       {'latitude': 10}, None, {}]:
            self.assertIsNone(listing_coordinates(record))

    def test_overview_has_no_marker_and_uses_vietnam_bounds(self):
        params = parse_qs(urlsplit(osm_embed_url()).query)
        self.assertEqual(params['bbox'], ['102,8,110,24'])
        self.assertNotIn('marker', params)
        self.assertNotIn('marker', parse_qs(urlsplit(osm_embed_url(math.nan, 0)).query))

    def test_point_bbox_order_and_edges(self):
        for latitude, longitude in [(0, 0), (10.78, 106.7), (90, 180), (-90, -180),
                                    (85.05, 179.999), (-85.05, -179.999)]:
            with self.subTest(point=(latitude, longitude)):
                params = parse_qs(urlsplit(osm_embed_url(latitude, longitude)).query)
                west, south, east, north = map(float, params['bbox'][0].split(','))
                self.assertLess(west, east)
                self.assertLess(south, north)
                self.assertGreaterEqual(west, -180)
                self.assertLessEqual(east, 180)
                self.assertGreaterEqual(south, -property_map.MERCATOR_LIMIT - 0.0000001)
                self.assertLessEqual(north, property_map.MERCATOR_LIMIT + 0.0000001)
                self.assertEqual(params['marker'], [f'{latitude},{longitude}'])

    def test_address_maps_url_is_encoded_on_fixed_domain(self):
        address = '12 Nguyễn Huệ, Q.1 & query=javascript:alert(1) # <script>'
        url = google_maps_url(query=address)
        self.assertEqual(urlsplit(url).netloc, 'www.google.com')
        self.assertEqual(parse_qs(urlsplit(url).query), {'api': ['1'], 'query': [address]})
        self.assertEqual(parse_qs(urlsplit(google_maps_url(0, 0, query=address)).query)['query'], ['0,0'])


class GeocoderTests(unittest.TestCase):
    def setUp(self):
        property_map._geocode_cached.clear()
        self.gate = {'lock': property_map.threading.Lock(), 'last_started': None}
        self.gate_patch = patch.object(property_map, '_geocoder_gate', return_value=self.gate)
        self.gate_patch.start()
        self.env_patch = patch.dict(os.environ, {'HOMENEST_GEOCODER_URL': property_map.DEFAULT_GEOCODER_URL})
        self.env_patch.start()

    def tearDown(self):
        self.gate_patch.stop()
        self.env_patch.stop()
        property_map._geocode_cached.clear()

    def response(self, features):
        response = Mock(status_code=200)
        response.json.return_value = {'features': features}
        return response

    @patch.object(property_map.requests, 'get')
    def test_query_is_explicit_cached_bounded_and_geojson_lonlat(self, get):
        get.return_value = self.response([
            feature(latitude=10+i/100, longitude=106+i/100,
                    name=f'Nhà {i}', housenumber=str(i+1), street='Nguyễn Huệ')
            for i in range(10)])
        result = geocode_address('  Nguyễn  Huệ   ')
        self.assertEqual(len(result), 5)
        self.assertEqual((result[0]['latitude'], result[0]['longitude']), (10, 106))
        self.assertEqual(result[0]['kind'], 'address')
        self.assertEqual(geocode_address('Nguyễn Huệ'), result)
        get.assert_called_once()
        args, kwargs = get.call_args
        self.assertEqual(args[0], property_map.DEFAULT_GEOCODER_URL)
        self.assertEqual(kwargs['timeout'], (3, 8))
        self.assertEqual(kwargs['params']['limit'], 5)
        self.assertEqual(kwargs['params']['countrycode'], 'VN')
        self.assertTrue(kwargs['headers']['User-Agent'].startswith('HomeNest/1.0'))
        self.assertFalse(kwargs['allow_redirects'])

    @patch.object(property_map.requests, 'get')
    def test_not_found_is_cached(self, get):
        get.return_value = self.response([])
        self.assertEqual(geocode_address('Khu vực không có'), [])
        self.assertEqual(geocode_address('Khu vực không có'), [])
        get.assert_called_once()

    @patch.object(property_map.time, 'sleep')
    @patch.object(property_map.time, 'monotonic', return_value=100.0)
    @patch.object(property_map.requests, 'get')
    def test_distinct_queries_share_rate_limit(self, get, monotonic, sleep):
        get.return_value = self.response([])
        geocode_address('Hà Nội')
        geocode_address('Đà Nẵng')
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(1.0)

    @patch.object(property_map.requests, 'get')
    def test_candidates_skip_invalid_geometry_and_deduplicate(self, get):
        valid = feature(0, 0, name='Quận A', osm_value='district')
        get.return_value = self.response([
            {}, None, feature(math.nan, 106, name='Bad'),
            feature(10, True, name='Bool'), feature(10, 181, name='Bounds'),
            {'geometry': {'type': 'LineString', 'coordinates': [106, 10]}, 'properties': {}},
            valid, valid, feature(11, 107, name='Malformed tags', osm_value=[], type={}),
        ])
        results = geocode_address('Quận A')
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['kind'], 'area')
        self.assertEqual(results[0]['latitude'], 0)

    @patch.object(property_map.requests, 'get')
    def test_streets_and_city_suggestions_are_coarse(self, get):
        get.return_value = self.response([
            feature(name='Hồ Chí Minh', osm_value='city'),
            feature(latitude=11, name='Đường A', osm_key='highway', osm_value='primary'),
            feature(latitude=12, name='Nhà A', housenumber='10', osm_value='residential'),
        ])
        results = geocode_address('Địa chỉ')
        self.assertEqual([r['kind'] for r in results], ['area', 'area', 'address'])

    @patch.object(property_map.requests, 'get')
    def test_timeout_rate_limit_http_and_bad_data_raise_recoverable_errors(self, get):
        failures = [requests.Timeout(), requests.ConnectionError()]
        for index, failure in enumerate(failures):
            get.side_effect = failure
            with self.subTest(error=type(failure).__name__), self.assertRaises(GeocodingError):
                geocode_address(f'Địa chỉ {index}')
            self.gate['last_started'] = None
        get.side_effect = None
        for status in (429, 503, 302):
            response = Mock(status_code=status)
            response.raise_for_status.side_effect = requests.HTTPError()
            get.return_value = response
            with self.subTest(status=status), self.assertRaises(GeocodingError):
                geocode_address(f'Địa chỉ lỗi {status}')
            self.gate['last_started'] = None
        response = Mock(status_code=200)
        response.json.side_effect = ValueError('bad JSON')
        get.return_value = response
        with self.assertRaises(GeocodingError):
            geocode_address('Dữ liệu lỗi JSON')
        self.gate['last_started'] = None
        response.json.side_effect = None
        response.json.return_value = {'features': None}
        with self.assertRaises(GeocodingError):
            geocode_address('Dữ liệu lỗi cấu trúc')

    @patch.object(property_map.requests, 'get')
    def test_rejects_invalid_queries_and_untrusted_endpoint_configuration(self, get):
        for query in ('', 'ab', 'x' * 301):
            with self.assertRaises(GeocodingError):
                geocode_address(query)
        for endpoint in ('javascript:alert(1)', 'https://example.com/api?q=abc',
                         'https://[malformed/api/',
                         'https://name:pass@example.com/api/',
                         'https://nominatim.openstreetmap.org/search'):
            with patch.dict(os.environ, {'HOMENEST_GEOCODER_URL': endpoint}):
                with self.assertRaises(GeocodingError):
                    geocode_address('Hà Nội')
        get.assert_not_called()


class MapStateTests(unittest.TestCase):
    def test_new_record_clears_stale_geocode_and_uses_record_point(self):
        state = {'valuation_map_candidate': 0, '_valuation_map_candidates': [{'old': True}],
                 'valuation_latitude': 1, 'valuation_longitude': 2,
                 '_valuation_map_error': 'old failure', 'valuation_map_address': 'Old'}
        with patch.object(property_map.st, 'session_state', state):
            reset_map_context({'id': 'B', 'address': '12 Nguyễn Huệ',
                               'latitude': 10, 'longitude': 106}, 'TP.HCM')
        self.assertEqual(state['valuation_map_address'], '12 Nguyễn Huệ')
        self.assertEqual((state['valuation_latitude'], state['valuation_longitude']), (10, 106))
        self.assertEqual(state['_valuation_map_source'], 'record')
        self.assertIsNone(state['valuation_map_candidate'])
        self.assertEqual(state['_valuation_map_candidates'], [])
        self.assertNotIn('_valuation_map_error', state)

    def test_location_without_coordinates_never_inherits_a_prior_pin(self):
        state = {'valuation_latitude': 10, 'valuation_longitude': 106}
        with patch.object(property_map.st, 'session_state', state):
            reset_map_context(location='Đà Nẵng')
        self.assertIsNone(state['valuation_latitude'])
        self.assertIsNone(state['valuation_longitude'])
        self.assertEqual(state['valuation_map_address'], 'Đà Nẵng')

    def test_candidate_selection_clears_manual_pair_before_widgets_rerender(self):
        state = {'valuation_map_candidate': 0,
                 '_valuation_map_candidates': [{'latitude': 10, 'longitude': 106}],
                 'valuation_latitude': 11, 'valuation_longitude': 107}
        with patch.object(property_map.st, 'session_state', state):
            property_map._candidate_changed()
        self.assertIsNone(state['valuation_latitude'])
        self.assertIsNone(state['valuation_longitude'])
        self.assertEqual(state['_valuation_map_source'], 'geocode')


class MapInteractionTests(unittest.TestCase):
    SCRIPT = """
import streamlit as st
from property_map import render_property_map
st.session_state['_test_map_result'] = render_property_map(
    st.session_state.get('_test_location', 'TP.HCM'),
    st.session_state.get('_test_record'))
"""

    def app(self):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_string(self.SCRIPT).run()

    @patch.object(property_map.requests, 'get')
    def test_manual_pair_address_edit_and_clear_are_live_without_network(self, get):
        app = self.app()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.warning), 0)
        self.assertIsNone(app.session_state['_test_map_result']['point'])
        app.number_input(key='valuation_latitude').set_value(10.78).run()
        self.assertEqual(len(app.warning), 1)
        self.assertIsNone(app.session_state['_test_map_result']['point'])
        app.number_input(key='valuation_longitude').set_value(106.7).run()
        self.assertEqual(app.session_state['_test_map_result']['point'], (10.78, 106.7))
        app.text_input(key='valuation_map_address').set_value('Đà Nẵng').run()
        self.assertIsNone(app.session_state['_test_map_result']['point'])
        self.assertIsNone(app.session_state['valuation_latitude'])
        self.assertIsNone(app.session_state['valuation_longitude'])
        app.button(key='valuation_map_clear').click().run()
        self.assertEqual(app.text_input(key='valuation_map_address').value, 'Đà Nẵng')
        self.assertEqual(len(app.exception), 0)
        get.assert_not_called()

    @patch.object(property_map, 'geocode_address')
    def test_search_replaces_manual_point_but_requires_explicit_candidate_selection(self, lookup):
        lookup.return_value = [{'latitude': 21.03, 'longitude': 105.85,
                                'label': 'Hoàn Kiếm, Hà Nội', 'kind': 'area'}]
        app = self.app()
        app.number_input(key='valuation_latitude').set_value(10.78).run()
        app.number_input(key='valuation_longitude').set_value(106.7).run()
        lookup.assert_not_called()
        app.button(key='valuation_map_search').click().run()
        self.assertIsNone(app.session_state['valuation_latitude'])
        self.assertIsNone(app.session_state['valuation_longitude'])
        self.assertIsNone(app.session_state['_test_map_result']['point'])
        app.selectbox(key='valuation_map_candidate').select(0).run()
        self.assertEqual(app.session_state['_test_map_result']['point'], (21.03, 105.85))
        self.assertEqual(app.session_state['_test_map_result']['basis'], 'area')
        app.button(key='valuation_map_clear').click().run()
        self.assertIsNone(app.session_state['_test_map_result']['point'])
        self.assertEqual(app.session_state['_valuation_map_candidates'], [])
        self.assertEqual(len(app.exception), 0)
        lookup.assert_called_once()

    @patch.object(property_map.requests, 'get')
    def test_changing_listing_drops_pin_when_next_listing_has_no_coordinates(self, get):
        app = self.app()
        app.session_state['_test_record'] = {
            'id': 'A', 'address': 'Nhà A', 'latitude': 10.78, 'longitude': 106.7}
        app.run()
        self.assertEqual(app.session_state['_test_map_result']['basis'], 'record')
        app.session_state['_test_record'] = {'id': 'B', 'address': 'Nhà B'}
        app.run()
        self.assertIsNone(app.session_state['_test_map_result']['point'])
        self.assertEqual(app.text_input(key='valuation_map_address').value, 'Nhà B')
        self.assertEqual(len(app.exception), 0)
        get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
