"""Trusted, self-contained Leaflet document with independent raster sources."""

import json
from functools import lru_cache
from pathlib import Path


_ASSETS = Path(__file__).parent / 'assets'
_CREDIT = (
    '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>, '
    'SRTM | <a href="https://opentopomap.org/about" target="_blank" rel="noopener">OpenTopoMap</a> '
    '(<a href="https://creativecommons.org/licenses/by-sa/3.0/" target="_blank" rel="noopener">CC BY-SA</a>)'
)
PROVIDERS = (
    {'name': 'OpenTopoMap', 'url': 'https://a.tile.opentopomap.org/{z}/{x}/{y}.png',
     'maxZoom': 19, 'maxNativeZoom': 17, 'attribution': _CREDIT},
    {'name': 'Top-O-Map', 'url': 'https://tile.top-o-map.com/{z}/{x}/{y}.png',
     'maxZoom': 19, 'maxNativeZoom': 17,
     'attribution': _CREDIT + ' | <a href="https://top-o-map.com/" target="_blank" rel="noopener">Top-O-Map</a>'},
)


@lru_cache(maxsize=1)
def _map_assets():
    """Ship the map engine with the app; runtime does not fetch a JS CDN."""
    return tuple(path.read_text(encoding='utf-8') for path in (
        _ASSETS / 'vendor/leaflet/leaflet.css',
        _ASSETS / 'vendor/leaflet/leaflet.js',
        _ASSETS / 'property_map.css',
        _ASSETS / 'property_map.js',
    ))


def render_map_html(latitude=None, longitude=None, *, area=False):
    # Import on use to keep the map document separate from Streamlit controls.
    from property_map import validate_coordinates

    point = validate_coordinates(latitude, longitude)
    config = json.dumps({'point': point, 'area': bool(area and point is not None),
                         'bounds': [[8, 102], [24, 110]], 'providers': PROVIDERS,
                         'timeoutMs': 9000}, ensure_ascii=True, allow_nan=False).replace('<', '\\u003c')
    leaflet_css, leaflet_js, map_css, map_js = _map_assets()
    return (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="strict-origin-when-cross-origin">'
        '<title>HomeNest · Bản đồ vị trí bất động sản</title>'
        f'<style>{leaflet_css}\n{map_css}</style></head><body>'
        '<main id="homenest-map-root" data-state="loading">'
        '<div id="homenest-map" aria-label="Bản đồ vị trí bất động sản"></div>'
        '<div id="map-message" role="status" aria-live="polite">'
        '<span id="map-message-text">Đang tải bản đồ…</span>'
        '<button id="map-retry" type="button" hidden>Thử lại</button></div>'
        '<button id="map-home" type="button" title="Về vị trí ban đầu" aria-label="Về vị trí ban đầu">'
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">'
        '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/>'
        '<path d="M12 1v4m0 14v4M1 12h4m14 0h4"/></svg></button>'
        '<noscript>Cần bật JavaScript để xem bản đồ. Bạn có thể mở vị trí bằng các liên kết bên dưới.</noscript>'
        '</main><script id="homenest-map-config" type="application/json">' + config + '</script>'
        f'<script>{leaflet_js}</script><script>{map_js}</script></body></html>'
    )
