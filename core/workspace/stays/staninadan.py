"""staninadan.com — Serbian short-term apartments ("stan na dan").

Source rules (staninadan.com/uslovi-koriscenja): reusing listing text is
permitted with the source named and a link back to the listing, which every
card here carries. robots.txt publishes no restrictions. Access stays polite:
an identifying User-Agent, at most one request per 0.6 s, cached pages, and
only pages a visitor sees without signing in.

What the site publishes is an advertised "Cena od" (price from) per unit and
night — not a quote for given dates — and no availability calendar. Listings
are therefore always marked indicative / availability unknown.
"""
from __future__ import annotations

import html as htmllib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from core.workspace import http
from core.workspace.geo import now_iso
from core.workspace.http import ProviderError
from core.workspace.stays.base import HEALTH, Listing, Provider, StayQuery
from core.workspace.text import fold

BASE = "https://staninadan.com/"
CITY_TTL = 7 * 24 * 3600
PAGE_TTL = 30 * 60
DETAIL_TTL = 6 * 3600
MAX_DETAILS = 14

_AMENITY = [
    (r"internet|wi-?fi|wireless", "wifi"), (r"parking|garaz", "parking"), (r"kuhinj|cajna kuhinj", "kitchen"),
    (r"klim", "ac"), (r"\btv\b|televiz|kablovsk", "tv"), (r"ves masin|masina za ves", "washer"),
    (r"teras|balkon", "balcony"), (r"\blift\b", "elevator"), (r"ljubimc|kucni ljub", "pets"),
    (r"kupatil", "bathroom"), (r"grejanj|centralno", "heating"), (r"dorucak", "breakfast"),
    (r"bazen", "pool"), (r"djakuzi|jacuzzi|hidromasaz", "jacuzzi"),
]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _https(url: str) -> str:
    url = htmllib.unescape(url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url and not url.startswith("http"):
        url = BASE + url.lstrip("/")
    return url if url.startswith("https://") else ""


def _price(text: str) -> tuple[Optional[float], Optional[float], str]:
    m = re.search(r"Cena od:?\s*([\d.,]+)\s*(€|eur|din|rsd)(?:\s*do\s*([\d.,]+)\s*(€|eur|din|rsd))?", text, re.I)
    if not m:
        return None, None, ""

    def num(s):
        s = s.replace(" ", "")
        if s.count(",") == 1 and s.count(".") == 0:
            s = s.replace(",", ".")
        elif s.count(".") > 1 or (s.count(".") == 1 and s.count(",") == 1):
            s = s.replace(".", "").replace(",", ".")
        try:
            v = float(s)
            return v if 0 < v < 100000 else None
        except ValueError:
            return None

    cur = "EUR" if m.group(2).lower() in ("€", "eur") else "RSD"
    lo = num(m.group(1))
    hi = num(m.group(3)) if m.group(3) else None
    return lo, (hi if hi and lo and hi >= lo else None), cur


def _ptype(t: str) -> str:
    f = fold(t)
    if "hostel" in f:
        return "hostel"
    if "hotel" in f:
        return "hotel"
    if "kuc" in f or "vikendic" in f or "vila" in f:
        return "house"
    if "sob" in f and "apartman" not in f and "stan" not in f:
        return "room"
    if "apartman" in f or "stan" in f or "studio" in f:
        return "apartment"
    return "apartment"


class StanNaDan(Provider):
    key = "staninadan"
    name = "staninadan.com"
    kind = "listings"
    homepage = "https://staninadan.com/"
    attribution = "Izvor: staninadan.com"

    def city_slug(self, city: str, cancel=None) -> Optional[str]:
        page = http.get_text(BASE + "gradovi-srbije", ttl=CITY_TTL, provider=self.name,
                             cache=http.disk_cache("staninadan"), cancel=cancel)
        pairs = re.findall(r'<a href="/?([a-z0-9-]+)"[^>]*title="([^"]+)"', page)
        want = fold(city)
        for slug, title in pairs:
            name = re.sub(r"^(stan na dan|apartmani)\s+|\s+(smestaj|apartmani)$", "", fold(htmllib.unescape(title)))
            if name == want or slug == want.replace(" ", "-"):
                return slug
        return None

    def search(self, q: StayQuery, cancel=None) -> list[Listing]:
        if q.country and q.country != "rs":
            return []
        slug = self.city_slug(q.city, cancel)
        if not slug:
            raise ProviderError(f"staninadan.com has no section for {q.city}.", status="not_found",
                                provider=self.name)
        page = http.get_text(BASE + slug, ttl=PAGE_TTL, provider=self.name, cancel=cancel)
        cards = self._cards(page, q.city)
        detailed = cards[:MAX_DETAILS]
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="sn-detail") as pool:
            futures = [pool.submit(self._detail, c, cancel) for c in detailed]
            for f in futures:
                try:
                    f.result(timeout=40)
                except Exception:
                    pass          # the card keeps what the list page showed
        HEALTH.ok(self.key, len(cards))
        return cards

    def _cards(self, page: str, city: str) -> list[Listing]:
        out, seen = [], set()
        blocks = page.split('class="property-listing property-2"')[1:]
        for blk in blocks:
            blk = blk[:6000]
            m = re.search(r'href="(smestaj/[a-z0-9-]+-(\d+))"', blk)
            if not m or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            title_m = re.search(r'<h4[^>]*class="listing-name[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', blk, re.S)
            img_m = re.search(r'<img[^>]+src="([^"]+\.(?:webp|jpe?g|png))"', blk, re.I)
            type_m = re.search(r'_list_blickes types">\s*([^<]+)<', blk)
            beds_m = re.search(r"(\d+)\s*le[žz]aj", blk)
            lo, hi, cur = _price(_text(blk))
            stars = len(re.findall(r'class="fas fa-star"', blk))
            ptype_text = _text(type_m.group(1)) if type_m else ""
            out.append(Listing(
                id=f"{self.key}:{m.group(2)}", provider=self.key, provider_name=self.name,
                title=_text(title_m.group(1)) if title_m else "", url=_https(m.group(1)),
                image=_https(img_m.group(1)) if img_m else "", property_type=_ptype(ptype_text or "apartman"),
                city=city, price_night=lo, price_night_max=hi, price_currency=cur,
                price_basis="from" if lo else "", availability="unknown",
                stars=stars or None, beds=int(beds_m.group(1)) if beds_m else None,
                verified_at=now_iso(), attribution=self.attribution,
                description=ptype_text))
        return [c for c in out if c.title and c.url]

    def _detail(self, card: Listing, cancel=None) -> None:
        page = http.get_text(card.url, ttl=DETAIL_TTL, provider=self.name, cancel=cancel,
                             cache=http.disk_cache("staninadan"))
        head = page.split("property_block_wrap", 1)[0]
        m = re.search(r"setView\(\[\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*\]", page)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if 40 < lat < 47.5 and 18 < lon < 23.5:       # Serbia; ignore placeholder points
                card.lat, card.lon = lat, lon
        addr = re.search(r'lni-map-marker"></i>\s*<a[^>]*>.*?</a>\s*([^<]{3,160})</span>', head, re.S)
        if addr:
            card.address = _text(addr.group(1))
        lo, hi, cur = _price(_text(head))
        if lo:
            card.price_night, card.price_night_max, card.price_currency = lo, hi, cur
            card.price_basis = "from"
        cap = re.search(r"Kapacitet sme[šs]taja:\s*(\d+)", _text(page))
        if cap:
            card.capacity = int(cap.group(1))
        feat = re.search(r"Izdvajamo Sadr[žz]aj</h4>(.*?)</ul>", page, re.S)
        if feat:
            found = []
            for li in re.findall(r"<li[^>]*>(.*?)</li>", feat.group(1), re.S):
                f = fold(_text(li))
                for rx, key in _AMENITY:
                    if re.search(rx, f) and key not in found:
                        found.append(key)
            card.amenities = found
        desc = re.search(r'<meta property="og:description" content="([^"]*)"', page)
        if desc:
            card.description = htmllib.unescape(desc.group(1))[:400]
        img = re.search(r"<meta property='og:image' content='([^']+)'", page)
        if img and not card.image:
            card.image = _https(img.group(1))
        card.verified_at = now_iso()
