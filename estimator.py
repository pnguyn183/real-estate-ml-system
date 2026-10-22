"""Pure comparable-price engine shared by API and offline tests."""
import math
import unicodedata
from statistics import median

def location_key(value):
    # Match full locations; do not conflate districts in different cities.
    return ' '.join(unicodedata.normalize('NFC', str(value)).casefold().split())


def estimate(properties, location, property_type, area):
    if not math.isfinite(area) or area <= 0:
        raise ValueError('Diện tích phải lớn hơn 0.')
    samples = []
    skipped = 0
    seen = set()
    for prop in properties:
        if (location_key(prop.get('location', '')) != location_key(location)
                or prop.get('type') != property_type or prop.get('status') != 'Bán'):
            continue
        try:
            price, size = float(prop['price']), float(prop['area'])
            if not all(math.isfinite(v) and v > 0 for v in (price, size)):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            skipped += 1
            continue
        signature = (location_key(prop.get('title', '')), price, size,
                     location_key(prop.get('contact', '')))
        if signature in seen:
            skipped += 1
            continue
        seen.add(signature)
        samples.append({**prop, 'unit_price': price / size})
    rates = [p['unit_price'] for p in samples]
    return {
        'samples': samples, 'skipped': skipped,
        'unit_price': median(rates) if rates else None,
        'price': median(rates) * area if rates else None,
        'low': min(rates) * area if rates else None,
        'high': max(rates) * area if rates else None,
    }
