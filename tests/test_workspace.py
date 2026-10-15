"""Workspace 2.0 tests. Offline: every provider response is a fixture.

Covers the critical flows end to end — message → intent → MCP tool call
(in-process MCP server) → provider → typed component → answer text — plus the
honesty rules (no invented distances or prices), follow-up context, an
unavailable MCP server, and the security boundaries of the web interface.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.integrations import store
from core.workspace import components as C
from core.workspace import dates, geo, http, intent, mcp_registry, service, storage, text
from core.workspace.http import ProviderError
from core.workspace.stays import aggregate, pricing
from core.workspace.stays.base import Listing, StayQuery
from core.workspace.stays.staninadan import StanNaDan

TODAY = date(2026, 10, 10)

# ── provider fixtures ───────────────────────────────────────────────────────
PLACES = {
    "Kuršumlija": ("town", 43.1380502, 21.2717791), "Vrbas": ("town", 45.568028, 19.6470089),
    "Niš": ("city", 43.3211, 21.8959), "Beograd": ("city", 44.8178, 20.4569),
}


def _nominatim(q):
    for name, (kind, lat, lon) in PLACES.items():
        if text.fold(name) == text.fold(q):
            return [{"name": name, "lat": str(lat), "lon": str(lon), "addresstype": kind, "category": "boundary",
                     "importance": 0.6, "display_name": f"{name}, Srbija", "address": {"country_code": "rs"},
                     "boundingbox": [str(lat - .02), str(lat + .02), str(lon - .02), str(lon + .02)]}]
    return []


def _card(pid, title, price, lat_note=""):
    price_html = f"Cena od: {price:.2f}  €" if price is not None else ""
    return (f'<div class="property-listing property-2"><a href="smestaj/{title.lower().replace(" ", "-")}-{pid}" title="{title}">'
            f'<img src="https://staninadan.com/images/{pid}/a.webp" /></a>'
            f'<span class="_list_blickes types">apartmani</span>'
            f'<h4 class="listing-name verified"><a href="smestaj/x-{pid}" class="prt-link-detail">{title}</a></h4>'
            f'<i class="fas fa-star"></i><i class="fas fa-star"></i><i class="far fa-star"></i>'
            f'<div>2 ležaja</div><div>{price_html}</div></div>')


LISTINGS = {  # id: (title, price per night or None, lat, lon, amenities)
    "1380": ("Apartman Snezana", 12.0, 43.3000, 21.9000, ["Internet", "Parking", "Kuhinja"]),
    "2426": ("Studio apartman Square", 20.0, 43.3202, 21.8954, ["Internet", "Klima"]),
    "2408": ("Apartman Bojke", 35.0, 43.3300, 21.8700, ["TV"]),
    "2500": ("Stan bez cene", None, 43.3100, 21.9100, []),
}
LIST_HTML = "<html>" + "".join(_card(k, v[0], v[1]) for k, v in LISTINGS.items()) + "</html>"


def _detail(pid):
    title, price, lat, lon, amen = LISTINGS[pid]
    price_html = f"Cena od: {price:.2f} € do {price + 10:.2f} €" if price is not None else ""
    lis = "".join(f'<li class="col"><i class="fa"></i>{a}</li>' for a in amen)
    return (f"<html><head><meta property=\"og:description\" content=\"Opis {title}\"/></head><body>"
            f'<span><i class="lni-map-marker"></i> <a href="nis">Niš</a> Ulica {pid} 18000 Nis</span>'
            f"<ul><li>{price_html}</li></ul>"
            f'<div class="property_block_wrap">Kapacitet smeštaja: 3 osobe'
            f"<h4 class=\"property_block_title\">Izdvajamo Sadržaj</h4><ul>{lis}</ul></div>"
            f"<script>var map = L.map('map').setView([{lat},{lon}], 13);</script></body></html>")


OSM_STAYS = {"elements": [
    {"type": "node", "id": 1, "lat": 43.32025, "lon": 21.89545, "tags": {"tourism": "apartment", "name": "Square Studio Apartman"}},
    {"type": "node", "id": 2, "lat": 43.3215, "lon": 21.8960, "tags": {"tourism": "hotel", "name": "Hotel Ambasador", "stars": "4"}},
]}
OSM_RESTAURANTS = {"elements": [
    {"type": "node", "id": 10, "lat": 45.5740, "lon": 19.6405, "tags": {"amenity": "restaurant", "name": "Gradska kafana"}},
    {"type": "node", "id": 11, "lat": 45.5690, "lon": 19.6475, "tags": {"amenity": "restaurant", "name": "Лovac"}},
]}


class FakeNet:
    """Stands in for core.workspace.http.request. Records every call."""

    def __init__(self):
        self.calls = []
        self.fail = set()

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw.get("params"), kw.get("data")))
        for marker in self.fail:
            if marker in url:
                raise ProviderError(f"{marker} unavailable", status="offline")
        params = kw.get("params") or {}
        if "nominatim" in url:
            return _nominatim(params.get("q", ""))
        if "router.project-osrm.org" in url:
            return {"code": "Ok", "routes": [{"distance": 393612.4, "duration": 16634.5,
                                              "geometry": {"coordinates": [[21.27, 43.14], [20.5, 44.3], [19.64, 45.57]]},
                                              "legs": [{"summary": "A1"}]}]}
        if "routed-foot" in url:
            return {"code": "Ok", "routes": [{"distance": 169.0, "duration": 121.0,
                                              "geometry": {"coordinates": [[21.8954, 43.3202], [21.8959, 43.3211]]},
                                              "legs": [{"summary": ""}]}]}
        if "overpass" in url:
            q = (kw.get("data") or {}).get("data", "")
            return OSM_RESTAURANTS if "restaurant" in q else OSM_STAYS if "tourism" in q else {"elements": []}
        if url.endswith("gradovi-srbije"):
            return '<a href="nis" title="Stan na dan Nis">Niš</a><a href="vrbas" title="Vrbas apartmani">Vrbas</a>'
        if url.endswith("staninadan.com/nis"):
            return LIST_HTML
        if "staninadan.com/smestaj/" in url:
            return _detail(url.rsplit("-", 1)[1])
        if "kurs.resenje.org" in url:
            return {"exchange_middle": 117.3657, "parity": 1}
        raise ProviderError("unexpected URL in test: " + url)


class WorkspaceCase(unittest.TestCase):
    """Isolated storage, credential store and MCP registry (in-process server)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        storage.set_data_dir(Path(self.tmp.name) / "ws")
        self.patches = [patch.object(store, "DATA", Path(self.tmp.name) / "cred"),
                        patch.object(dates, "today", return_value=TODAY),
                        patch.object(mcp_registry, "default_config", return_value=[{
                            "id": mcp_registry.BUILTIN_ID, "name": "ULTRON Workspace", "transport": "inproc",
                            "enabled": True, "builtin": True, "description": "test"}]),
                        patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": "", "AMADEUS_CLIENT_ID": ""})]
        self.net = FakeNet()
        self.patches.append(patch.object(http, "request", self.net))
        for p in self.patches:
            p.start()
        http._memory_cache.clear()
        http._disk_caches.clear()
        mcp_registry.reset_for_tests()

    def tearDown(self):
        mcp_registry.reset_for_tests()
        for p in reversed(self.patches):
            p.stop()
        storage.set_data_dir(None)
        self.tmp.cleanup()

    # Run one message through the real orchestrator and wait for its answer.
    def ask(self, orch, cid, message, timeout=60):
        done, box = threading.Event(), {}

        def listen(ev):
            if ev.get("type") in ("task.completed", "task.cancelled") and ev.get("conversation_id") == box.get("cid"):
                box["msg"] = ev["message"]
                done.set()
        orch.emit_cb = listen
        res = orch.submit(cid, message)
        box["cid"] = res["conversation_id"]
        self.assertTrue(done.wait(timeout), "no answer for: " + message)
        return res["conversation_id"], box["msg"]

    def comps(self, msg, kind):
        return [c for c in msg["components"] if c["kind"] == kind]


# ── language ────────────────────────────────────────────────────────────────
class LanguageTests(unittest.TestCase):
    def test_declined_serbian_place_names_resolve_to_base_form(self):
        for given, base in [("Kuršumlije", "Kuršumlija"), ("Vrbasa", "Vrbas"), ("Nišu", "Niš"), ("Novom Sadu", "Novi Sad"),
                            ("Kragujevca", "Kragujevac"), ("Sremskoj Mitrovici", "Sremska Mitrovica"),
                            ("Sremskim Karlovcima", "Sremski Karlovci"), ("Нишу", "Niš")]:
            self.assertEqual(text.place_lemma(given)[0], base, given)

    def test_dates_relative_to_today(self):
        span = dates.parse("za 16. oktobar", TODAY)
        self.assertEqual(span.check_in, date(2026, 10, 16))
        self.assertIsNone(span.check_out)
        self.assertEqual(dates.parse("5. januar", TODAY).check_in, date(2027, 1, 5))
        rng = dates.parse("od 16. do 18. oktobra", TODAY)
        self.assertEqual((rng.check_in, rng.check_out, rng.nights), (date(2026, 10, 16), date(2026, 10, 18), 2))
        self.assertEqual(dates.parse("za sledeći petak", TODAY).check_in, date(2026, 10, 16))
        self.assertEqual(dates.parse("Нађи стан за 16. октобар", TODAY).check_in, date(2026, 10, 16))

    def test_language_detection_ignores_serbian_letters_in_place_names(self):
        self.assertTrue(text.is_serbian("Koliko ima od Kuršumlije do Vrbasa?"))
        self.assertFalse(text.is_serbian("Find an affordable apartment in Niš and show me the centre"))


class IntentTests(unittest.TestCase):
    ROUTE_CTX = {"route": {"origin": {"name": "Kuršumlija", "lat": 43.13, "lon": 21.27},
                           "destination": {"name": "Vrbas", "lat": 45.57, "lon": 19.64},
                           "origin_query": "Kuršumlije", "destination_query": "Vrbasa", "mode": "driving"}}
    STAY_CTX = {"stays": {"query": {"city": "Niš", "check_in": "2026-10-16", "check_out": "2026-10-17", "nights": 1, "guests": 1},
                          "selected": None, "listings": []}}

    def u(self, message, ctx=None):
        return intent.understand(message, ctx or {}, TODAY)

    def test_route_question(self):
        i = self.u("Koliko ima od Kuršumlije do Vrbasa?")
        self.assertEqual((i.kind, i.entities["origin"], i.entities["destination"]), ("route", "Kuršumlije", "Vrbasa"))

    def test_stay_request_with_date(self):
        i = self.u("Nađi mi najjeftiniji stan na dan u Nišu za 16. oktobar.")
        self.assertEqual(i.kind, "stays")
        self.assertEqual(i.entities["city"], "Nišu")
        self.assertEqual((i.entities["check_in"], i.entities["check_out"]), ("2026-10-16", "2026-10-17"))
        self.assertIn("one_night", i.assumptions)
        self.assertIn("default_guests", i.assumptions)

    def test_followups_reuse_context(self):
        self.assertEqual(self.u("A koliko treba autobusom?", self.ROUTE_CTX).entities["mode"], "transit")
        self.assertEqual(self.u("A koliko treba autobusom?", self.ROUTE_CTX).entities["destination"], "Vrbasa")
        there = self.u("Nađi mi tamo stan na dan za 16. oktobar.", self.ROUTE_CTX)
        self.assertEqual((there.kind, there.entities["city"]), ("stays", "Vrbasa"))
        near = self.u("Pokaži mi restorane blizu destinacije.", self.ROUTE_CTX)
        self.assertEqual((near.kind, near.entities["anchor"]["name"]), ("nearby", "Vrbas"))
        budget = self.u("Samo one do 30 evra.", self.STAY_CTX)
        self.assertEqual((budget.kind, budget.entities["budget"]["amount"]), ("stays_filter", 30.0))
        self.assertEqual(self.u("Koji je najbliži centru?", self.STAY_CTX).kind, "stays_sort")
        self.assertEqual(self.u("Pokaži mi najjeftiniji apartman na mapi.", self.STAY_CTX).entities["which"], "cheapest")
        self.assertEqual(self.u("Daj mi link za rezervaciju.", self.STAY_CTX).kind, "stays_link")

    def test_a_date_is_never_read_as_a_budget(self):
        i = self.u("hotel u Beogradu za 2 osobe od 20. do 22. oktobra do 100 evra")
        self.assertEqual(i.entities["budget"]["amount"], 100.0)
        self.assertEqual(i.entities["guests"], 2)

    def test_multi_step_request_keeps_the_landmark(self):
        i = self.u("Nađi mi najjeftiniji apartman u Novom Sadu za petak, pokaži gde se nalazi i koliko je daleko od autobuske stanice.")
        self.assertEqual(i.kind, "stays")
        self.assertIn("autobusk", i.entities["landmark"])
        self.assertEqual(i.entities["check_in"], "2026-10-16")


# ── providers ───────────────────────────────────────────────────────────────
class ProviderTests(WorkspaceCase):
    def test_route_metrics_are_copied_from_the_router(self):
        a, b = geo.geocode("Kuršumlije"), geo.geocode("Vrbasa")
        r = geo.route(a, b, "driving")
        self.assertEqual(r["routes"][0]["distance_m"], 393612.4)
        self.assertEqual(r["routes"][0]["duration_s"], 16634.5)
        self.assertIn("OSRM", r["provider"])
        self.assertFalse(r["traffic"])

    def test_transit_without_google_is_refused_not_estimated(self):
        a, b = geo.geocode("Kuršumlije"), geo.geocode("Vrbasa")
        with self.assertRaises(ProviderError) as ctx:
            geo.route(a, b, "transit")
        self.assertEqual(ctx.exception.status, "unsupported")
        self.assertFalse(any("osrm" in c[1] for c in self.net.calls if "transit" in str(c)))

    def test_google_walking_route_has_no_traffic_preference(self):
        sent = {}

        def fake_post(url, **kw):
            sent.update(kw["json_body"])
            return {"routes": [{"distanceMeters": 1200, "duration": "900s", "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC"}}]}
        with patch.object(geo, "google_key", return_value="test-key"), patch.object(http, "post_json", fake_post):
            r = geo.route({"lat": 45.25, "lon": 19.84}, {"lat": 45.57, "lon": 19.64}, "walking")
        self.assertNotIn("routingPreference", sent)
        self.assertEqual(r["routes"][0]["distance_m"], 1200)
        self.assertEqual(r["provider"], "Google Routes")

    def test_staninadan_parses_public_listing_and_detail(self):
        q = StayQuery(city="Niš", lat=43.3211, lon=21.8959, check_in="2026-10-16", check_out="2026-10-17", nights=1)
        rows = {r.id: r for r in StanNaDan().search(q)}
        sq = rows["staninadan:2426"]
        self.assertEqual((sq.price_night, sq.price_currency, sq.price_basis), (20.0, "EUR", "from"))
        self.assertEqual((sq.lat, sq.lon), (43.3202, 21.8954))
        self.assertEqual(sq.capacity, 3)
        self.assertIn("ac", sq.amenities)
        self.assertTrue(sq.url.startswith("https://staninadan.com/smestaj/"))
        self.assertEqual(sq.availability, "unknown")           # the site has no availability calendar
        self.assertIsNone(rows["staninadan:2500"].price_night)  # no price published → none invented

    def test_one_failing_source_still_returns_the_others(self):
        self.net.fail.add("overpass")
        q = StayQuery(city="Niš", lat=43.3211, lon=21.8959, check_in="2026-10-16", check_out="2026-10-17", nights=1)
        out = aggregate.search(q)
        self.assertEqual([a["provider"] for a in out["answered"]], ["staninadan"])
        self.assertEqual(out["failed"][0]["provider"], "osm")
        self.assertTrue(out["listings"])
        self.assertEqual(out["bookings_made"], 0)

    def test_ranking_normalisation_and_deduplication(self):
        q = StayQuery(city="Niš", lat=43.3211, lon=21.8959, check_in="2026-10-16", check_out="2026-10-18", nights=2)
        out = aggregate.search(q)
        ls = out["listings"]
        priced = [x for x in ls if x["price_status"] != "unavailable"]
        self.assertEqual([x["total"] for x in priced], sorted(x["total"] for x in priced))
        self.assertEqual(priced[0]["total"], 24.0)                         # 12 € × 2 nights
        self.assertIn("lowest_indicative", priced[0]["flags"])
        self.assertFalse(any("lowest_verified" in x["flags"] for x in ls))  # nothing here is a verified quote
        square = next(x for x in ls if x["id"] == "staninadan:2426")
        self.assertEqual([a["provider"] for a in square["also_on"]], ["OpenStreetMap"])   # same flat on two sources
        self.assertEqual(ls[-1]["price_status"], "unavailable")
        self.assertEqual({l["name"] for l in out["links"]}, {"Booking.com", "Airbnb"})

    def test_total_includes_mandatory_fees_and_verified_quotes_rank_first(self):
        cheap_with_fee = Listing(id="a", provider="p", provider_name="P", title="A", url="https://a.example",
                                 price_night=25, price_currency="EUR", price_basis="from",
                                 fees=[{"label": "cleaning", "amount": 15, "currency": "EUR", "mandatory": True}])
        flat = Listing(id="b", provider="p", provider_name="P", title="B", url="https://b.example",
                       price_night=32, price_currency="EUR", price_basis="from")
        quote = Listing(id="c", provider="q", provider_name="Q", title="C", url="https://c.example",
                        stay_total_quote=60, price_currency="EUR", price_basis="quote")
        for x in (cheap_with_fee, flat, quote):
            pricing.price_listing(x, 1, "EUR")
        self.assertEqual((cheap_with_fee.total, flat.total), (40, 32))       # €25 + €15 fee costs more than €32
        ranked = aggregate.rank([cheap_with_fee, flat, quote])
        self.assertEqual([x.id for x in ranked], ["c", "b", "a"])
        self.assertEqual(quote.price_status, "verified")

    def test_budget_filter_never_admits_unknown_prices(self):
        ls = [{"night_price": 12, "total": 12}, {"night_price": None, "total": None}, {"night_price": 35, "total": 35}]
        self.assertEqual(aggregate.apply_filters(ls, {"max_night": 30}), [ls[0]])


