"""
actions/weather_report.py — the weather, as data.

This tool used to open a Google search for "weather in <city>" in the browser
on every call, so asking ULTRON for the weather always launched Chrome and the
assistant itself never knew the answer. It now reads the forecast from
Open-Meteo (free, no API key, no account) and returns it to the conversation.

A browser page is opened only for action="open" — i.e. when the user asks to
see the forecast in the browser — and that call passes the browser policy.
"""
import webbrowser
from urllib.parse import quote_plus

import requests

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes (Open-Meteo documentation).
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "light freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def _forecast(city: str) -> dict:
    g = requests.get(_GEO_URL, params={"name": city, "count": 1, "language": "en"},
                     timeout=10)
    g.raise_for_status()
    results = (g.json() or {}).get("results") or []
    if not results:
        raise LookupError(f"no place called '{city}' was found")
    place = results[0]
    f = requests.get(_FORECAST_URL, params={
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max",
        "timezone": "auto", "forecast_days": 3,
    }, timeout=10)
    f.raise_for_status()
    return {"place": place, "data": f.json()}


def _describe(city: str, when: str, fc: dict) -> str:
    place, data = fc["place"], fc["data"]
    name = ", ".join(x for x in (place.get("name"), place.get("country")) if x)
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    lines = [f"Weather for {name} (source: Open-Meteo, observed {cur.get('time', '')}):"]
    if cur:
        lines.append(
            f"Now: {cur.get('temperature_2m')}°C (feels {cur.get('apparent_temperature')}°C), "
            f"{_WMO.get(cur.get('weather_code'), 'unknown conditions')}, "
            f"humidity {cur.get('relative_humidity_2m')}%, wind {cur.get('wind_speed_10m')} km/h.")
    for i, day in enumerate(daily.get("time") or []):
        label = ("Today", "Tomorrow")[i] if i < 2 else day
        lines.append(
            f"{label}: {_WMO.get(daily['weather_code'][i], 'unknown')}, "
            f"{daily['temperature_2m_min'][i]}–{daily['temperature_2m_max'][i]}°C, "
            f"rain chance {daily['precipitation_probability_max'][i]}%.")
    if when and when.lower() not in ("today", "now", ""):
        lines.append(f"(Requested: {when}.)")
    return "\n".join(lines)


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city = parameters.get("city")
    when = (parameters.get("time") or "today").strip()
    action = str(parameters.get("action") or "report").lower().strip()

    if not city or not isinstance(city, str) or not city.strip():
        msg = "The city is missing for the weather report."
        _log(msg, player)
        return msg
    city = city.strip()

    if action in ("open", "show_in_browser", "browser"):
        url = f"https://www.google.com/search?q={quote_plus(f'weather in {city} {when}')}"
        try:
            if not webbrowser.open(url):
                raise RuntimeError("webbrowser.open returned False")
        except Exception as e:
            return f"Could not open the browser for the weather page: {e}"
        return f"Opened the weather page for {city} in the browser."

    try:
        report = _describe(city, when, _forecast(city))
    except Exception as e:
        msg = (f"Could not get the weather for {city}: {e}. "
               f"Nothing was opened in the browser.")
        _log(msg, player)
        return msg

    _log(report.splitlines()[1] if len(report.splitlines()) > 1 else report, player)
    if session_memory:
        try:
            session_memory.set_last_search(query=f"weather in {city} {when}", response=report)
        except Exception:
            pass
    return report


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"SYS: {message}")
        except Exception:
            pass


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "weather_report",
    "description": (
        "Gets the current weather and a 3-day forecast for a city and returns it as data "
        "(no browser). Use action='open' ONLY if the user explicitly asks to see the "
        "weather page in the browser."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {
                "type": "STRING",
                "description": "City name"
            },
            "time": {
                "type": "STRING",
                "description": "today | tomorrow | a day name (optional)"
            },
            "action": {
                "type": "STRING",
                "description": "report (default) | open — open is only for an explicit browser request"
            },
        },
        "required": [
            "city"
        ]
    },
    "handler": weather_action,
}
