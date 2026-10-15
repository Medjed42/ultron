"""iCloud CalDAV on Windows, or an authorized read-only iCloud ICS subscription."""
import hashlib
import time as clock
from copy import deepcopy
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo
import requests
from . import common, store


def period(p):
    zone = ZoneInfo(p.get("timezone", "Europe/Belgrade"))
    def parse(value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(zone)
        candidate = parsed.replace(tzinfo=zone)
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != parsed:
            raise ValueError("This local time does not exist because of a daylight-saving change.")
        if candidate.utcoffset() != candidate.replace(fold=1).utcoffset():
            raise ValueError("This local time occurs twice. Supply an explicit UTC offset.")
        return candidate
    start = parse(p.get("start") or datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0).isoformat())
    end = parse(p.get("end") or (start + timedelta(days=7)).isoformat())
    if end <= start or end-start > timedelta(days=366):
        raise ValueError("Calendar range must be positive and at most 366 days.")
    return start, end, zone


def event_rows(data, start, end, zone):
    import icalendar
    import recurring_ical_events
    calendar = icalendar.Calendar.from_ical(data)
    output = []
    for event in recurring_ical_events.of(calendar).between(start, end):
        begins = event.decoded("DTSTART")
        finishes = event.decoded("DTEND", None)
        if finishes is None:
            finishes = begins + event.decoded("DURATION", timedelta(days=1) if not isinstance(begins, datetime) else timedelta())
        all_day = not isinstance(begins, datetime)
        def stamp(value):
            if not isinstance(value, datetime): return value.isoformat()
            if value.tzinfo is None: value = value.replace(tzinfo=zone)
            return value.astimezone(zone).isoformat()
        output.append({"uid": str(event.get("UID", "")), "title": str(event.get("SUMMARY", "(Untitled)")),
                       "start": stamp(begins), "end": stamp(finishes), "all_day": all_day,
                       "location": str(event.get("LOCATION", "")),
                       "recurrence_id": str(event.get("RECURRENCE-ID", ""))})
    return sorted(output, key=lambda row: row["start"])


@contextmanager
def connection():
    import caldav
    account = store.get_secret("icloud")
    if not account or not account.get("username") or not account.get("password"):
        raise PermissionError("Connect iCloud Calendar in settings with an app-specific password, or add a read-only iCloud subscription.")
    client = caldav.DAVClient(url="https://caldav.icloud.com", username=account["username"],
                             password=account["password"], timeout=20, require_tls=True)
    try:
        yield client
    finally:
        client.close()


def calendar_id(cal):
    return hashlib.sha256(str(cal.url).encode()).hexdigest()[:20]


def can_write(cal):
    from caldav.elements.base import BaseElement
    class CurrentUserPrivilegeSet(BaseElement):
        tag = "{DAV:}current-user-privilege-set"
    try:
        data = cal.get_properties([CurrentUserPrivilegeSet()], parse_props=False)
        from lxml import etree
        raw = data.get("{DAV:}current-user-privilege-set", "")
        if not raw: return False
        root = raw if hasattr(raw, "iter") else etree.fromstring(raw.encode() if isinstance(raw, str) else raw,
                                etree.XMLParser(resolve_entities=False, no_network=True))
        return any(node.tag in {"{DAV:}write", "{DAV:}all", "{DAV:}write-content"} for node in root.iter())
    except Exception:
        return False