# ── end-to-end scenarios ────────────────────────────────────────────────────
class ScenarioTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        from core.workspace.orchestrator import Orchestrator
        self.orch = Orchestrator(lambda ev: None)

    def test_1_route_question_returns_a_real_route_map(self):
        cid, msg = self.ask(self.orch, None, "Koliko ima od Kuršumlije do Vrbasa?")
        (card,) = self.comps(msg, "route_map")
        d = card["data"]
        self.assertEqual((d["origin"]["name"], d["destination"]["name"]), ("Kuršumlija", "Vrbas"))
        self.assertEqual(d["routes"][0]["distance_m"], 393612.4)
        self.assertGreater(len(d["routes"][0]["geometry"]), 1)
        self.assertTrue(d["open_url"].startswith("https://www.google.com/maps/dir/"))
        self.assertIn("393,6 km", msg["text"])
        self.assertIn("4 h 37 min", msg["text"])
        self.assertTrue(any("router.project-osrm.org" in c[1] for c in self.net.calls))
        # Follow-up by bus: honest refusal, no numbers made up.
        cid, bus = self.ask(self.orch, cid, "A koliko treba autobusom?")
        self.assertFalse(self.comps(bus, "route_map"))
        self.assertTrue(self.comps(bus, "link_card"))
        self.assertNotRegex(bus["text"], r"\d+\s*(min|h)\b")

    def test_2_to_4_stays_follow_ups_keep_city_and_dates(self):
        cid, msg = self.ask(self.orch, None, "Nađi mi najjeftiniji stan na dan u Nišu za 16. oktobar.")
        (card,) = self.comps(msg, "stay_results")
        d = card["data"]
        self.assertEqual((d["query"]["city"], d["query"]["check_in"], d["query"]["check_out"], d["query"]["guests"]),
                         ("Niš", "2026-10-16", "2026-10-17", 1))
        self.assertIn("one_night", d["assumptions"])
        priced = [x for x in d["listings"] if x["price_status"] != "unavailable"]
        self.assertEqual(priced[0]["title"], "Apartman Snezana")
        self.assertTrue(all(x["url"].startswith("https://") for x in d["listings"]))
        self.assertIn("indikativna", msg["text"])
        self.assertNotIn("potvrđena ponuda", msg["text"])

        # Test 3: budget follow-up applies to the same search.
        calls_before = len(self.net.calls)
        cid, filt = self.ask(self.orch, cid, "Samo one do 30 evra.")
        (fcard,) = self.comps(filt, "stay_results")
        self.assertEqual(fcard["data"]["filters"]["max_night"], 30.0)
        self.assertEqual((fcard["data"]["query"]["city"], fcard["data"]["query"]["check_in"]), ("Niš", "2026-10-16"))
        self.assertEqual(len(self.net.calls), calls_before)          # filtered locally, no new provider calls
        kept = aggregate.apply_filters(fcard["data"]["listings"], fcard["data"]["filters"])
        self.assertEqual({x["title"] for x in kept}, {"Apartman Snezana", "Studio apartman Square"})

        # Closest to the centre: straight line, then a real walking route.
        cid, near = self.ask(self.orch, cid, "Koji je najbliži centru?")
        self.assertIn("Studio apartman Square", near["text"])
        self.assertIn("169 m", near["text"])

        # Test 4: the cheapest on the map, from the current results.
        cid, focus = self.ask(self.orch, cid, "Pokaži mi najjeftiniji apartman na mapi.")
        (fc,) = self.comps(focus, "property_focus")
        self.assertEqual((fc["data"]["listing"]["title"], fc["data"]["listing"]["lat"]), ("Apartman Snezana", 43.3))

        cid, link = self.ask(self.orch, cid, "Daj mi link za rezervaciju.")
        (lc,) = self.comps(link, "link_card")
        self.assertTrue(lc["data"]["links"][0]["url"].startswith("https://staninadan.com/smestaj/"))
        self.assertIn("nema online rezervaciju", lc["data"]["note"])

    def test_there_after_a_route_means_the_destination(self):
        cid, _ = self.ask(self.orch, None, "Koliko ima od Kuršumlije do Vrbasa?")
        cid, msg = self.ask(self.orch, cid, "Pokaži mi restorane blizu destinacije.")
        (pc,) = self.comps(msg, "place_results")
        names = [p["name"] for p in pc["data"]["places"]]
        self.assertEqual(names, ["Lovac", "Gradska kafana"])                 # nearest first; Cyrillic shown in Latin
        self.assertLess(pc["data"]["places"][0]["distance_m"], pc["data"]["places"][1]["distance_m"])

    def test_5_unavailable_mcp_server_gives_an_honest_error(self):
        with patch.object(mcp_registry.MCPRegistry, "call",
                          side_effect=mcp_registry.MCPToolError("ULTRON Workspace is offline", "offline")):
            cid, msg = self.ask(self.orch, None, "Koliko ima od Kuršumlije do Vrbasa?")
        self.assertEqual(msg["status"], "error")
        self.assertFalse(self.comps(msg, "route_map"))
        self.assertTrue(self.comps(msg, "error_recovery"))
        self.assertNotIn("km", msg["text"])

    def test_route_without_origin_asks_and_accepts_the_answer(self):
        cid, ask = self.ask(self.orch, None, "Koliko ima do Vrbasa?")
        self.assertIn("Odakle", ask["text"])
        self.assertFalse(ask["components"])
        cid, msg = self.ask(self.orch, cid, "Iz Kuršumlije")
        self.assertTrue(self.comps(msg, "route_map"))

    def test_cancel_stops_a_task(self):
        gate = threading.Event()
        real = self.net.__call__

        def slow(method, url, **kw):
            gate.wait(5)
            return real(method, url, **kw)
        with patch.object(http, "request", slow):
            done = threading.Event()
            box = {}
            self.orch.emit_cb = lambda ev: (box.update(ev), done.set()) if ev["type"] in ("task.cancelled", "task.completed") else None
            res = self.orch.submit(None, "Koliko ima od Kuršumlije do Vrbasa?")
            time.sleep(0.2)
            self.assertTrue(self.orch.cancel(res["task_id"]))
            gate.set()
            self.assertTrue(done.wait(30))
        self.assertIn(box["message"]["status"], ("cancelled", "error"))
        self.assertFalse(self.comps(box["message"], "route_map"))


