"""Pure helpers behind the local form (scripts/ui.py): turn friendly input (city, radius in km,
one keyword per line) into the job body the scraper API expects, and trim results to the radius.
No I/O here, so it is all unit-testable (tests/test_jobform.py).
"""
import math

LANGS = {"pt": "Português", "en": "English"}
DEFAULT_LANG = "pt"
MIN_RADIUS_KM, MAX_RADIUS_KM = 0.5, 200
MIN_DEPTH, MAX_DEPTH = 1, 50
MIN_MINUTES, MAX_MINUTES = 1, 120
MIN_ZOOM, MAX_ZOOM = 8, 18
EARTH_RADIUS_KM = 6371.0088
# Web-mercator ground resolution at zoom 0 on the equator, and the map width the scraper's
# headless browser roughly shows. Used to pick a zoom whose viewport spans the search diameter.
METERS_PER_PIXEL_Z0 = 156543.03392
VIEWPORT_PX = 1024


def parse_keywords(text):
    """Textarea -> list of keywords. One per line; blanks, '#' comments and duplicates dropped."""
    seen, out = set(), []
    for line in (text or "").splitlines():
        kw = line.strip()
        if not kw or kw.startswith("#") or kw.lower() in seen:
            continue
        seen.add(kw.lower())
        out.append(kw)
    return out


def km_to_m(km):
    return int(round(float(km) * 1000))


def zoom_for_radius(radius_km, lat):
    """Map zoom whose viewport is about 2 x radius wide at this latitude."""
    width_m = 2 * float(radius_km) * 1000
    zoom = math.log2(VIEWPORT_PX * METERS_PER_PIXEL_Z0 * math.cos(math.radians(float(lat))) / width_m)
    return max(MIN_ZOOM, min(MAX_ZOOM, round(zoom)))


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _number(payload, key, label, low, high, cast=float):
    try:
        value = cast(payload.get(key))
    except (TypeError, ValueError):
        raise ValueError(f"{label}: informe um número.")
    if not low <= value <= high:
        raise ValueError(f"{label}: use um valor entre {low} e {high}.")
    return value


def build_job_body(payload):
    """Validated form payload -> body for POST /api/v1/jobs. Raises ValueError (message in pt-BR)."""
    keywords = parse_keywords(payload.get("keywords"))
    if not keywords:
        raise ValueError("Palavras-chave: informe pelo menos uma, uma por linha.")
    lang = payload.get("lang") or DEFAULT_LANG
    if lang not in LANGS:
        raise ValueError(f"Idioma: use um destes: {', '.join(LANGS)}.")
    lat = _number(payload, "lat", "Latitude", -90, 90)
    lon = _number(payload, "lon", "Longitude", -180, 180)
    radius_km = _number(payload, "radius_km", "Raio (km)", MIN_RADIUS_KM, MAX_RADIUS_KM)
    depth = _number(payload, "depth", "Profundidade", MIN_DEPTH, MAX_DEPTH, int)
    minutes = _number(payload, "max_minutes", "Tempo máximo (min)", MIN_MINUTES, MAX_MINUTES, int)
    city = (payload.get("city") or "").strip()
    name = (payload.get("name") or "").strip() or " — ".join(p for p in (keywords[0], city) if p)
    return {"name": name, "keywords": keywords, "lang": lang,
            "zoom": zoom_for_radius(radius_km, lat), "lat": str(lat), "lon": str(lon),
            "fast_mode": False, "radius": km_to_m(radius_km), "depth": depth,
            "email": bool(payload.get("email")), "max_time": minutes * 60}


def _distance_km(row, lat, lon):
    try:
        return haversine_km(lat, lon, float(row["latitude"]), float(row["longitude"]))
    except (KeyError, TypeError, ValueError):
        return None


def leads_within_radius(rows, fields, lat, lon, radius_m):
    """Keep `fields` of each row inside the radius and add distance_km.

    Rows without usable coordinates are kept (distance blank) rather than silently lost.
    """
    radius_km = float(radius_m) / 1000
    out = []
    for row in rows:
        distance = _distance_km(row, float(lat), float(lon))
        if distance is not None and distance > radius_km:
            continue
        lead = {k: row.get(k, "") for k in fields}
        lead["distance_km"] = "" if distance is None else f"{distance:.1f}"
        out.append(lead)
    return out
