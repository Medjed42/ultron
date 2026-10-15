"""What the user is asking for, in Serbian (Latin or Cyrillic) or English.

`understand(text, context)` returns an Intent: a kind, the entities it found,
and the assumptions it made. It is deliberately conservative — it only claims
a tool intent when the words for it are present; anything else becomes
"chat", which the orchestrator hands to the AI planner together with the
live MCP tool catalogue. So common requests are instant and deterministic,
and unusual ones still reach a model that can choose tools.

Context (the previous route, stay search, selected property) resolves
follow-ups: "a autobusom?", "tamo", "samo do 30 evra", "pokaži ga na mapi".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from core.workspace import dates
from core.workspace.geo import detect_category
from core.workspace.text import fold, is_serbian, latin, lookup_place

# ── vocabulary (folded: lower case, no diacritics) ──────────────────────────

_MODE_WORDS = [
    ("transit", r"autobus\w*|busom|bus\b|javn\w* prevoz\w*|gradsk\w* prevoz\w*|vozom|vozu|by train|by bus|"
                r"public transport|transit|prevozom"),
    ("walking", r"peske|pesice|pjeske|hodanj\w*|setnj\w*|on foot|walk\w*|peske\b"),
    ("cycling", r"bicikl\w*|biciklom|by bike|cycl\w*|bike\b"),
    ("driving", r"kolima|autom|automobil\w*|vozilom|kola\b|drive|driving|by car|car\b|kolim"),
]

_ROUTE_RX = re.compile(
    r"\b(koliko\s+(ima|je|treba|traje|se\s+putuje|km|kilometara|daleko|sati|vremena)|"
    r"razdaljin\w*|udaljenost\w*|rastojanj\w*|ruta|rutu|putanj\w*|put\s+(od|iz|do)|kako\s+(da\s+)?(stignem|dodjem|odem|idem)|"
    r"navigacij\w*|navigiraj|vozi\s+me|"
    r"how\s+far|how\s+long|distance|directions?|route|drive\s+from|get\s+(from|to))\b")
_FROM_TO = re.compile(r"\b(?:od|iz|from|izmedju|between)\s+(?P<a>.+?)\s+(?:do|to|i|and|za|until|pa\s+do)\s+(?P<b>.+)$")
_TO_ONLY = re.compile(r"\b(?:do|to|za|until)\s+(?P<b>[^,.;!?]+)$")

_STAY_RX = re.compile(
    r"\b(stan\w*\s+na\s+dan|stan|stana|stanu|stanove|smestaj\w*|apartman\w*|hotel\w*|hostel\w*|motel\w*|prenociste\w*|nocenj\w*|"
    r"noćenj\w*|sobu|sobe\b|soba\b|vil[aeu]\b|vikendic\w*|accommodation|apartment\w*|"
    r"place\s+to\s+stay|somewhere\s+to\s+stay|a\s+stay|stays|airbnb|booking|lodging|guest\s*house|bnb|"
    r"spavanj\w*|da\s+prespavam|prespavati)\b")
_PLACES_RX = re.compile(r"\b(blizu|u\s+blizini|oko|near|nearby|around|close\s+to|u\s+okolini|pored|kod)\b")
_SHOW_MAP_RX = re.compile(r"\b(pokazi|prikazi|show|vidi|see|otvori|open)\b.*\b(mapi|mapu|karti|kartu|map)\b|"
                          r"\b(na\s+mapi|on\s+(the\s+)?map|gde\s+se\s+nalazi|where\s+is\s+it|gde\s+je)\b")
_LINK_RX = re.compile(r"\b(link\w*|rezervacij\w*|rezervisi|rezervisem|book\w*|booking\s+page|reserve|url)\b")
_CLOSEST_RX = re.compile(r"\b(najbliz\w*|closest|nearest|najmanje\s+udaljen\w*)\b")
_CHEAP_RX = re.compile(r"\b(najjeftin\w*|jeftin\w*|cheap\w*|lowest\s+price|budget|povoljn\w*|najpovoljn\w*|"
                       r"affordable|inexpensive|low[- ]cost)\b")
_RATING_RX = re.compile(r"\b(najbolje\s+ocenjen\w*|najbolj\w*\s+ocen\w*|highest\s+rated|best\s+rated|top\s+rated|ocen\w*)\b")
_BUDGET_RX = re.compile(
    r"(?:do|ispod|manje\s+od|max(?:imum|imalno)?|najvise|under|below|up\s+to|less\s+than|<)\s*"
    r"(?:€|eur\w*\s*)?(\d+(?:[.,]\d+)?)\s*(€|e\b|eur\w*|evr\w*|din\w*|rsd|dinar\w*|\$|usd|dollar\w*)?")
_TOTAL_RX = re.compile(r"\b(ukupno|ukupn\w*|total|za\s+sve|za\s+ceo\s+boravak|whole\s+stay)\b")
_GUESTS_RX = re.compile(r"\b(?:za\s+)?(\d{1,2})\s*(osob\w*|odrasl\w*|gost\w*|ljud\w*|people|persons?|guests?|adults?)\b")
_GUEST_WORDS = {"dvoje": 2, "troje": 3, "cetvoro": 4, "petoro": 5, "sestoro": 6, "dve osobe": 2,
                "two people": 2, "couple": 2, "par": 2, "nas dvoje": 2, "nas troje": 3, "sam": 1, "sama": 1,
                "alone": 1, "jednu osobu": 1, "one person": 1}

_EMAIL_RX = re.compile(r"\b(gmail|e-?mail\w*|mejl\w*|imejl\w*|inbox|posta\b|postu\b|poruk\w*\s+u\s+posti|"
                       r"sanduc\w*|unread|neprocitan\w*)\b")
_DRIVE_RX = re.compile(r"\b(google\s+drive|drive\b|gdrive|drajv\w*|moj\w*\s+(fajl\w*|dokument\w*|datotek\w*)|"
                       r"my\s+(files?|documents?|docs)|find\s+(a\s+)?(file|document)|pronadji\s+(fajl|dokument))\b")
_CAL_RX = re.compile(r"\b(kalendar\w*|raspored\w*|obavez\w*|sastan\w*|termin\w*|dogadjaj\w*|agenda|"
                     r"calendar|schedule|meeting\w*|appointment\w*|my\s+day|moj\s+dan)\b")
_RESEARCH_RX = re.compile(r"\b(istrazi|istrazivanj\w*|pretrazi\s+(internet|web|net)|potrazi\s+na\s+(internetu|netu|webu)|"
                          r"research|search\s+the\s+web|web\s+search|look\s+up|najnovij\w*\s+vest\w*|latest\s+news|"
                          r"news\s+about|vesti\s+o|sta\s+se\s+desava|what'?s\s+new|izvori|sources)\b")
_MCP_RX = re.compile(r"\b(mcp|konekcij\w*|connections?|connector\w*)\b.*\b(status|stanje|hub|provera|proveri|check|list)\b|"
                     r"\bmcp\s+hub\b")
_THERE_RX = re.compile(r"\b(tamo|onde|there|u\s+tom\s+mestu|that\s+town|that\s+city|destinacij\w*|destination|"
                       r"odredist\w*|na\s+cilju)\b")
_IT_RX = re.compile(r"\b(ga|ju|njega|nju|taj|tog|tu|ovaj|ovog|it|that\s+one|this\s+one|the\s+cheapest|"
                    r"najjeftinij\w*|izabran\w*|selected)\b")

_STOP_PLACE = re.compile(r"\b(kolima|autom|automobilom|autobusom|busom|vozom|peske|pesice|biciklom|"
                         r"za|sa|na|by|via|preko|u|in|tomorrow|sutra|danas|today|i|and|pa|molim|please|"
                         r"koliko|treba|ima|je|traje|km|kilometara|minuta|sati|car|bus|train|on|foot)\b.*$")


@dataclass
class Intent:
    kind: str
    lang: str = "sr"
    confidence: float = 0.0
    entities: dict = field(default_factory=dict)
    assumptions: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    steps: list = field(default_factory=list)      # extra steps for multi-part requests

    def to_dict(self) -> dict:
        return {"kind": self.kind, "lang": self.lang, "confidence": self.confidence,
                "entities": self.entities, "assumptions": self.assumptions, "missing": self.missing,
                "steps": self.steps}


def _mode(f: str) -> Optional[str]:
    for mode, rx in _MODE_WORDS:
        if re.search(r"\b(" + rx + r")", f):
            return mode
    return None


def _clean_place(raw: str) -> str:
    raw = re.sub(r"[?!.,;:]+.*$", "", raw.strip())
    raw = _STOP_PLACE.sub("", " " + raw + " ").strip() if not lookup_place(raw.strip()) else raw.strip()
    raw = re.sub(r"^(grada|mesta|sela|city\s+of|town\s+of|the)\s+", "", raw.strip())
    words = raw.split()
    # Prefer the longest leading phrase the gazetteer knows ("novog sada" in "novog sada sutra").
    for n in range(min(4, len(words)), 0, -1):
        if lookup_place(" ".join(words[:n])):
            return " ".join(words[:n])
    return " ".join(words[:4])


def _original_span(text: str, folded_span: str) -> str:
    """Map a folded phrase back to the user's spelling (keeps š, ć, Cyrillic→Latin)."""
    src = latin(text)
    words_f = folded_span.split()
    words_o = src.split()
    for i in range(len(words_o)):
        window = words_o[i:i + len(words_f)]
        if [fold(w).strip("?!.,;:") for w in window] == words_f:
            return " ".join(w.strip("?!.,;:") for w in window)
    return folded_span