class MCPRegistryTests(WorkspaceCase):
    def test_builtin_tools_are_listed_with_schemas_and_read_only(self):
        reg = mcp_registry.registry()
        reg.start()
        reg.ensure(mcp_registry.BUILTIN_ID)
        tools = {t["name"]: t for t in reg.tools()}
        self.assertEqual(set(tools), {"geocode", "route", "nearby_places", "stay_search", "exchange_rate", "stay_providers"})
        self.assertTrue(all(t["read_only"] for t in tools.values()))
        self.assertIn("origin", tools["route"]["input_schema"]["properties"])
        self.assertFalse(any(re.search(r"book|reserv|pay", n) for n in tools))

    def test_invalid_arguments_are_rejected_before_the_call(self):
        reg = mcp_registry.registry()
        with self.assertRaises(mcp_registry.MCPToolError) as ctx:
            reg.call(mcp_registry.BUILTIN_ID, "route", {"origin": 1})
        self.assertEqual(ctx.exception.status, "invalid_args")

    def test_unreachable_server_is_reported_not_connected(self):
        bad = {"id": "broken", "name": "Broken", "transport": "stdio", "command": "definitely-not-a-real-binary-xyz",
               "args": [], "enabled": True, "builtin": False}
        with patch.object(mcp_registry, "load_config", return_value=mcp_registry.default_config() + [bad]):
            reg = mcp_registry.registry()
            reg.start()
            with self.assertRaises(mcp_registry.MCPToolError):
                reg.call("broken", "anything", {})
            row = next(s for s in reg.servers() if s["id"] == "broken")
        self.assertIn(row["status"], ("error", "offline"))
        self.assertNotEqual(row["status"], "connected")
        self.assertTrue(row["error"])


