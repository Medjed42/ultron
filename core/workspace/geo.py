"""Mapping service: geocoding, routes and nearby places from real providers.

Provider order (first configured one answers):
    geocode   Google Geocoding (server key)   →  OpenStreetMap Nominatim
    route     Google Routes (server key)       →  OSRM (OSM data; car, foot, bike)
    nearby    Google Places (server key)       →  OpenStreetMap Overpass

Every result names its provider. A distance or duration is only ever copied
from a routing response; straight-line distances are labelled as such.
Public transport needs Google Routes: without a key the answer says so.
"""
from __future__ import annotations

import math
import re
import time
from typing import Optional
from urllib.parse import urlencode

from core.workspace import http
from core.workspace.http import ProviderError
from core.workspace.text import fold, place_lemma

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_ENDPOINTS = (OVERPASS, "https://overpass.kumi.systems/api/interpreter")
OSRM = {
    "driving": "https://router.project-osrm.org/route/v1/driving/",
    "walking": "https://routing.openstreetmap.de/routed-foot/route/v1/driving/",
    "cycling": "https://routing.openstreetmap.de/routed-bike/route/v1/driving/",
}
MODES = ("driving", "walking", "cycling", "transit")
GOOGLE_MODES = {"driving": "DRIVE", "walking": "WALK", "cycling": "BICYCLE", "transit": "TRANSIT"}

GEOCODE_TTL = 30 * 24 * 3600
ROUTE_TTL = 30 * 60
NEARBY_TTL = 6 * 3600