_NOT_PLACES = {"centru", "centar", "centra", "blizini", "okolini", "gradu", "the", "a", "an", "dan", "noc",
               "vikend", "weekend", "sutra", "danas", "tomorrow", "today", "mapi", "mapu", "map", "osobe",
               "osoba", "dvoje", "troje", "petak", "subotu", "nedelju", "ponedeljak", "utorak", "sredu",
               "cetvrtak", "friday", "saturday", "sunday", "monday", "tuesday", "wednesday", "thursday",
               "kolima", "autom", "autobusom", "my", "me", "mi", "moj", "moje", "it", "that", "this",
               "the city", "city center", "budzet", "evra", "eur", "the center", "the map",
               # words that point back at the conversation, resolved from context instead
               "stan", "stana", "stanu", "apartman", "apartmana", "apartmanu", "smestaja", "smestaju",
               "hotel", "hotela", "hotelu", "njega", "nje", "tamo", "there", "destinacije", "destinaciji"}


def _place_after(text: str, f: str, prepositions: str) -> Optional[str]:
    """First place phrase after one of the prepositions. Every occurrence is
    tried ("stan na dan u Nišu" must skip "na dan"); a gazetteer hit wins."""
    fallback = None
    # Lookahead capture: matches may overlap, so "na dan u nisu" also yields "nisu".
    for m in re.finditer(rf"\b(?:{prepositions})\s+(?=([a-z][\w'-]*(?:\s+[a-z][\w'-]*){{0,3}}))", f):
        phrase = _clean_place(m.group(1))
        if not phrase or fold(phrase) in _NOT_PLACES or re.match(r"^\d", phrase):
            continue
        if lookup_place(phrase):
            return _original_span(text, phrase)
        if fallback is None and len(phrase) > 2:
            fallback = _original_span(text, phrase)
    return fallback


