"""StayFinder data model and provider registry.

A provider turns (destination, dates, guests) into Listings. Each listing
says exactly what is known about its price:

    verified    a dated quote for these dates and guests (e.g. Amadeus offer)
    indicative  an advertised "price from" — not a quote, not a confirmed availability
    unavailable the source publishes no price

and about availability ("available", "unknown", "unavailable"). Nothing is
upgraded on the way to the screen: an indicative price stays indicative.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class StayQuery:
    city: str                      # display name, e.g. "Niš"
    lat: float
    lon: float
    check_in: str                  # ISO date
    check_out: str                 # ISO date
    nights: int
    guests: int = 1
    currency: str = "EUR"
    property_type: str = ""        # "" = any; apartment | room | hotel | guest_house | hostel | house
    country: str = "rs"


@dataclass
class Listing:
    id: str
    provider: str
    provider_name: str
    title: str
    url: str
    image: str = ""
    property_type: str = "other"
    city: str = ""
    address: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    price_night: Optional[float] = None       # in price_currency
    price_night_max: Optional[float] = None
    price_currency: str = ""
    price_basis: str = ""                     # from | quote | ""
    stay_total_quote: Optional[float] = None  # provider's own total for the stay (quotes only)
    fees: list = field(default_factory=list)  # [{label, amount, currency, mandatory, per}]
    availability: str = "unknown"             # available | unknown | unavailable
    rating: Optional[float] = None
    rating_scale: Optional[float] = None
    review_count: Optional[int] = None
    stars: Optional[int] = None               # category stars as advertised
    capacity: Optional[int] = None
    beds: Optional[int] = None
    amenities: list = field(default_factory=list)
    cancellation: str = ""
    description: str = ""
    verified_at: str = ""
    attribution: str = ""
    also_on: list = field(default_factory=list)
    # filled by the aggregator
    total: Optional[float] = None
    total_currency: str = ""
    price_status: str = "unavailable"
    night_price: Optional[float] = None       # converted, per night
    distance_center_m: Optional[float] = None
    distance_landmark_m: Optional[float] = None
    flags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Provider:
    key = "base"
    name = "Base"
    kind = "listings"          # listings | discovery | link
    homepage = ""
    needs = ()                 # credential names (DPAPI store / env) this provider requires
    verifies_prices = False

    def configured(self) -> bool:
        return all(_has_secret(n) for n in self.needs)

    def search(self, q: StayQuery, cancel=None) -> list[Listing]:
        raise NotImplementedError

    def describe(self) -> dict:
        h = HEALTH.get(self.key)
        return {"key": self.key, "name": self.name, "kind": self.kind, "homepage": self.homepage,
                "configured": self.configured(), "verifies_prices": self.verifies_prices,
                "needs": list(self.needs), **h}


def _has_secret(name: str) -> bool:
    try:
        from core.integrations import store
        store.api_key(name)
        return True
    except Exception:
        return False


class _Health:
    """Last real outcome per provider — the only source of its status."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}

    def ok(self, key: str, count: int) -> None:
        with self._lock:
            d = self._data.setdefault(key, {})
            d.update(last_ok=time.time(), last_count=count, last_error="", last_status="ok")

    def fail(self, key: str, error: str, status: str = "error") -> None:
        with self._lock:
            d = self._data.setdefault(key, {})
            d.update(last_fail=time.time(), last_error=error[:240], last_status=status)

    def get(self, key: str) -> dict:
        with self._lock:
            return dict(self._data.get(key, {"last_status": "unused"}))


HEALTH = _Health()

PROPERTY_TYPES = ("apartment", "room", "house", "hotel", "guest_house", "hostel", "other")

AMENITY_LABELS = {
    "wifi": ("Wi-Fi", "Wi-Fi"), "parking": ("Parking", "Parking"), "kitchen": ("Kuhinja", "Kitchen"),
    "ac": ("Klima", "Air conditioning"), "tv": ("TV", "TV"), "washer": ("Veš mašina", "Washer"),
    "balcony": ("Terasa", "Balcony"), "elevator": ("Lift", "Elevator"), "pets": ("Ljubimci", "Pets allowed"),
    "bathroom": ("Kupatilo", "Private bathroom"), "heating": ("Grejanje", "Heating"),
    "breakfast": ("Doručak", "Breakfast"), "pool": ("Bazen", "Pool"), "jacuzzi": ("Đakuzi", "Jacuzzi"),
}
