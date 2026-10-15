"""Dates in Serbian (Latin and Cyrillic) and English, relative to today.

    "16. oktobar", "16. oktobra", "16.10.", "16/10/2026", "October 16th",
    "od 16. do 18. oktobra", "16-18. okt", "sutra", "prekosutra",
    "za sledeći petak", "u subotu", "next Friday", "ovaj vikend",
    "za 3 noći", "2 nights"

A day and month without a year means the next occurrence: on 10 October
2026, "16. oktobar" is 16 October 2026 and "5. januar" is 5 January 2027.
The parser reports what it inferred so the interface can show assumptions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from core.workspace.text import fold

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Belgrade")
except Exception:            # tzdata missing: local time is the best we have
    _TZ = None


def today() -> date:
    return datetime.now(_TZ).date() if _TZ else date.today()


_MONTHS = {
    # Serbian, all cases a month name takes in dates
    "januar": 1, "januara": 1, "jan": 1, "februar": 2, "februara": 2, "feb": 2,
    "mart": 3, "marta": 3, "mar": 3, "april": 4, "aprila": 4, "apr": 4,
    "maj": 5, "maja": 5, "jun": 6, "juna": 6, "jul": 7, "jula": 7,
    "avgust": 8, "avgusta": 8, "avg": 8, "septembar": 9, "septembra": 9, "sep": 9, "sept": 9,
    "oktobar": 10, "oktobra": 10, "okt": 10, "novembar": 11, "novembra": 11, "nov": 11,
    "decembar": 12, "decembra": 12, "dec": 12,
    # English
    "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7, "august": 8,
    "aug": 8, "september": 9, "october": 10, "oct": 10, "november": 11, "december": 12,
}
_MONTH_RX = "|".join(sorted(_MONTHS, key=len, reverse=True))

_WEEKDAYS = {
    "ponedeljak": 0, "ponedeljka": 0, "ponedeljku": 0, "utorak": 1, "utorka": 1, "utorku": 1,
    "sreda": 2, "sredu": 2, "srede": 2, "sredi": 2, "cetvrtak": 3, "cetvrtka": 3, "cetvrtku": 3,
    "petak": 4, "petka": 4, "petku": 4, "subota": 5, "subotu": 5, "subote": 5, "suboti": 5,
    "nedelja": 6, "nedelju": 6, "nedelje": 6, "nedelji": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5,
    "sunday": 6,
}
_WD_RX = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))


@dataclass
class DateSpan:
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    nights: Optional[int] = None
    matched: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.check_in is not None

    def to_dict(self) -> dict:
        return {"check_in": self.check_in.isoformat() if self.check_in else None,
                "check_out": self.check_out.isoformat() if self.check_out else None,
                "nights": self.nights, "assumptions": list(self.assumptions)}


def _next_occurrence(day: int, month: int, ref: date, year: Optional[int] = None) -> Optional[date]:
    try:
        if year:
            return date(year, month, day)
        d = date(ref.year, month, day)
        if d < ref:
            d = date(ref.year + 1, month, day)
        return d
    except ValueError:
        return None


def _weekday(target: int, ref: date, strictly_next: bool = False, next_week: bool = False) -> date:
    delta = (target - ref.weekday()) % 7
    if strictly_next and delta == 0:
        delta = 7
    if next_week:
        delta += 7 if delta < 7 and (ref + timedelta(days=delta)).isocalendar()[1] == ref.isocalendar()[1] else 0
    return ref + timedelta(days=delta)


def parse(text: str, ref: Optional[date] = None) -> DateSpan:
    """Find a stay/travel date span in free text. Never raises."""
    ref = ref or today()
    t = " " + fold(text) + " "
    span = DateSpan()

    # nights: "3 noci", "za 2 nocenja", "na 4 dana", "2 nights"
    m = re.search(r"\b(\d{1,2})\s*(noci|nocenja|nocenje|noc|nights?|dana|days?)\b", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 30:
            span.nights = n
            span.matched.append(m.group(0).strip())

    # range with month: "od 16. do 18. oktobra", "16-18. okt", "16 - 18 october"
    m = re.search(rf"(?:od\s+)?(\d{{1,2}})\.?\s*(?:-|–|do|to|until)\s*(\d{{1,2}})\.?\s*({_MONTH_RX})\b(?:\s+(\d{{4}}))?", t)
    if m:
        month = _MONTHS[m.group(3)]
        year = int(m.group(4)) if m.group(4) else None
        a = _next_occurrence(int(m.group(1)), month, ref, year)
        b = _next_occurrence(int(m.group(2)), month, ref, year or (a.year if a else None))
        if a and b and b > a:
            span.check_in, span.check_out = a, b
            span.nights = (b - a).days
            span.matched.append(m.group(0).strip())
            return span

    # "16. oktobar", "16 oktobra 2026", "october 16th", "oct 16"
    m = (re.search(rf"\b(\d{{1,2}})\.?\s*({_MONTH_RX})\b\.?(?:\s+(\d{{4}}))?", t)
         or None)
    if m:
        d = _next_occurrence(int(m.group(1)), _MONTHS[m.group(2)], ref,
                             int(m.group(3)) if m.group(3) else None)
    else:
        m = re.search(rf"\b({_MONTH_RX})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", t)
        d = _next_occurrence(int(m.group(2)), _MONTHS[m.group(1)], ref,
                             int(m.group(3)) if m.group(3) else None) if m else None
    if not d:
        # "16.10.", "16.10.2026", "16/10", "2026-10-16"
        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
        if m:
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                d = None
        else:
            m = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\.?(?=\s|$|[,;!?])", t)
            if m and 1 <= int(m.group(2)) <= 12:
                y = m.group(3)
                year = (2000 + int(y) if y and len(y) == 2 else int(y)) if y else None
                d = _next_occurrence(int(m.group(1)), int(m.group(2)), ref, year)

    if not d:
        if re.search(r"\b(prekosutra|day after tomorrow)\b", t):
            d, m = ref + timedelta(days=2), re.search(r"prekosutra|day after tomorrow", t)
        elif re.search(r"\b(sutra|tomorrow)\b", t):
            d, m = ref + timedelta(days=1), re.search(r"sutra|tomorrow", t)
        elif re.search(r"\b(danas|veceras|tonight|today|noc[ae]s)\b", t):
            d, m = ref, re.search(r"danas|veceras|tonight|today|noc[ae]s", t)
        elif re.search(r"\b(ovaj|ovog|za|this|next|sledeci|sledeceg|iduci|iduceg)?\s*(vikend|weekend)\b", t):
            m = re.search(r"\b(ovaj|ovog|za|this|next|sledeci|sledeceg|iduci|iduceg)?\s*(vikend|weekend)\b", t)
            nxt = m.group(1) in ("next", "sledeci", "sledeceg", "iduci", "iduceg")
            sat = _weekday(5, ref, strictly_next=False)
            if nxt:
                sat += timedelta(days=7)
            d = sat
            if span.nights is None:
                span.nights = 1
                span.assumptions.append("weekend_one_night")
        else:
            m = re.search(rf"\b(sledeci|sledeceg|sledece|sledecu|iduci|iduceg|iducu|next|this|ovaj|ovog|ovu|u|za)?\s*({_WD_RX})\b", t)
            if m:
                word = m.group(1) or ""
                d = _weekday(_WEEKDAYS[m.group(2)], ref,
                             strictly_next=word in ("sledeci", "sledeceg", "sledece", "sledecu",
                                                    "iduci", "iduceg", "iducu", "next"))
    if not d:
        return span
    span.check_in = d
    span.matched.append(m.group(0).strip() if m else "")
    if span.nights:
        span.check_out = d + timedelta(days=span.nights)
    return span


def format_day(d: date, lang: str = "sr") -> str:
    if lang == "sr":
        months = ["januar", "februar", "mart", "april", "maj", "jun", "jul", "avgust",
                  "septembar", "oktobar", "novembar", "decembar"]
        days = ["pon", "uto", "sre", "čet", "pet", "sub", "ned"]
        return f"{days[d.weekday()]}, {d.day}. {months[d.month - 1]} {d.year}."
    return d.strftime("%a, %d %b %Y")
