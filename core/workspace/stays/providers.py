"""The remaining StayFinder sources.

    osm            OpenStreetMap (Overpass) — hotels, guest houses, hostels and
                   apartments mapped near the destination. ODbL data, no prices.
    google_places  Google Places text search — needs the Maps server key. No prices.
    amadeus        Amadeus production Hotel Search — needs client id + secret.
                   Real dated offers for the requested dates and guests.
    booking/airbnb Search links only. Neither offers a public API to individual
                   developers and both forbid automated collection, so ULTRON
                   opens their search for the same city, dates and guests
                   instead of reading their pages.
"""
from __future__ import annotations

from urllib.parse import quote, urlencode

from core.workspace import http
from core.workspace.geo import _overpass, maps_place_link, now_iso
from core.workspace.http import ProviderError
from core.workspace.stays.base import HEALTH, Listing, Provider, StayQuery

_OSM_TYPE = {"hotel": "hotel", "motel": "hotel", "guest_house": "guest_house", "hostel": "hostel",
             "apartment": "apartment", "chalet": "house"}


class OSMDiscovery(Provider):
    key = "osm"
    name = "OpenStreetMap"
    kind = "discovery"
    homepage = "https://www.openstreetmap.org/"

    def search(self, q: StayQuery, cancel=None) -> list[Listing]:
        rows = _overpass(f'nwr["tourism"~"^(hotel|motel|guest_house|hostel|apartment|chalet)$"]'
                         f'(around:6000,{q.lat},{q.lon});', 120, cancel)
        out = []
        for r in rows:
            if not r["name"]:
                continue
            tags = r["tags"]
            stars = tags.get("stars", "")
            url = r["website"] if str(r["website"]).startswith("https://") else ""
            out.append(Listing(
                id=r["id"], provider=self.key, provider_name=self.name, title=r["name"],
                url=url or maps_place_link(r["name"], r["lat"], r["lon"]),
                property_type=_OSM_TYPE.get(tags.get("tourism", ""), "other"), city=q.city,
                address=r["address"], lat=r["lat"], lon=r["lon"],
                stars=int(stars[0]) if stars[:1].isdigit() else None,
                availability="unknown", verified_at=now_iso(),
                attribution="© OpenStreetMap contributors (ODbL)",
                description="Mapped in OpenStreetMap. No price or availability is published there."))
        HEALTH.ok(self.key, len(out))
        return out


class GooglePlacesDiscovery(Provider):
    key = "google_places"
    name = "Google Places"
    kind = "discovery"
    homepage = "https://developers.google.com/maps/documentation/places/web-service"
    needs = ("google_maps_api_key",)

    def search(self, q: StayQuery, cancel=None) -> list[Listing]:
        from core.integrations import store
        key = store.api_key("google_maps_api_key")
        kind = {"hotel": "hotel", "hostel": "hostel", "guest_house": "guest house"}.get(q.property_type,
                                                                                      "apartment accommodation")
        data = http.post_json(
            "https://places.googleapis.com/v1/places:searchText",
            json_body={"textQuery": f"{kind} {q.city}", "languageCode": "sr-Latn", "pageSize": 20,
                       "locationBias": {"circle": {"center": {"latitude": q.lat, "longitude": q.lon},
                                                   "radius": 8000.0}}},
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask":
                     "places.id,places.displayName,places.formattedAddress,places.location,"
                     "places.googleMapsUri,places.websiteUri,places.rating,places.userRatingCount,places.types"},
            ttl=6 * 3600, provider=self.name, cancel=cancel)
        out = []
        for p in data.get("places", []):
            loc = p.get("location") or {}
            if "latitude" not in loc:
                continue
            types = p.get("types") or []
            out.append(Listing(
                id="g:" + p.get("id", ""), provider=self.key, provider_name=self.name,
                title=(p.get("displayName") or {}).get("text", ""),
                url=p.get("websiteUri") if str(p.get("websiteUri", "")).startswith("https://") else p.get("googleMapsUri", ""),
                property_type=("hotel" if "hotel" in types else
                               "guest_house" if "guest_house" in types else
                               "hostel" if "hostel" in types else "apartment"),
                city=q.city, address=p.get("formattedAddress", ""), lat=loc["latitude"], lon=loc["longitude"],
                rating=p.get("rating"), rating_scale=5.0 if p.get("rating") is not None else None,
                review_count=p.get("userRatingCount"), availability="unknown", verified_at=now_iso(),
                attribution="Google Places"))
        HEALTH.ok(self.key, len(out))
        return out


