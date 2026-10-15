"""What the assistant says, in Serbian (Latin) and English.

Answers are built from the data, never from a model, for maps and stays: a
sentence can only contain a number the provider returned.
"""
from __future__ import annotations

from datetime import date


def km(meters: float, lang: str) -> str:
    if meters < 1000:
        return f"{int(round(meters))} m"
    v = meters / 1000
    s = f"{v:.1f}" if v < 100 else f"{v:.1f}"
    return (s.replace(".", ",") if lang == "sr" else s) + " km"


def duration(seconds: float, lang: str) -> str:
    mins = int(round(seconds / 60))
    if mins < 60:
        return f"{mins} min"
    h, m = divmod(mins, 60)
    if h >= 24:
        d, h = divmod(h, 24)
        return (f"{d} d {h} h" if lang == "en" else f"{d} d {h} h")
    return f"{h} h {m} min" if m else f"{h} h"


def money(amount: float, currency: str, lang: str) -> str:
    if amount is None:
        return "—"
    if currency == "RSD":
        s = f"{amount:,.0f}".replace(",", "." if lang == "sr" else ",")
        return f"{s} RSD" if lang == "en" else f"{s} din"
    whole = abs(amount - round(amount)) < 0.005
    s = f"{amount:,.0f}" if whole else f"{amount:,.2f}"
    if lang == "sr":
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    sym = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF "}.get(currency, currency + " ")
    return f"{s} {sym}".strip() if lang == "sr" and currency == "EUR" else f"{sym}{s}"


MODE_SR = {"driving": "kolima", "walking": "peške", "cycling": "biciklom", "transit": "javnim prevozom"}
MODE_EN = {"driving": "by car", "walking": "on foot", "cycling": "by bike", "transit": "by public transport"}


def day(iso: str, lang: str) -> str:
    d = date.fromisoformat(iso)
    if lang == "sr":
        months = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "avg", "sep", "okt", "nov", "dec"]
        return f"{d.day}. {months[d.month - 1]}"
    return d.strftime("%b %d").replace(" 0", " ")


ASSUMPTION = {
    "one_night": ("1 noćenje (odjava sledećeg dana)", "1 night (check-out next day)"),
    "default_guests": ("1 gost", "1 guest"),
    "date_tonight": ("dolazak večeras", "check-in tonight"),
    "city_from_route": ("mesto iz prethodne rute", "town from the previous route"),
    "city_from_previous_search": ("isto mesto kao u prethodnoj pretrazi", "same town as the previous search"),
    "dates_from_previous_search": ("isti datumi kao u prethodnoj pretrazi", "same dates as the previous search"),
    "weekend_one_night": ("vikend = subota → nedelja", "weekend = Saturday → Sunday"),
    "mode_driving": ("prevoz kolima", "travel by car"),
    "city_from_context": ("mesto iz razgovora", "town from the conversation"),
}


def assumption(key: str, lang: str) -> str:
    pair = ASSUMPTION.get(key)
    return (pair[0] if lang == "sr" else pair[1]) if pair else key


def t(lang: str, sr: str, en: str, **kw) -> str:
    return (sr if lang == "sr" else en).format(**kw)