def _budget(f: str) -> Optional[dict]:
    """A price limit. "do 22. oktobra" is a date, not a budget: a number
    followed by a month or a date dot is skipped, and a bare number only
    counts when money words are around."""
    from core.workspace.dates import _MONTH_RX
    money_words = re.search(r"\b(cen\w*|price|budzet|budget|placam|platiti|kosta\w*|evr\w*|eur\w*|din\w*|rsd)\b|€", f)
    best = None
    for m in _BUDGET_RX.finditer(f):
        tail = f[m.end(1):m.end(1) + 14]
        if re.match(rf"\.?\s*({_MONTH_RX})\b", tail) or re.match(r"\.\d", tail):
            continue
        unit = (m.group(2) or "").strip()
        if not unit and not money_words:
            continue
        try:
            amount = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        cur = ("RSD" if unit.startswith(("din", "rsd")) else
               "USD" if unit.startswith(("$", "usd", "dollar")) else "EUR")
        cand = {"amount": amount, "currency": cur, "per": "total" if _TOTAL_RX.search(f) else "night"}
        if unit:
            return cand
        best = best or cand
    return best


def _guests(f: str) -> Optional[int]:
    m = _GUESTS_RX.search(f)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 16 else None
    for w, n in _GUEST_WORDS.items():
        if re.search(rf"\b{w}\b", f):
            return n
    return None