# ── security ────────────────────────────────────────────────────────────────
class SecurityTests(WorkspaceCase):
    def test_components_keep_only_https_links(self):
        for bad in ("javascript:alert(1)", "http://example.com", "https://user:pw@example.com/", "data:text/html,x", ""):
            self.assertEqual(C.url(bad), "", bad)
        self.assertEqual(C.url("https://staninadan.com/nis"), "https://staninadan.com/nis")
        listing = C.listing({"title": "<img src=x onerror=alert(1)>", "url": "javascript:alert(1)",
                             "image": "http://insecure.example/a.jpg", "price_status": "made-up", "lat": "999"})
        self.assertEqual(listing["url"], "")
        self.assertEqual(listing["image"], "")
        self.assertEqual(listing["price_status"], "unavailable")
        self.assertIsNone(listing["lat"])
        self.assertEqual(listing["title"], "<img src=x onerror=alert(1)>")   # kept as text; the UI never parses HTML

    def test_interface_scripts_never_inject_html(self):
        web = ROOT / "core" / "workspace" / "web"
        for js in (web / "js").glob("*.js"):
            src = js.read_text(encoding="utf-8")
            self.assertNotRegex(src, r"\.innerHTML\s*=|insertAdjacentHTML|outerHTML\s*=|document\.write|\beval\(|new Function",
                                js.name)
        csp = re.search(r'Content-Security-Policy"\s+content="([^"]+)"', (web / "index.html").read_text(encoding="utf-8")).group(1)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("object-src 'none'", csp)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_interface_scripts_parse(self):
        for js in (ROOT / "core" / "workspace" / "web" / "js").glob("*.js"):
            r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, js.name + ": " + r.stderr)

    def test_credentials_never_reach_the_web_view(self):
        store.set_secret("google_maps_api_key", "AIzaTEST-not-a-real-key-123456789")
        payload = json.dumps(service.handle({"type": "init"}), default=str)
        payload += json.dumps(service.handle({"type": "mcp.list"}), default=str)
        payload += json.dumps(service.handle({"type": "routing.get"}), default=str)
        self.assertNotIn("AIzaTEST", payload)

    def test_only_https_links_open_and_unknown_requests_fail(self):
        opened = []
        service.set_native("open_url", opened.append)
        try:
            self.assertFalse(service.handle({"type": "open.url", "url": "file:///C:/Windows/system.ini"})["ok"])
            self.assertFalse(service.handle({"type": "open.url", "url": "javascript:alert(1)"})["ok"])
            self.assertTrue(service.handle({"type": "open.url", "url": "https://staninadan.com/nis"})["ok"])
        finally:
            service.set_native("open_url", None)
        self.assertEqual(opened, ["https://staninadan.com/nis"])
        self.assertFalse(service.handle({"type": "exec", "cmd": "dir"})["ok"])
        self.assertFalse(service.handle("not json")["ok"])
        self.assertFalse(service.handle({"type": "conversation.load", "id": "../../config/api_keys"})["ok"])

    def test_no_booking_or_payment_capability_exists(self):
        from core.workspace import orchestrator
        src = Path(orchestrator.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(src, r"def _(book|reserve|pay)\w*\(")
        self.assertNotIn("book", " ".join(service._HANDLERS))

    def test_dev_server_requires_token_and_stays_inside_web_folder(self):
        from http.server import ThreadingHTTPServer
        import urllib.request
        import urllib.error
        from core.workspace import devserver
        srv = ThreadingHTTPServer(("127.0.0.1", 0), devserver.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            def status(req):
                try:
                    return urllib.request.urlopen(req, timeout=10).status
                except urllib.error.HTTPError as e:
                    return e.code
            self.assertEqual(status(base + "/index.html"), 200)
            self.assertEqual(status(base + "/../../config/api_keys.json"), 404)
            self.assertEqual(status(base + "/%2e%2e/%2e%2e/config/api_keys.json"), 404)
            self.assertEqual(status(urllib.request.Request(base + "/api", data=b'{"type":"init"}', method="POST")), 403)
            ok = urllib.request.Request(base + "/api", data=b'{"type":"status"}', method="POST",
                                        headers={"X-Ultron-Token": devserver.TOKEN})
            self.assertEqual(status(ok), 200)
        finally:
            srv.shutdown()


class BridgeThreadingTests(unittest.TestCase):
    """Regression: events produced on worker threads must reach the page's
    channel on the GUI thread (emitting on the worker thread crashed Qt)."""

    def test_response_events_arrive_on_the_gui_thread(self):
        from PyQt6.QtCore import QCoreApplication, Qt
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        app = QApplication.instance() or QApplication([])
        from core.workspace.window import WorkspaceBridge
        bridge = WorkspaceBridge()
        got = []
        bridge.event.connect(lambda s: got.append((json.loads(s), threading.current_thread() is threading.main_thread())))
        accepted = json.loads(bridge.request(json.dumps({"type": "settings.get", "_rid": "r1"})))
        self.assertTrue(accepted["ok"])
        deadline = time.time() + 15
        while time.time() < deadline and not any(e.get("rid") == "r1" for e, _ in got):
            app.processEvents()
            time.sleep(0.02)
        bridge.close()
        resp = [(e, main) for e, main in got if e.get("rid") == "r1"]
        self.assertTrue(resp, "no response event")
        self.assertTrue(resp[0][1], "event was not delivered on the GUI thread")
        self.assertTrue(resp[0][0]["result"]["ok"])
        self.assertNotIn("_deliver", [m for m in dir(bridge) if not m.startswith("__")
                                      and callable(getattr(bridge, m)) and hasattr(getattr(bridge, m), "__pyqtSignature__")])


if __name__ == "__main__":
    unittest.main()
