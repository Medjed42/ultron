"""Typed result components — the only things the interface will render.

The assistant decides WHAT to show; this module decides what a component may
contain. Every builder copies whitelisted fields out of provider data, coerces
types, keeps only https links and images, trims text and bounds list sizes.
Tool output is untrusted input: no HTML, script or arbitrary key gets through,
and the interface draws everything with textContent, never innerHTML.
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional
from urllib.parse import urlsplit

from core.workspace.storage import new_id

KINDS = ("route_map", "place_results", "stay_results", "property_focus", "link_card", "research",
         "email_summary", "drive_files", "calendar_agenda", "mcp_status", "error_recovery", "notice",
         "tool_result")

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def text(v: Any, limit: int = 500) -> str:
    if v is None:
        return ""
    s = _CTRL.sub("", str(v)).strip()
    return s[:limit] + ("…" if len(s) > limit else "")


def url(v: Any) -> str:
    """https links only, no credentials, bounded length."""
    s = str(v or "").strip()
    if not s or len(s) > 2000:
        return ""
    p = urlsplit(s)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        return ""
    return s


def num(v: Any, lo: float = -1e12, hi: float = 1e12) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and lo <= f <= hi else None


def point(p: Any) -> Optional[dict]:
    if not isinstance(p, dict):
        return None
    lat, lon = num(p.get("lat"), -90, 90), num(p.get("lon"), -180, 180)
    if lat is None or lon is None:
        return None
    return {"lat": lat, "lon": lon, "name": text(p.get("name"), 160), "label": text(p.get("label"), 240),
            "kind": text(p.get("kind"), 40), "source": text(p.get("source"), 80)}


def _wrap(kind: str, data: dict) -> dict:
    assert kind in KINDS
    return {"id": new_id("k"), "kind": kind, "data": data}


def _geometry(coords: Any, limit: int = 800) -> list:
    out = []
    for c in (coords or [])[:limit]:
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            lat, lon = num(c[0], -90, 90), num(c[1], -180, 180)
            if lat is not None and lon is not None:
                out.append([round(lat, 6), round(lon, 6)])
    return out


def route_map(r: dict) -> dict:
    routes = []
    for x in (r.get("routes") or [])[:3]:
        d, t = num(x.get("distance_m"), 0, 5e7), num(x.get("duration_s"), 0, 1e7)
        if d is None or t is None:
            continue
        routes.append({"distance_m": d, "duration_s": t, "summary": text(x.get("summary"), 160),
                       "geometry": _geometry(x.get("geometry"))})
    return _wrap("route_map", {
        "origin": point(r.get("origin")), "destination": point(r.get("destination")),
        "mode": r.get("mode") if r.get("mode") in ("driving", "walking", "cycling", "transit") else "driving",
        "routes": routes, "provider": text(r.get("provider"), 120), "traffic": bool(r.get("traffic")),
        "notes": [text(n, 240) for n in (r.get("notes") or [])[:4]],
        "available_modes": [m for m in (r.get("available_modes") or []) if m in ("driving", "walking", "cycling", "transit")],
        "open_url": url(r.get("open_url")), "fetched_at": text(r.get("fetched_at"), 40)})


def place_results(r: dict) -> dict:
    places = []
    for p in (r.get("places") or [])[:40]:
        pt = point(p)
        if not pt:
            continue
        places.append(dict(pt, id=text(p.get("id"), 80), address=text(p.get("address"), 200),
                           distance_m=num(p.get("distance_m"), 0, 1e8), website=url(p.get("website")),
                           maps_url=url(p.get("maps_url")), rating=num(p.get("rating"), 0, 10),
                           reviews=num(p.get("reviews"), 0, 1e8), opening_hours=text(p.get("opening_hours"), 160),
                           cuisine=text(p.get("cuisine"), 80), phone=text(p.get("phone"), 40),
                           open_now=p.get("open_now") if isinstance(p.get("open_now"), bool) else None))
    label = r.get("label") or {}
    return _wrap("place_results", {"center": point(r.get("center")), "category": text(r.get("category"), 40),
                                   "label": {"sr": text(label.get("sr"), 60), "en": text(label.get("en"), 60)},
                                   "radius_m": num(r.get("radius_m"), 0, 1e5), "places": places,
                                   "provider": text(r.get("provider"), 80), "distance_type": "straight_line",
                                   "fetched_at": text(r.get("fetched_at"), 40)})


def listing(x: dict) -> dict:
    return {
        "id": text(x.get("id"), 120), "provider": text(x.get("provider"), 40),
        "provider_name": text(x.get("provider_name"), 80), "title": text(x.get("title"), 160),
        "url": url(x.get("url")), "image": url(x.get("image")),
        "property_type": text(x.get("property_type"), 20), "city": text(x.get("city"), 80),
        "address": text(x.get("address"), 200), "lat": num(x.get("lat"), -90, 90), "lon": num(x.get("lon"), -180, 180),
        "price_night": num(x.get("price_night"), 0, 1e7), "price_night_max": num(x.get("price_night_max"), 0, 1e7),
        "price_currency": text(x.get("price_currency"), 3), "price_basis": text(x.get("price_basis"), 10),
        "total": num(x.get("total"), 0, 1e8), "total_currency": text(x.get("total_currency"), 3),
        "night_price": num(x.get("night_price"), 0, 1e7),
        "price_status": x.get("price_status") if x.get("price_status") in ("verified", "indicative", "unavailable") else "unavailable",
        "availability": x.get("availability") if x.get("availability") in ("available", "unknown", "unavailable") else "unknown",
        "rating": num(x.get("rating"), 0, 10), "rating_scale": num(x.get("rating_scale"), 1, 10),
        "review_count": num(x.get("review_count"), 0, 1e8), "stars": num(x.get("stars"), 0, 7),
        "capacity": num(x.get("capacity"), 0, 100), "beds": num(x.get("beds"), 0, 100),
        "amenities": [text(a, 20) for a in (x.get("amenities") or [])[:20]],
        "cancellation": text(x.get("cancellation"), 160), "description": text(x.get("description"), 400),
        "verified_at": text(x.get("verified_at"), 40), "attribution": text(x.get("attribution"), 120),
        "also_on": [{"provider": text(a.get("provider"), 60), "url": url(a.get("url"))}
                    for a in (x.get("also_on") or [])[:5] if isinstance(a, dict)],
        "distance_center_m": num(x.get("distance_center_m"), 0, 1e8),
        "distance_landmark_m": num(x.get("distance_landmark_m"), 0, 1e8),
        "flags": [text(f, 40) for f in (x.get("flags") or [])[:8]],
    }


def stay_results(r: dict, *, query: dict, assumptions: list, sort: str = "price", filters: Optional[dict] = None,
                 landmark: Optional[dict] = None, center: Optional[dict] = None, notes: Optional[list] = None) -> dict:
    return _wrap("stay_results", {
        "query": {"city": text(query.get("city"), 80), "check_in": text(query.get("check_in"), 10),
                  "check_out": text(query.get("check_out"), 10), "nights": int(num(query.get("nights"), 1, 30) or 1),
                  "guests": int(num(query.get("guests"), 1, 16) or 1), "currency": text(query.get("currency"), 3),
                  "property_type": text(query.get("property_type"), 20)},
        "assumptions": [text(a, 40) for a in assumptions[:8]],
        "listings": [listing(x) for x in (r.get("listings") or [])[:120]],
        "answered": [{"name": text(a.get("name"), 60), "count": int(num(a.get("count"), 0, 1e5) or 0)}
                     for a in (r.get("answered") or [])],
        "failed": [{"name": text(a.get("name"), 60), "error": text(a.get("error"), 200), "status": text(a.get("status"), 30)}
                   for a in (r.get("failed") or [])],
        "not_configured": [{"name": text(a.get("name"), 60), "needs": [text(n, 40) for n in a.get("needs", [])]}
                           for a in (r.get("not_configured") or [])],
        "links": [{"name": text(a.get("name"), 60), "url": url(a.get("url"))} for a in (r.get("links") or [])
                  if url(a.get("url"))],
        "sort": sort if sort in ("price", "distance", "rating", "landmark") else "price",
        "filters": filters or {}, "landmark": point(landmark), "center": point(center or r.get("center")),
        "fx": text(r.get("fx"), 120), "fetched_at": text(r.get("fetched_at"), 40),
        "notes": [text(n, 300) for n in (notes or [])[:6]]})


def property_focus(x: dict, center: Optional[dict] = None, reason: str = "") -> dict:
    return _wrap("property_focus", {"listing": listing(x), "center": point(center), "reason": text(reason, 60)})


def link_card(title: str, links: list, note: str = "") -> dict:
    return _wrap("link_card", {"title": text(title, 160), "note": text(note, 400),
                               "links": [{"label": text(l.get("label"), 80), "url": url(l.get("url")),
                                          "kind": text(l.get("kind"), 20)} for l in links if url(l.get("url"))][:6]})


def research(query: str, answer: str, sources: list, provider: str) -> dict:
    return _wrap("research", {"query": text(query, 300), "answer": text(answer, 8000), "provider": text(provider, 80),
                              "sources": [{"title": text(s.get("title"), 200), "url": url(s.get("url")),
                                           "snippet": text(s.get("snippet"), 400),
                                           "host": urlsplit(url(s.get("url"))).hostname or ""}
                                          for s in sources[:10] if url(s.get("url"))]})


def email_summary(summary: str, items: list, provider: str, query: str = "") -> dict:
    return _wrap("email_summary", {"summary": text(summary, 4000), "provider": text(provider, 80), "query": text(query, 200),
                                   "items": [{"subject": text(i.get("subject"), 200), "from": text(i.get("from"), 160),
                                              "date": text(i.get("date"), 60), "snippet": text(i.get("snippet"), 300),
                                              "unread": bool(i.get("unread")), "url": url(i.get("url"))}
                                             for i in items[:25] if isinstance(i, dict)]})


def drive_files(summary: str, items: list, provider: str) -> dict:
    return _wrap("drive_files", {"summary": text(summary, 4000), "provider": text(provider, 80),
                                 "items": [{"name": text(i.get("name"), 200), "type": text(i.get("type") or i.get("mimeType"), 80),
                                            "modified": text(i.get("modified") or i.get("modifiedTime"), 40),
                                            "owner": text(i.get("owner"), 120), "size": num(i.get("size") or i.get("size_bytes"), 0, 1e15),
                                            "url": url(i.get("url") or i.get("webViewLink"))}
                                           for i in items[:40] if isinstance(i, dict)]})


def calendar_agenda(range_label: str, events: list, provider: str, configured: bool = True, note: str = "") -> dict:
    return _wrap("calendar_agenda", {"range": text(range_label, 80), "provider": text(provider, 80),
                                     "configured": bool(configured), "note": text(note, 400),
                                     "events": [{"title": text(e.get("title"), 200), "start": text(e.get("start"), 40),
                                                 "end": text(e.get("end"), 40), "calendar": text(e.get("calendar"), 80),
                                                 "location": text(e.get("location"), 160)}
                                                for e in events[:60] if isinstance(e, dict)]})


def mcp_status(servers: list) -> dict:
    return _wrap("mcp_status", {"servers": [{"id": text(s.get("id"), 80), "name": text(s.get("name"), 80),
                                             "kind": text(s.get("kind"), 10), "status": text(s.get("status"), 20),
                                             "tools": len(s.get("tools") or []), "error": text(s.get("error"), 200)}
                                            for s in servers[:30]]})


def error_recovery(title: str, detail: str, actions: list) -> dict:
    return _wrap("error_recovery", {"title": text(title, 160), "detail": text(detail, 600),
                                    "actions": [{"label": text(a.get("label"), 60),
                                                 "kind": a.get("kind") if a.get("kind") in ("prompt", "view", "retry", "url") else "prompt",
                                                 "value": text(a.get("value"), 500) if a.get("kind") != "url" else url(a.get("value"))}
                                                for a in actions[:4]]})


def notice(title: str, body: str, tone: str = "info") -> dict:
    return _wrap("notice", {"title": text(title, 160), "text": text(body, 1200),
                            "tone": tone if tone in ("info", "warning", "success") else "info"})


def tool_result(server: str, tool: str, data: Any) -> dict:
    import json
    try:
        body = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    except Exception:
        body = str(data)
    return _wrap("tool_result", {"server": text(server, 80), "tool": text(tool, 80), "body": text(body, 6000)})