def _ptype(f: str) -> str:
    if re.search(r"\bhotel", f):
        return "hotel"
    if re.search(r"\bhostel", f):
        return "hostel"
    if re.search(r"\b(sob[aeu]|room)\b", f) and not re.search(r"apartman|stan|apartment", f):
        return "room"
    if re.search(r"\b(kuc[aeu]|vikendic|vil[aeu]|house|villa|cottage)\b", f):
        return "house"
    if re.search(r"stan\w*\s+na\s+dan|apartman|apartment|\bstan\b", f):
        return "apartment"
    return ""


def understand(text: str, context: Optional[dict] = None, ref: Optional[date] = None) -> Intent:
    context = context or {}
    f = fold(text)
    lang = "sr" if is_serbian(text) else "en"
    has_route_ctx = bool(context.get("route"))
    has_stays_ctx = bool(context.get("stays"))

    # ── explicit service requests ──────────────────────────────────────
    if _MCP_RX.search(f):
        return Intent("mcp_status", lang, 0.9)
    stay_words = _STAY_RX.search(f)
    route_words = _ROUTE_RX.search(f)
    category = detect_category(text)

    if _EMAIL_RX.search(f) and not stay_words:
        return Intent("email", lang, 0.85, {"task": text})
    if _CAL_RX.search(f) and not stay_words and not route_words:
        span = dates.parse(text, ref)
        return Intent("calendar", lang, 0.8, {"task": text, "date": span.to_dict()})
    if _DRIVE_RX.search(f) and not stay_words and not route_words:
        return Intent("drive", lang, 0.8, {"task": text})

    # ── stay follow-ups (need a previous search) ───────────────────────
    if has_stays_ctx and not route_words:
        intent = _stay_followup(text, f, lang, context, ref)
        if intent:
            return intent

    # ── route ──────────────────────────────────────────────────────────
    mode = _mode(f)
    if route_words or (mode and _FROM_TO.search(f) and not stay_words):
        intent = _route(text, f, lang, mode, context)
        if intent:
            if stay_words:
                stay = _stays(text, f, lang, context, ref, default_city=intent.entities.get("destination"))
                if stay.kind == "stays":
                    intent.steps.append(stay.to_dict())
            return intent

    # "a autobusom?", "and on foot?" — mode change on the previous route
    if has_route_ctx and mode and not stay_words and not category and len(f.split()) <= 6:
        r = context["route"]
        return Intent("route", lang, 0.9, {"origin": r.get("origin_query") or r["origin"]["name"],
                                           "destination": r.get("destination_query") or r["destination"]["name"],
                                           "mode": mode, "followup": True})

    # ── stays ──────────────────────────────────────────────────────────
    if stay_words and not category:
        return _stays(text, f, lang, context, ref)

    # ── nearby places ──────────────────────────────────────────────────
    where = (_place_after(text, f, r"blizu|near|oko|around|u|in|kod|pored|close\s+to")
             if category and not _THERE_RX.search(f) else None)
    if category and (where or _PLACES_RX.search(f) or _THERE_RX.search(f) or re.search(
            r"\b(nadji|pronadji|gde|ima\s+li|pokazi|find|show|where|list|preporuci|recommend)\b", f)):
        anchor = None
        if not where:
            anchor = _context_anchor(f, context)
        ent = {"category": category, "where": where, "anchor": anchor}
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(km|m|metara|metra|kilometr\w*)\b", f)
        if m:
            val = float(m.group(1).replace(",", "."))
            ent["radius_m"] = int(val * 1000 if m.group(2).startswith("k") else val)
        missing = [] if (where or anchor) else ["location"]
        return Intent("nearby", lang, 0.85, ent, missing=missing)

    if _SHOW_MAP_RX.search(f):
        target = _place_after(text, f, r"mapi|map|kartu|karti")
        if target:
            return Intent("show_place", lang, 0.7, {"where": target})

    if _RESEARCH_RX.search(f):
        q = re.sub(r"^(molim\s+te\s+)?(istra[zž]i|pretra[zž]i\s+(internet|web)\s*(za)?|research|"
                   r"search\s+the\s+web\s+(for)?|look\s+up)\s*", "", latin(text).strip(), flags=re.I)
        return Intent("research", lang, 0.8, {"query": q or text})

    return Intent("chat", lang, 0.3, {"text": text})