class AmadeusOffers(Provider):
    key = "amadeus"
    name = "Amadeus Hotel Search"
    kind = "listings"
    homepage = "https://developers.amadeus.com/"
    needs = ("amadeus_client_id", "amadeus_client_secret")
    verifies_prices = True
    BASE = "https://api.amadeus.com"   # production only: sandbox data is not real availability

    def search(self, q: StayQuery, cancel=None) -> list[Listing]:
        from core.integrations import store
        auth = http.request("POST", self.BASE + "/v1/security/oauth2/token", provider=self.name,
                            data={"grant_type": "client_credentials",
                                  "client_id": store.api_key("amadeus_client_id"),
                                  "client_secret": store.api_key("amadeus_client_secret")}, cancel=cancel)
        headers = {"Authorization": "Bearer " + auth["access_token"]}
        hotels = http.get_json(self.BASE + "/v1/reference-data/locations/hotels/by-geocode", headers=headers,
                               params={"latitude": q.lat, "longitude": q.lon, "radius": 10, "radiusUnit": "KM",
                                       "hotelSource": "ALL"}, provider=self.name, cancel=cancel).get("data", [])[:20]
        if not hotels:
            HEALTH.ok(self.key, 0)
            return []
        offers = http.get_json(self.BASE + "/v3/shopping/hotel-offers", headers=headers, provider=self.name,
                               params={"hotelIds": ",".join(h["hotelId"] for h in hotels), "adults": q.guests,
                                       "checkInDate": q.check_in, "checkOutDate": q.check_out,
                                       "roomQuantity": 1, "currency": q.currency, "bestRateOnly": "true"},
                               cancel=cancel, timeout=30).get("data", [])
        out = []
        for row in offers:
            hotel = row.get("hotel", {})
            offer = (row.get("offers") or [None])[0]
            if not offer:
                continue
            price = offer.get("price", {})
            try:
                total = float(price["total"])
            except (KeyError, TypeError, ValueError):
                continue
            policy = (offer.get("policies") or {}).get("cancellations") or []
            name = hotel.get("name", "Hotel")
            out.append(Listing(
                id=f"amadeus:{hotel.get('hotelId')}", provider=self.key, provider_name=self.name, title=name,
                url="https://www.google.com/maps/search/?" + urlencode({"api": 1, "query": f"{name} {q.city}"}),
                property_type="hotel", city=q.city, lat=hotel.get("latitude"), lon=hotel.get("longitude"),
                price_currency=price.get("currency", q.currency), price_basis="quote", stay_total_quote=total,
                price_night=round(total / max(1, q.nights), 2), availability="available",
                cancellation=("Free cancellation until " + policy[0]["deadline"][:10]) if policy and policy[0].get("deadline") else "",
                description=((offer.get("room") or {}).get("description") or {}).get("text", "")[:300],
                verified_at=now_iso(), attribution="Amadeus production offer — recheck before booking"))
        HEALTH.ok(self.key, len(out))
        return out


class _LinkOut(Provider):
    kind = "link"

    def search(self, q, cancel=None):
        return []

    def link(self, q: StayQuery) -> str:
        raise NotImplementedError


class BookingLink(_LinkOut):
    key = "booking"
    name = "Booking.com"
    homepage = "https://www.booking.com/"

    def link(self, q: StayQuery) -> str:
        return "https://www.booking.com/searchresults.html?" + urlencode(
            {"ss": f"{q.city}", "checkin": q.check_in, "checkout": q.check_out, "group_adults": q.guests,
             "no_rooms": 1, "group_children": 0, "order": "price", "selected_currency": q.currency})


class AirbnbLink(_LinkOut):
    key = "airbnb"
    name = "Airbnb"
    homepage = "https://www.airbnb.com/"

    def link(self, q: StayQuery) -> str:
        return (f"https://www.airbnb.com/s/{quote(q.city)}/homes?"
                + urlencode({"checkin": q.check_in, "checkout": q.check_out, "adults": q.guests}))


def registry() -> list[Provider]:
    from core.workspace.stays.staninadan import StanNaDan
    return [StanNaDan(), AmadeusOffers(), GooglePlacesDiscovery(), OSMDiscovery(), BookingLink(), AirbnbLink()]


def fail(provider: Provider, exc: Exception) -> dict:
    status = getattr(exc, "status", "error")
    HEALTH.fail(provider.key, str(exc), status)
    return {"provider": provider.key, "name": provider.name, "status": status, "error": str(exc)[:240]}


__all__ = ["registry", "fail", "ProviderError"]