def subscription_data(url):
    url = url.replace("webcal://", "https://", 1)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".icloud.com") or parsed.username or parsed.port not in (None, 443):
        raise ValueError("Use an HTTPS/webcal iCloud calendar subscription URL.")
    with requests.get(url, timeout=(5, 20), allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise RuntimeError("The iCloud subscription could not be read.")
        body = bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > 5_000_000: raise ValueError("Subscription exceeds the 5 MB limit.")
    return bytes(body)


def read(p):
    start, end, zone = period(p)
    subscription = store.get_secret("icloud_subscription")
    if subscription:
        return {"success": True, "source": "iCloud ICS subscription", "read_only": True,
                "events": event_rows(subscription_data(subscription), start, end, zone),
                "calendars": [{"id": "subscription", "name": "iCloud subscription", "read_only": True}],
                "timezone": str(zone), "fetched_at": common.now()}
    rows, calendars, warnings = [], [], []
    deadline = clock.monotonic() + 40
    with connection() as client:
        for cal in client.principal().calendars()[:20]:
            if clock.monotonic() >= deadline:
                warnings.append("Partial scan: remaining calendars were skipped after the response-time limit.")
                break
            cid = calendar_id(cal)
            writable = can_write(cal)
            calendars.append({"id": cid, "name": str(cal.name or "Calendar"), "read_only": not writable})
            if p.get("calendar_id") and p["calendar_id"] != cid:
                continue
            try:
                events = cal.search(start=start, end=end, event=True, expand=True)
                for item in events[:500]:
                    for row in event_rows(item.data, start, end, zone):
                        row.update(calendar_id=cid, calendar=str(cal.name), read_only=not writable)
                        rows.append(row)
            except Exception:
                warnings.append(f"Could not read calendar {cal.name}; other calendars remain available.")
    return {"success": True, "source": "iCloud CalDAV", "calendars": calendars,
            "events": sorted(rows, key=lambda r: r["start"]), "timezone": str(zone),
            "warnings": warnings, "fetched_at": common.now()}


def _choose(client, cid):
    matches = [c for c in client.principal().calendars() if calendar_id(c) == cid]
    if len(matches) != 1:
        raise ValueError("Select an observed calendar ID.")
    if not can_write(matches[0]):
        raise PermissionError("This calendar is read-only or its write permission could not be verified.")
    return matches[0]


def mutate(p):
    p = deepcopy(p)  # Freeze exactly what the review approves.
    if store.get_secret("icloud_subscription"):
        raise PermissionError("The subscribed calendar is read-only.")
    action = p["action"]
    cid = common.text(p.get("calendar_id"), "Calendar ID", 100)
    if action != "delete":
        common.text(p.get("start"), "Explicit start date and time")
        common.text(p.get("end"), "Explicit end date and time")
    start, end, zone = period(p)
    recurrence = p.get("recurrence")
    count = p.get("count", 10)
    minutes = p.get("reminder_minutes")
    if recurrence and recurrence not in {"DAILY", "WEEKLY", "MONTHLY"}:
        raise ValueError("Unsupported recurrence frequency.")
    if recurrence and (type(count) is not int or not 1 <= count <= 100):
        raise ValueError("Recurrence count must be 1–100.")
    if minutes is not None and (type(minutes) is not int or not 0 <= minutes <= 10080):
        raise ValueError("Reminder must be within one week.")
    if action != "delete":
        title = common.text(p.get("title"), "Event title", 300)
    fingerprint, original, conflicts = None, None, []
    with connection() as client:
        cal = _choose(client, cid)
        if action in {"update", "delete"}:
            event = cal.event_by_uid(common.text(p.get("uid"), "Event UID"))
            event.load()
            original = event.data
            fingerprint = hashlib.sha256(original.encode()).hexdigest()
            import icalendar
            entries = icalendar.Calendar.from_ical(original).walk("VEVENT")
            if any(x.get("RRULE") or x.get("RECURRENCE-ID") for x in entries) and p.get("scope") != "series":
                raise ValueError("Recurring changes require scope=series and an explicit whole-series review. Individual recurrence edits are not supported.")
            previous = str(entries[0].get("SUMMARY", "(Untitled)"))
            previous_start = str(entries[0].decoded("DTSTART"))
            previous_end = str(entries[0].decoded("DTEND", "not supplied"))
        if action != "delete":
            for event in cal.search(start=start, end=end, event=True, expand=True):
                conflicts.extend(r for r in event_rows(event.data, start, end, zone) if r["uid"] != p.get("uid"))
    detail = (f"{action.upper()} — {title if action != 'delete' else previous}\n"
              f"{previous_start if action == 'delete' else start.isoformat()} → {previous_end if action == 'delete' else end.isoformat()}\nCalendar: {cid}\n"
              f"Scope: {'ENTIRE SERIES' if p.get('scope') == 'series' else 'single event'}\n"
              f"Conflicts in the displayed interval: {', '.join(r['title'] for r in conflicts) or 'none'}")
    if action == "create":
        detail += f"\nLocation: {p.get('location') or 'not set'}\nRepeat: {recurrence or 'none'}"
        if recurrence:
            detail += f" × {count}. Later recurrence conflicts have NOT been checked."
        detail += f"\nReminder: {str(minutes) + ' minutes before' if minutes is not None else 'none'}"
    else:
        detail += f"\nEvent UID: {p['uid']}"
    if action == "update":
        detail += f"\nPrevious: {previous}\nPrevious time: {previous_start} → {previous_end}\nExisting recurrence, exceptions, location and alarms are preserved."
    from core import confirm
    def perform():
        import icalendar
        with connection() as client:
            cal = _choose(client, cid)
            if action in {"update", "delete"}:
                event = cal.event_by_uid(p["uid"])
                event.load()
                if hashlib.sha256(event.data.encode()).hexdigest() != fingerprint:
                    raise RuntimeError("The event changed after preview; request a fresh review.")
                if action == "delete":
                    event.delete()
                else:
                    document = icalendar.Calendar.from_ical(event.data)
                    # Series edits change the master, preserving exceptions.
                    component = next(e for e in document.walk("VEVENT") if not e.get("RECURRENCE-ID"))
                    component.pop("DURATION", None)
                    for key, value in (("SUMMARY", title), ("DTSTART", start), ("DTEND", end)):
                        component.pop(key, None); component.add(key, value)
                    event.data = document.to_ical().decode()
                    event.save(no_create=True, all_recurrences=p.get("scope") == "series")
            else:
                document = icalendar.Calendar()
                document.add("PRODID", "-//Ultron//Calendar//EN"); document.add("VERSION", "2.0")
                component = icalendar.Event()
                component.add("UID", str(uuid4())); component.add("SUMMARY", title)
                component.add("DTSTART", start); component.add("DTEND", end); component.add("DTSTAMP", datetime.now(timezone.utc))
                if p.get("location"): component.add("LOCATION", common.text(p["location"], "Location"))
                if p.get("recurrence") in {"DAILY", "WEEKLY", "MONTHLY"}:
                    count = p.get("count", 10)
                    if not 1 <= count <= 100: raise ValueError("Recurrence count must be 1–100.")
                    component.add("RRULE", {"FREQ": p["recurrence"], "COUNT": count})
                if p.get("reminder_minutes") is not None:
                    minutes = p["reminder_minutes"]
                    if not 0 <= minutes <= 10080: raise ValueError("Reminder must be within one week.")
                    alarm = icalendar.Alarm(); alarm.add("ACTION", "DISPLAY"); alarm.add("DESCRIPTION", title)
                    alarm.add("TRIGGER", timedelta(minutes=-minutes)); component.add_component(alarm)
                document.add_component(component)
                cal.save_event(document.to_ical())
        common.publish("calendar", read({"start": start.isoformat(), "end": (end+timedelta(days=7)).isoformat(), "timezone": str(zone)}))
        return "Calendar change completed."
    return confirm.request("calendar_change", "Review calendar change", detail, perform)


def handle(p):
    return mutate(p) if p.get("action") in {"create", "update", "delete"} else read(p)