def _context_anchor(f: str, context: dict) -> Optional[dict]:
    """The place a follow-up refers to: a selected property, the route
    destination, or the last place shown."""
    stays = context.get("stays") or {}
    if re.search(r"\b(stan\w*|apartman\w*|smestaj\w*|property|apartment|hotel\w*)\b", f) and stays.get("selected"):
        sel = stays["selected"]
        if sel.get("lat") is not None:
            return {"name": sel.get("title", ""), "lat": sel["lat"], "lon": sel["lon"], "kind": "property"}
    route = context.get("route") or {}
    if route and re.search(r"destinacij|destination|odredist|cilj|tamo|there|onde", f):
        d = route["destination"]
        return {"name": d.get("name", ""), "lat": d["lat"], "lon": d["lon"], "kind": "destination"}
    focus = context.get("focus")
    if focus and focus.get("lat") is not None:
        return {"name": focus.get("name", ""), "lat": focus["lat"], "lon": focus["lon"],
                "kind": focus.get("kind", "place")}
    if route:
        d = route["destination"]
        return {"name": d.get("name", ""), "lat": d["lat"], "lon": d["lon"], "kind": "destination"}
    return None


def _route(text: str, f: str, lang: str, mode: Optional[str], context: dict) -> Optional[Intent]:
    m = _FROM_TO.search(f)
    origin = dest = None
    if m:
        origin = _original_span(text, _clean_place(m.group("a")))
        dest = _original_span(text, _clean_place(m.group("b")))
    else:
        t = _TO_ONLY.search(re.sub(r"[?!.]+$", "", f))
        if t:
            dest = _original_span(text, _clean_place(t.group("b")))
    if dest and _THERE_RX.search(fold(dest)):
        dest = None
    followup = False
    if not dest:
        if not context.get("route"):
            return None
        prev = context["route"]
        dest = prev.get("destination_query") or prev["destination"]["name"]
        origin = origin or prev.get("origin_query") or prev["origin"]["name"]
        mode = mode or prev.get("mode")
        followup = True
    ent = {"origin": origin, "destination": dest, "mode": mode or "driving"}
    if followup:
        ent["followup"] = True
    assumptions = []
    if not mode:
        assumptions.append("mode_driving")
    missing = []
    if not origin:
        missing.append("origin")
    return Intent("route", lang, 0.9 if origin else 0.75, ent, assumptions, missing)


def _stays(text: str, f: str, lang: str, context: dict, ref: Optional[date],
           default_city: Optional[str] = None) -> Intent:
    city = None
    if not _THERE_RX.search(f):
        city = _place_after(text, f, r"u|in|na|at|kod|near|blizu|oko|around|za")
        # "za 16. oktobar" is a date, not a place
        if city and re.match(r"^\d", city):
            city = None
        if city and fold(city) in ("dan", "noc", "vikend", "weekend", "sutra", "danas", "tomorrow", "today"):
            city = None
        if city and not lookup_place(city) and re.search(r"\d", city):
            city = None
    assumptions = []
    if not city:
        route = context.get("route") or {}
        stays = context.get("stays") or {}
        if default_city:
            city = default_city
        elif route and (_THERE_RX.search(f) or not stays):
            city = route.get("destination_query") or route["destination"]["name"]
            assumptions.append("city_from_route")
        elif stays:
            city = stays["query"]["city"]
            assumptions.append("city_from_previous_search")
        elif context.get("focus"):
            city = context["focus"].get("name")
            assumptions.append("city_from_context")
    span = dates.parse(text, ref)
    ref = ref or dates.today()
    assumptions.extend(span.assumptions)
    if not span.found:
        prev = (context.get("stays") or {}).get("query")
        if prev and prev.get("check_in") and date.fromisoformat(prev["check_in"]) >= ref:
            span.check_in = date.fromisoformat(prev["check_in"])
            span.nights = span.nights or prev.get("nights") or 1
            assumptions.append("dates_from_previous_search")
        else:
            span.check_in = ref
            assumptions.append("date_tonight")
    if not span.check_out:
        span.nights = span.nights or 1
        span.check_out = span.check_in + timedelta(days=span.nights)
        if "dates_from_previous_search" not in assumptions:
            assumptions.append("one_night")
    guests = _guests(f)
    if guests is None:
        assumptions.append("default_guests")
    budget = _budget(f)
    sort = "distance" if _CLOSEST_RX.search(f) else "rating" if _RATING_RX.search(f) and not _CHEAP_RX.search(f) else "price"
    landmark = None
    lm = re.search(r"(?:od|from|do|to)\s+(?:the\s+)?(autobusk\w*\s+stanic\w*|zeleznick\w*\s+stanic\w*|aerodrom\w*|"
                   r"bus\s+station|train\s+station|airport|centr\w*|city\s+cent\w*|downtown)", f)
    if lm:
        landmark = lm.group(1)
    ent = {"city": city, "check_in": span.check_in.isoformat(), "check_out": span.check_out.isoformat(),
           "nights": (span.check_out - span.check_in).days, "guests": guests, "budget": budget,
           "property_type": _ptype(f), "sort": sort, "landmark": landmark,
           "cheapest": bool(_CHEAP_RX.search(f))}
    return Intent("stays", lang, 0.9 if city else 0.6, ent, assumptions, [] if city else ["city"])


