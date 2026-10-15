"""ULTRON Workspace MCP server — maps, places, stays and exchange rates.

A real Model Context Protocol server (mcp SDK, MCPServer). The workspace
reaches it over stdio like any other MCP server, and it can be added to other
MCP clients too, e.g. Claude Code:

    claude mcp add ultron-workspace -- python -m core.workspace.mcp_server

(run from the ULTRON folder). Every tool is read-only: it looks things up and
never books, pays, sends or changes anything. Results carry their provider
and timestamp; nothing is estimated.

stdout belongs to the protocol — diagnostics go to stderr only.
"""
from __future__ import annotations

import sys
from datetime import date
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from core.workspace import geo
from core.workspace.http import ProviderError

server = MCPServer(
    name="ultron-workspace",
    title="ULTRON Workspace",
    version="2.0.0",
    instructions=("Read-only travel and place tools for ULTRON. Distances and durations come from a routing "
                  "provider; prices are labelled verified or indicative; nothing is booked."),
)

_RO = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)


def _error(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "status": getattr(exc, "status", "error"), "error": str(exc)[:400]}


async def _run(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        result = await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))
        return {"ok": True, **result} if isinstance(result, dict) else {"ok": True, "value": result}
    except (ProviderError, ValueError) as exc:
        return _error(exc)


@server.tool(title="Geocode a place", annotations=_RO)
async def geocode(place: str, country: str = "") -> dict[str, Any]:
    """Resolve a place name (any Serbian case form, Latin or Cyrillic, or English) to coordinates.
    `country` is an optional ISO code hint such as "rs"."""
    return await _run(geo.geocode, place, prefer_country=(country or "rs").lower()[:2])


@server.tool(title="Route between two places", annotations=_RO)
async def route(origin: str, destination: str, mode: str = "driving") -> dict[str, Any]:
    """Real route distance, travel time and geometry. mode: driving | walking | cycling | transit.
    transit requires Google Routes; without it the result says so instead of estimating."""
    if mode not in geo.MODES:
        return {"ok": False, "status": "error", "error": "mode must be driving, walking, cycling or transit"}

    def work():
        a = geo.geocode(origin)
        b = geo.geocode(destination, near=None)
        return geo.route(a, b, mode)
    return await _run(work)


@server.tool(title="Places near a point", annotations=_RO)
async def nearby_places(category: str, place: str = "", lat: float | None = None, lon: float | None = None,
                        radius_m: int = 1500) -> dict[str, Any]:
    """Named places of one category near a place or coordinates, nearest first, with straight-line
    distances. category: restaurant, cafe, bar, fast_food, parking, fuel, pharmacy, hospital, atm,
    supermarket, attraction, charging."""
    def work():
        if lat is not None and lon is not None:
            center = {"lat": float(lat), "lon": float(lon), "name": place or "", "source": "coordinates"}
        else:
            center = geo.geocode(place)
        return geo.nearby(center, category, radius_m)
    return await _run(work)


@server.tool(title="Search accommodation", annotations=_RO)
async def stay_search(city: str, check_in: str, check_out: str, guests: int = 1, currency: str = "EUR",
                      property_type: str = "", landmark: str = "") -> dict[str, Any]:
    """Apartments, rooms and hotels for a stay, from every configured source at once (staninadan.com,
    OpenStreetMap, and Google Places / Amadeus when their keys are set). Ranked by comparable total
    price; each listing is marked verified / indicative / unavailable. Optional landmark (e.g.
    "autobuska stanica") adds straight-line distances to it. Never books."""
    def work():
        from core.workspace.stays.aggregate import search
        from core.workspace.stays.base import StayQuery
        a, b = date.fromisoformat(check_in), date.fromisoformat(check_out)
        nights = (b - a).days
        if not 1 <= nights <= 30:
            raise ValueError("Stay must be 1–30 nights.")
        from core.workspace.dates import today
        if a < today():
            raise ValueError("Check-in is in the past.")
        if not 1 <= int(guests) <= 16:
            raise ValueError("Guests must be 1–16.")
        cur = currency.upper()
        if cur not in ("EUR", "RSD", "USD", "GBP", "CHF"):
            raise ValueError("Unsupported currency.")
        center = geo.geocode(city)
        lm = geo.resolve_landmark(landmark, center) if landmark else None
        q = StayQuery(city=center["name"], lat=center["lat"], lon=center["lon"], check_in=a.isoformat(),
                      check_out=b.isoformat(), nights=nights, guests=int(guests), currency=cur,
                      property_type=property_type, country=center.get("country") or "rs")
        out = search(q, landmark=lm)
        out["center"] = center
        out["landmark"] = lm
        return out
    return await _run(work)


@server.tool(title="Exchange rate", annotations=_RO)
async def exchange_rate(from_currency: str, to_currency: str = "RSD") -> dict[str, Any]:
    """National Bank of Serbia middle rate for today, for EUR, RSD, USD, GBP and CHF."""
    def work():
        from core.workspace.stays.pricing import convert
        rate = convert(1.0, from_currency.upper(), to_currency.upper())
        return {"from": from_currency.upper(), "to": to_currency.upper(), "rate": rate,
                "source": "NBS middle rate via kurs.resenje.org", "fetched_at": geo.now_iso()}
    return await _run(work)


@server.tool(title="Accommodation sources", annotations=_RO)
async def stay_providers() -> dict[str, Any]:
    """Every accommodation source, whether it is configured, and its last real outcome."""
    from core.workspace.stays.providers import registry
    return {"ok": True, "providers": [p.describe() for p in registry()]}


def main() -> None:
    # Make the project importable no matter where the process was started.
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    server.run("stdio")


if __name__ == "__main__":
    main()
