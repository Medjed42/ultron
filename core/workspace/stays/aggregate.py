"""StayFinder: search every source at once, then normalise, merge and rank.

One slow or failing source never blocks the others: each runs in its own
worker with a deadline, and the result lists which sources answered, which
failed and why. Ranking for "cheapest" is by comparable total — verified
quotes first, then indicative prices, then listings with no published price.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from core.workspace.geo import haversine_m, now_iso, valid_point
from core.workspace.stays import pricing
from core.workspace.stays.base import Listing, StayQuery
from core.workspace.stays.providers import fail, registry
from core.workspace.text import fold

PROVIDER_DEADLINE = 45.0
_STATUS_RANK = {"verified": 0, "indicative": 1, "unavailable": 2}
_STOP = {"apartman", "apartmani", "apartment", "apartments", "stan", "na", "dan", "studio", "sobe", "soba",
         "rooms", "room", "hotel", "hostel", "vila", "villa", "kuca", "house", "i", "and", "the", "lux", "&", "-",
         "centar", "center", "city", "garni"}


def _tokens(title: str, city: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]+", fold(title)))
    return {w for w in words if w not in _STOP and w not in set(fold(city).split()) and len(w) > 1}


def _same_property(a: Listing, b: Listing) -> bool:
    ta, tb = _tokens(a.title, a.city), _tokens(b.title, b.city)
    if not ta or not tb:
        return False
    sim = len(ta & tb) / len(ta | tb)
    close = None
    if a.lat is not None and b.lat is not None:
        close = haversine_m({"lat": a.lat, "lon": a.lon}, {"lat": b.lat, "lon": b.lon}) < 250
    if close is True:
        return sim >= 0.5
    if close is False:
        return False
    return sim >= 0.85


def deduplicate(items: list[Listing]) -> list[Listing]:
    """Merge the same property seen on several sources. The record with a
    price (or a verified price) wins; the others become `also_on` links."""
    order = sorted(items, key=lambda x: (_STATUS_RANK.get(x.price_status, 3), x.price_night is None))
    kept: list[Listing] = []
    for item in order:
        twin = next((k for k in kept if k.provider != item.provider and _same_property(k, item)), None)
        if twin is None:
            kept.append(item)
            continue
        twin.also_on.append({"provider": item.provider_name, "url": item.url})
        if twin.lat is None and item.lat is not None:
            twin.lat, twin.lon = item.lat, item.lon
        if not twin.image and item.image:
            twin.image = item.image
        if not twin.address and item.address:
            twin.address = item.address
        if twin.rating is None and item.rating is not None:
            twin.rating, twin.rating_scale, twin.review_count = item.rating, item.rating_scale, item.review_count
        for a in item.amenities:
            if a not in twin.amenities:
                twin.amenities.append(a)
    return kept


def rank(items: list[Listing], sort: str = "price") -> list[Listing]:
    if sort == "distance":
        return sorted(items, key=lambda x: (x.distance_center_m is None, x.distance_center_m or 0))
    if sort == "landmark":
        return sorted(items, key=lambda x: (x.distance_landmark_m is None, x.distance_landmark_m or 0))
    if sort == "rating":
        return sorted(items, key=lambda x: (-(x.rating or 0) / (x.rating_scale or 5), x.total is None, x.total or 0))
    return sorted(items, key=lambda x: (_STATUS_RANK.get(x.price_status, 3), x.total is None, x.total or 0,
                                        x.distance_center_m or 1e9))


def search(q: StayQuery, *, cancel=None, progress: Optional[Callable[[str, dict], None]] = None,
           landmark: Optional[dict] = None, providers=None) -> dict:
    """Run all configured listing/discovery providers concurrently."""
    progress = progress or (lambda *_: None)
    all_providers = providers or registry()
    active = [p for p in all_providers if p.kind != "link" and p.configured()]
    skipped = [{"provider": p.key, "name": p.name, "status": "not_configured",
                "needs": list(p.needs)} for p in all_providers if p.kind != "link" and not p.configured()]
    links = [{"provider": p.key, "name": p.name, "url": p.link(q)} for p in all_providers if p.kind == "link"]
    results: list[Listing] = []
    answered, failures = [], []
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, len(active)), thread_name_prefix="stay") as pool:
        futures = {pool.submit(p.search, q, cancel): p for p in active}
        for p in active:
            progress("provider_started", {"provider": p.key, "name": p.name})
        try:
            for fut in as_completed(futures, timeout=PROVIDER_DEADLINE):
                p = futures[fut]
                try:
                    found = fut.result()
                    results.extend(found)
                    answered.append({"provider": p.key, "name": p.name, "count": len(found)})
                    progress("provider_done", {"provider": p.key, "name": p.name, "count": len(found)})
                except Exception as exc:
                    failures.append(fail(p, exc))
                    progress("provider_failed", {"provider": p.key, "name": p.name, "error": str(exc)[:160]})
                if cancel is not None and cancel.is_set():
                    break
        except TimeoutError:
            for fut, p in futures.items():
                if not fut.done():
                    fut.cancel()
                    failures.append(fail(p, TimeoutError(f"{p.name} did not answer within {int(PROVIDER_DEADLINE)} s")))
    progress("normalizing", {"count": len(results)})
    center = {"lat": q.lat, "lon": q.lon}
    for item in results:
        pricing.price_listing(item, q.nights, q.currency, cancel)
        if item.lat is not None and valid_point({"lat": item.lat, "lon": item.lon}):
            item.distance_center_m = round(haversine_m(center, {"lat": item.lat, "lon": item.lon}))
            if landmark:
                item.distance_landmark_m = round(haversine_m(landmark, {"lat": item.lat, "lon": item.lon}))
        else:
            item.lat = item.lon = None
        if item.capacity is not None and item.capacity < q.guests:
            item.flags.append("capacity_below_guests")
    merged = deduplicate(results)
    # Far-away OSM/Places matches (another town with the same street name) are dropped.
    merged = [m for m in merged if m.distance_center_m is None or m.distance_center_m < 25000]
    ranked = rank(merged, "price")
    cheapest_verified = next((x for x in ranked if x.price_status == "verified"), None)
    cheapest_any = next((x for x in ranked if x.price_status in ("verified", "indicative")), None)
    if cheapest_verified:
        cheapest_verified.flags.append("lowest_verified")
    elif cheapest_any:
        cheapest_any.flags.append("lowest_indicative")
    return {"query": q.__dict__, "listings": [x.to_dict() for x in ranked], "answered": answered,
            "failed": failures, "not_configured": skipped, "links": links, "fetched_at": now_iso(),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "fx": pricing.fx_note("EUR", q.currency) if q.currency != "EUR" else None,
            "bookings_made": 0}


def apply_filters(listings: list[dict], f: dict) -> list[dict]:
    """Backend twin of the interface filters, for follow-ups such as
    "samo one do 30 evra". Unknown values never pass a filter that needs them."""
    out = []
    for x in listings:
        if f.get("max_night") is not None and (x.get("night_price") is None or x["night_price"] > f["max_night"] + 1e-9):
            continue
        if f.get("max_total") is not None and (x.get("total") is None or x["total"] > f["max_total"] + 1e-9):
            continue
        if f.get("min_rating") is not None:
            r, s = x.get("rating"), x.get("rating_scale") or 5
            if r is None or (r / s * 5) < f["min_rating"]:
                continue
        if f.get("types") and x.get("property_type") not in f["types"]:
            continue
        if f.get("amenities") and not set(f["amenities"]) <= set(x.get("amenities") or []):
            continue
        if f.get("max_distance_m") is not None and (x.get("distance_center_m") is None
                                                    or x["distance_center_m"] > f["max_distance_m"]):
            continue
        if f.get("free_cancellation") and "free cancellation" not in (x.get("cancellation") or "").lower():
            continue
        if f.get("priced_only") and x.get("price_status") == "unavailable":
            continue
        out.append(x)
    return out