def _stay_followup(text: str, f: str, lang: str, context: dict, ref: Optional[date]) -> Optional[Intent]:
    stays = context["stays"]
    stay_words = _STAY_RX.search(f)
    budget = _budget(f)
    span = dates.parse(text, ref)
    guests = _guests(f)
    city_change = _place_after(text, f, r"u|in") if stay_words else None
    if city_change and fold(city_change) != fold(stays["query"]["city"]) and lookup_place(city_change):
        return None                                     # a new search in another town
    amen = [k for k, rx in (("parking", r"parking|parkir"), ("wifi", r"wi-?fi|internet"),
                            ("kitchen", r"kuhinj|kitchen"), ("ac", r"klim|air\s*con"),
                            ("pets", r"ljubim|pets?\b")) if re.search(rx, f)]
    if span.found or guests or re.search(r"\b(\d+)\s*(noci|nocenja|nights?)\b", f):
        ent = {}
        if span.found:
            ent["check_in"] = span.check_in.isoformat()
            if span.check_out:
                ent["check_out"] = span.check_out.isoformat()
        if span.nights:
            ent["nights"] = span.nights
        if guests:
            ent["guests"] = guests
        if ent and (len(f.split()) <= 10 or not stay_words or re.search(r"\b(promeni|izmeni|change|instead|umesto)\b", f)):
            return Intent("stays_update", lang, 0.85, ent)
    if budget and len(f.split()) <= 12:
        return Intent("stays_filter", lang, 0.9, {"budget": budget, "amenities": amen})
    if (amen and not _PLACES_RX.search(f)
            and re.search(r"\b(samo|only|sa|with|ima\s+li|koji\s+ima\w*|that\s+have)\b", f) and len(f.split()) <= 10):
        return Intent("stays_filter", lang, 0.85, {"amenities": amen})
    which = "selected"
    if _CHEAP_RX.search(f):
        which = "cheapest"
    if _CLOSEST_RX.search(f) and not _SHOW_MAP_RX.search(f):
        lm = re.search(r"(autobusk\w*\s+stanic\w*|zeleznick\w*\s+stanic\w*|aerodrom\w*|bus\s+station|"
                       r"train\s+station|airport)", f)
        return Intent("stays_sort", lang, 0.9, {"sort": "landmark" if lm else "distance",
                                                "landmark": lm.group(1) if lm else None})
    if _CLOSEST_RX.search(f):
        which = "closest"
    if _SHOW_MAP_RX.search(f) and (_IT_RX.search(f) or stay_words or which != "selected" or len(f.split()) <= 6):
        return Intent("stays_focus", lang, 0.9, {"which": which})
    if _LINK_RX.search(f) and (_IT_RX.search(f) or len(f.split()) <= 8 or which != "selected"):
        return Intent("stays_link", lang, 0.9, {"which": which})
    if re.search(r"\b(jeftinij\w*|cheaper)\b", f) and len(f.split()) <= 8:
        return Intent("stays_sort", lang, 0.85, {"sort": "price", "priced_only": True})
    if _RATING_RX.search(f) and len(f.split()) <= 8:
        return Intent("stays_sort", lang, 0.8, {"sort": "rating"})
    return None
