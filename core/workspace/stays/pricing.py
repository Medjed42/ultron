"""Comparable stay prices.

"Cheapest" means the lowest total for the whole stay in one currency:

    total = nightly price × nights  +  mandatory fees the source states

A provider quote for the stay (Amadeus) is used as-is. Currency conversion
uses the National Bank of Serbia middle rate for the day (via
kurs.resenje.org); if no rate can be fetched, prices in another currency are
left unconverted and marked non-comparable instead of guessed.
"""
from __future__ import annotations

from typing import Optional

from core.workspace import http
from core.workspace.http import ProviderError
from core.workspace.stays.base import Listing

FX_URL = "https://kurs.resenje.org/api/v1/currencies/{code}/rates/today"
CURRENCIES = ("EUR", "RSD", "USD", "GBP", "CHF")


def rsd_per(code: str, cancel=None) -> float:
    """RSD for one unit of `code` (NBS middle rate)."""
    code = code.upper()
    if code == "RSD":
        return 1.0
    if code not in CURRENCIES:
        raise ProviderError(f"Unsupported currency {code}.")
    data = http.get_json(FX_URL.format(code=code.lower()), ttl=6 * 3600, provider="NBS rates (kurs.resenje.org)",
                         cache=http.disk_cache("fx"), cancel=cancel)
    rate = float(data.get("exchange_middle") or 0) / float(data.get("parity") or 1)
    if not rate > 0:
        raise ProviderError("The exchange-rate service returned no middle rate.")
    return rate


def convert(amount: float, src: str, dst: str, cancel=None) -> float:
    if src == dst:
        return amount
    return amount * rsd_per(src, cancel) / rsd_per(dst, cancel)


def fx_note(src: str, dst: str) -> Optional[str]:
    if src == dst:
        return None
    try:
        return f"1 {src} = {rsd_per(src) / rsd_per(dst):.4f} {dst} (NBS srednji kurs)"
    except ProviderError:
        return None


def price_listing(item: Listing, nights: int, currency: str, cancel=None) -> None:
    """Fill total / night_price / price_status on one listing, honestly."""
    item.total, item.night_price, item.total_currency = None, None, currency
    if item.price_basis == "quote" and item.stay_total_quote is not None:
        base, status = item.stay_total_quote, "verified"
        src = item.price_currency or currency
        fees = 0.0
    elif item.price_night is not None and item.price_basis == "from":
        base, status = item.price_night * nights, "indicative"
        src = item.price_currency or currency
        fees = sum(f.get("amount", 0) for f in item.fees
                   if f.get("mandatory") and f.get("currency", src) == src)
    else:
        item.price_status = "unavailable"
        return
    try:
        item.total = round(convert(base + fees, src, currency, cancel), 2)
        item.night_price = round(item.total / max(1, nights), 2)
        item.price_status = status
    except ProviderError:
        # Keep the provider's own currency rather than inventing a rate.
        item.total = round(base + fees, 2)
        item.total_currency = src
        item.night_price = round(item.total / max(1, nights), 2)
        item.price_status = status
        if "fx_unavailable" not in item.flags:
            item.flags.append("fx_unavailable")