def google_key() -> Optional[str]:
    """The server-side Maps key, or None. Never sent to the web view."""
    try:
        from core.integrations import store
        return store.api_key("google_maps_api_key")
    except Exception:
        return None


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def haversine_m(a: dict, b: dict) -> float:
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat = lat2 - lat1
    dlon = math.radians(b["lon"] - a["lon"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(min(1.0, math.sqrt(h)))


def valid_point(p) -> bool:
    try:
        return -90 <= float(p["lat"]) <= 90 and -180 <= float(p["lon"]) <= 180
    except (KeyError, TypeError, ValueError):
        return False


def maps_link(destination: str, origin: str = "", mode: str = "driving") -> str:
    args = {"api": 1, "destination": destination,
            "travelmode": {"cycling": "bicycling"}.get(mode, mode)}
    if origin:
        args["origin"] = origin
    return "https://www.google.com/maps/dir/?" + urlencode(args)


def maps_place_link(name: str, lat: Optional[float] = None, lon: Optional[float] = None) -> str:
    query = f"{lat:.6f},{lon:.6f}" if lat is not None and lon is not None else name
    return "https://www.google.com/maps/search/?" + urlencode({"api": 1, "query": query})


# ── geocoding ────────────────────────────────────────────────────────────────

_PLACE_TYPES = {"city", "town", "village", "municipality", "suburb", "hamlet", "administrative",
                "quarter", "neighbourhood", "locality", "county", "state", "island"}


def _nominatim(query: str, country: Optional[str], cancel=None) -> list[dict]:
    params = {"q": query, "format": "jsonv2", "limit": 5, "addressdetails": 1,
              "accept-language": "sr-Latn,sr,en"}
    if country:
        params["countrycodes"] = country
    rows = http.get_json(NOMINATIM, params=params, ttl=GEOCODE_TTL, provider="OpenStreetMap Nominatim",
                         cache=http.disk_cache("geocode"), cancel=cancel)
    return rows if isinstance(rows, list) else []


def _google_geocode(query: str, country: Optional[str], key: str, cancel=None) -> list[dict]:
    params = {"address": query, "key": key, "language": "sr-Latn"}
    if country:
        params["components"] = f"country:{country.upper()}"
    data = http.get_json("https://maps.googleapis.com/maps/api/geocode/json", params=params,
                         ttl=GEOCODE_TTL, provider="Google Geocoding", cancel=cancel)
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        raise ProviderError("Google Geocoding returned " + str(data.get("status")), provider="Google Geocoding",
                            status="unauthorized" if data.get("status") == "REQUEST_DENIED" else "error")
    return data.get("results", [])


def geocode(phrase: str, *, near: Optional[dict] = None, prefer_country: str = "rs",
            cancel=None) -> dict:
    """Resolve a (possibly declined) place phrase to one point.

    Returns {name, label, lat, lon, country, kind, source, query, assumed_country}.
    Raises ProviderError when nothing usable is found."""
    m = re.fullmatch(r"\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*", phrase or "")
    if m and valid_point({"lat": float(m.group(1)), "lon": float(m.group(2))}):
        lat, lon = float(m.group(1)), float(m.group(2))
        return {"name": f"{lat:.5f}, {lon:.5f}", "label": f"{lat:.5f}, {lon:.5f}", "lat": lat, "lon": lon,
                "country": "", "kind": "coordinates", "source": "coordinates", "query": phrase,
                "assumed_country": False}
    display, country, candidates = place_lemma(phrase)
    key = google_key()
    last_error: Optional[Exception] = None
    fallback_hit: Optional[dict] = None       # a non-settlement match, used only if nothing better turns up
    for cand in candidates:
        for cc in ([country] if country else [prefer_country, None]):
            try:
                if key:
                    rows = _google_geocode(cand, cc, key, cancel)
                    if rows:
                        r = rows[0]
                        loc = r["geometry"]["location"]
                        comps = {t: c for c in r.get("address_components", []) for t in c.get("types", [])}
                        return {"name": (comps.get("locality") or {}).get("long_name") or cand,
                                "label": r.get("formatted_address", cand), "lat": loc["lat"], "lon": loc["lng"],
                                "country": ((comps.get("country") or {}).get("short_name") or "").lower(),
                                "kind": (r.get("types") or ["place"])[0], "source": "Google Geocoding",
                                "query": phrase, "assumed_country": bool(cc and not country)}
                    continue
                rows = _nominatim(cand, cc, cancel)
            except ProviderError as exc:
                if exc.status == "cancelled":
                    raise
                last_error = exc
                continue
            if not rows:
                continue
            best = _pick_nominatim(rows, cand, near, (display, phrase))
            if best:
                addr = best.get("address") or {}
                name = (best.get("name") or addr.get("city") or addr.get("town")
                        or addr.get("village") or cand)
                hit = {"name": name, "label": best.get("display_name", name),
                       "lat": float(best["lat"]), "lon": float(best["lon"]),
                       "country": (addr.get("country_code") or "").lower(),
                       "kind": best.get("addresstype") or best.get("type") or "place",
                       "source": "OpenStreetMap Nominatim", "query": phrase,
                       "assumed_country": bool(cc and not country),
                       "bbox": [float(x) for x in best.get("boundingbox", [])][:4] or None}
                if best.get("category") in _NOT_SETTLEMENT and len(candidates) > 1:
                    fallback_hit = fallback_hit or hit
                    continue
                return hit
    if fallback_hit:
        return fallback_hit
    if last_error:
        raise ProviderError(f"Could not locate “{display}”: {last_error}", provider="geocoding",
                            status=getattr(last_error, "status", "error"))
    raise ProviderError(f"No map provider recognised “{display}”. Try the full name or add the country.",
                        provider="geocoding", status="not_found")


_NOT_SETTLEMENT = {"waterway", "highway", "natural", "railway", "landuse", "leisure", "shop", "amenity",
                   "building", "man_made", "tourism", "office", "craft"}


def _pick_nominatim(rows: list[dict], cand: str, near: Optional[dict], aliases: tuple = ()) -> Optional[dict]:
    """Prefer settlements over same-named rivers and streets ("Wien" is also a
    river), then an exact name match in any script, then importance."""
    want = {fold(cand)} | {fold(a) for a in aliases if a}

    def score(r):
        s = float(r.get("importance") or 0)
        if fold(r.get("name") or "") in want:
            s += 0.6
        kind = r.get("addresstype") or r.get("type")
        if kind in _PLACE_TYPES or r.get("category") in ("place", "boundary"):
            s += 1.5
        elif r.get("category") in _NOT_SETTLEMENT:
            s -= 1.0
        if near and valid_point({"lat": float(r["lat"]), "lon": float(r["lon"])}):
            d = haversine_m(near, {"lat": float(r["lat"]), "lon": float(r["lon"])})
            s += max(0.0, 0.8 - d / 50000)
        return s

    return max(rows, key=score) if rows else None


def resolve_landmark(phrase: str, around: dict, cancel=None) -> dict:
    """A landmark inside a known city ("autobuska stanica", "centar")."""
    f = fold(phrase)
    if re.search(r"\b(centar|centra|centru|center|centre|downtown)\b", f):
        return dict(around, name=around.get("name", "") + " — center", kind="center",
                    source=around.get("source", "") + " (city reference point)")
    tag = None
    if re.search(r"autobusk|bus station|autobuska|bas stanic", f):
        tag = '["amenity"="bus_station"]'
    elif re.search(r"zeleznick|train station|railway|voz", f):
        tag = '["railway"="station"]'
    elif re.search(r"aerodrom|airport", f):
        tag = '["aeroway"="aerodrome"]'
    if tag:
        rows = _overpass(f'nwr{tag}(around:12000,{around["lat"]},{around["lon"]});', 10, cancel)
        if rows:
            best = min(rows, key=lambda r: haversine_m(around, r))
            return {"name": best["name"] or phrase, "label": best["name"] or phrase,
                    "lat": best["lat"], "lon": best["lon"], "kind": "landmark",
                    "source": "OpenStreetMap Overpass", "query": phrase}
    return geocode(f"{phrase}, {around.get('name', '')}", near=around, cancel=cancel)


# ── routing ──────────────────────────────────────────────────────────────────

def _decode_polyline(encoded: str) -> list[list[float]]:
    points, idx, lat, lng = [], 0, 0, 0
    while idx < len(encoded):
        for which in (0, 1):
            shift, result = 0, 0
            while True:
                b = ord(encoded[idx]) - 63
                idx += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if which == 0:
                lat += delta
            else:
                lng += delta
        points.append([lat / 1e5, lng / 1e5])
    return points


def _thin(points: list[list[float]], limit: int = 600) -> list[list[float]]:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    out = [points[int(i * step)] for i in range(limit)]
    out.append(points[-1])
    return out


def route(origin: dict, destination: dict, mode: str = "driving", cancel=None) -> dict:
    """Routes between two geocoded points. Returns a dict with `routes` (fastest
    first) or raises ProviderError. `status` = unsupported for transit without
    Google Routes."""
    if mode not in MODES:
        raise ProviderError("Unsupported travel mode.", status="error")
    key = google_key()
    base = {"origin": origin, "destination": destination, "mode": mode, "fetched_at": now_iso(),
            "open_url": maps_link(_label(destination), _label(origin), mode),
            "available_modes": list(MODES) if key else ["driving", "walking", "cycling"]}
    if key:
        return dict(base, **_google_route(origin, destination, mode, key, cancel))
    if mode == "transit":
        raise ProviderError("Public-transport times need Google Routes (add a Google Maps server key in "
                            "Connections). OpenStreetMap routing covers car, walking and cycling only.",
                            status="unsupported", provider="OSRM")
    coords = f"{origin['lon']:.6f},{origin['lat']:.6f};{destination['lon']:.6f},{destination['lat']:.6f}"
    data = http.get_json(OSRM[mode] + coords,
                         params={"overview": "full", "geometries": "geojson", "alternatives": "true",
                                 "steps": "false"},
                         ttl=ROUTE_TTL, provider="OSRM", cancel=cancel, timeout=20)
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ProviderError(f"OSRM found no {mode} route ({data.get('code', 'no route')}).",
                            status="not_found", provider="OSRM")
    routes = []
    for r in data["routes"][:3]:
        if "distance" not in r or "duration" not in r:
            continue
        geom = [[c[1], c[0]] for c in (r.get("geometry") or {}).get("coordinates", [])]
        routes.append({"distance_m": float(r["distance"]), "duration_s": float(r["duration"]),
                       "summary": r.get("legs", [{}])[0].get("summary", ""), "geometry": _thin(geom)})
    if not routes:
        raise ProviderError("OSRM omitted distance or duration; nothing verified.", provider="OSRM")
    routes.sort(key=lambda x: x["duration_s"])
    host = "OSRM demo server" if mode == "driving" else "FOSSGIS OSRM"
    return dict(base, routes=routes, provider=f"OSRM · OpenStreetMap ({host})", traffic=False,
                notes=["Travel times assume typical free-flow speeds; live traffic is not included."]
                if mode == "driving" else [])


def _label(p: dict) -> str:
    if p.get("kind") == "coordinates":
        return f"{p['lat']:.6f},{p['lon']:.6f}"
    return p.get("name") or p.get("label") or f"{p['lat']:.6f},{p['lon']:.6f}"


def _google_route(origin, destination, mode, key, cancel) -> dict:
    payload = {"origin": {"location": {"latLng": {"latitude": origin["lat"], "longitude": origin["lon"]}}},
               "destination": {"location": {"latLng": {"latitude": destination["lat"],
                                                       "longitude": destination["lon"]}}},
               "travelMode": GOOGLE_MODES[mode], "computeAlternativeRoutes": mode != "transit",
               "languageCode": "sr-Latn", "units": "METRIC"}
    if mode == "driving":
        payload["routingPreference"] = "TRAFFIC_AWARE"
    data = http.post_json("https://routes.googleapis.com/directions/v2:computeRoutes", json_body=payload,
                          headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask":
                                   "routes.distanceMeters,routes.duration,routes.description,"
                                   "routes.polyline.encodedPolyline,routes.warnings"},
                          ttl=ROUTE_TTL, provider="Google Routes", cancel=cancel)
    routes = []
    for r in data.get("routes", []):
        dur = str(r.get("duration", ""))
        if "distanceMeters" not in r or not dur.endswith("s"):
            continue
        routes.append({"distance_m": float(r["distanceMeters"]), "duration_s": float(dur[:-1]),
                       "summary": r.get("description", ""),
                       "geometry": _thin(_decode_polyline((r.get("polyline") or {}).get("encodedPolyline", "")))})
    if not routes:
        raise ProviderError(f"Google Routes returned no {mode} route.", status="not_found",
                            provider="Google Routes")
    routes.sort(key=lambda x: x["duration_s"])
    return {"routes": routes, "provider": "Google Routes", "traffic": mode == "driving", "notes": []}


# ── nearby places ────────────────────────────────────────────────────────────

CATEGORIES = {
    # key: (Overpass selector, Google type, sr label, en label, regex of request words)
    "restaurant": ('["amenity"="restaurant"]', "restaurant", "Restorani", "Restaurants",
                   r"restoran|restaurant|rucak|vecer[au]|dinner|lunch|jelo|hrana|food|kafan"),
    "cafe": ('["amenity"="cafe"]', "cafe", "Kafići", "Cafés", r"kafic|kafe|cafe|coffee|kafa|kafu"),
    "bar": ('["amenity"~"^(bar|pub)$"]', "bar", "Barovi i pabovi", "Bars & pubs", r"\bbar|pub|pivnic"),
    "fast_food": ('["amenity"="fast_food"]', "meal_takeaway", "Brza hrana", "Fast food",
                  r"brz[au] hran|fast food|pica|pizza|burger|rostilj|giros"),
    "parking": ('["amenity"="parking"]', "parking", "Parkinzi", "Parking", r"parking|parkir"),
    "fuel": ('["amenity"="fuel"]', "gas_station", "Benzinske pumpe", "Fuel stations",
             r"pump[aeu]|benzin|gorivo|fuel|gas station|petrol"),
    "pharmacy": ('["amenity"="pharmacy"]', "pharmacy", "Apoteke", "Pharmacies", r"apotek|pharmac|ljekarn"),
    "hospital": ('["amenity"~"^(hospital|clinic)$"]', "hospital", "Bolnice i klinike", "Hospitals & clinics",
                 r"bolnic|hospital|klinik|clinic|hitna|dom zdravlja"),
    "atm": ('["amenity"="atm"]', "atm", "Bankomati", "ATMs", r"bankomat|atm\b"),
    "supermarket": ('["shop"~"^(supermarket|convenience)$"]', "supermarket", "Prodavnice", "Shops",
                    r"prodavnic|market|supermarket|grocer|shop|radnj"),
    "attraction": ('["tourism"~"^(attraction|museum|viewpoint|gallery)$"]', "tourist_attraction",
                   "Znamenitosti", "Sights", r"znamenitost|muzej|museum|sight|attraction|vidikovac|turistick"),
    "charging": ('["amenity"="charging_station"]', "electric_vehicle_charging_station", "Punjači za EV",
                 "EV charging", r"punjac|charging|elektricn\w* auto"),
}


def detect_category(text: str) -> Optional[str]:
    f = fold(text)
    for key, spec in CATEGORIES.items():
        if re.search(spec[4], f):
            return key
    return None


def _overpass(body: str, limit: int, cancel=None) -> list[dict]:
    """Overpass with failover: the main instance is often overloaded (HTTP
    504/429), so the same query goes to the next public instance listed on
    the OSM wiki. Same data, same licence."""
    q = f"[out:json][timeout:20];({body});out center tags {int(limit)};"
    data, last = None, None
    for attempt, endpoint in enumerate(OVERPASS_ENDPOINTS + (OVERPASS,)):
        if attempt == len(OVERPASS_ENDPOINTS):
            time.sleep(1.5)            # one delayed retry: overload errors are usually brief
        try:
            data = http.post_json(endpoint, data={"data": q}, ttl=NEARBY_TTL, provider="OpenStreetMap Overpass",
                                  cancel=cancel, timeout=25)
            break
        except ProviderError as exc:
            if exc.status in ("cancelled", "unauthorized"):
                raise
            last = exc
    if data is None:
        raise last or ProviderError("OpenStreetMap Overpass is unavailable.", provider="OpenStreetMap Overpass")
    out = []
    for e in data.get("elements", []):
        lat = e.get("lat", (e.get("center") or {}).get("lat"))
        lon = e.get("lon", (e.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            continue
        tags = e.get("tags") or {}
        street = " ".join(x for x in (tags.get("addr:street", ""), tags.get("addr:housenumber", "")) if x)
        out.append({"id": f"osm:{e.get('type', 'n')}/{e.get('id')}", "name": tags.get("name", ""),
                    "lat": float(lat), "lon": float(lon), "tags": tags,
                    "address": ", ".join(x for x in (street, tags.get("addr:city", "")) if x),
                    "website": tags.get("website") or tags.get("contact:website") or "",
                    "phone": tags.get("phone") or tags.get("contact:phone") or "",
                    "opening_hours": tags.get("opening_hours", ""),
                    "cuisine": tags.get("cuisine", "").replace(";", ", ")})
    return out


def nearby(center: dict, category: str, radius_m: int = 1500, limit: int = 30, cancel=None) -> dict:
    if category not in CATEGORIES:
        raise ProviderError("Unknown place category.", status="error")
    radius_m = max(200, min(int(radius_m), 10000))
    selector, gtype, sr_label, en_label, _ = CATEGORIES[category]
    key = google_key()
    places: list[dict] = []
    if key:
        data = http.post_json("https://places.googleapis.com/v1/places:searchNearby",
                              json_body={"includedTypes": [gtype], "maxResultCount": min(limit, 20),
                                         "locationRestriction": {"circle": {
                                             "center": {"latitude": center["lat"], "longitude": center["lon"]},
                                             "radius": float(radius_m)}}},
                              headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask":
                                       "places.id,places.displayName,places.formattedAddress,places.location,"
                                       "places.rating,places.userRatingCount,places.googleMapsUri,"
                                       "places.websiteUri,places.currentOpeningHours.openNow"},
                              ttl=NEARBY_TTL, provider="Google Places", cancel=cancel)
        for p in data.get("places", []):
            loc = p.get("location") or {}
            if "latitude" not in loc:
                continue
            places.append({"id": "g:" + p.get("id", ""), "name": (p.get("displayName") or {}).get("text", ""),
                           "lat": loc["latitude"], "lon": loc["longitude"],
                           "address": p.get("formattedAddress", ""), "rating": p.get("rating"),
                           "reviews": p.get("userRatingCount"), "website": p.get("websiteUri", ""),
                           "maps_url": p.get("googleMapsUri", ""),
                           "open_now": (p.get("currentOpeningHours") or {}).get("openNow")})
        provider = "Google Places"
    else:
        places = _overpass(f'nwr{selector}(around:{radius_m},{center["lat"]},{center["lon"]});', limit * 2, cancel)
        provider = "OpenStreetMap Overpass"
    for p in places:
        p["distance_m"] = round(haversine_m(center, p))
        p.setdefault("maps_url", maps_place_link(p["name"] or category, p["lat"], p["lon"]))
    named = [p for p in places if p.get("name")]
    named.sort(key=lambda p: p["distance_m"])
    return {"center": center, "category": category, "label": {"sr": sr_label, "en": en_label},
            "radius_m": radius_m, "places": named[:limit], "provider": provider,
            "distance_type": "straight_line", "fetched_at": now_iso()}
