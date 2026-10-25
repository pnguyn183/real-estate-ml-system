# Leaflet 1.9.4

Vendored from the stable release linked by https://leafletjs.com/download.
BSD 2-Clause license is included in `LICENSE`. The app inlines these assets;
the browser does not need a CDN to load the map engine.

Official SHA-256 SRI checksums, verified when downloaded:

- `leaflet.js`: `sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=`
- `leaflet.css`: `sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=`

The HomeNest renderer uses an inline SVG pin and no layer selector, so Leaflet's
optional default marker/layer-control image assets are not used.
