"""Friday-inspired news briefing, implemented directly for Ultron's Gemini tools."""
import concurrent.futures
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

FEEDS = {
    "world": [("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
              ("NYT", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml")],
    "finance": [("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
                ("NYT", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml")],
}
PLUGIN = {"name": "world_briefing", "description": "Current world/finance headlines with source URLs and publication dates, or open World Monitor. Partial feed failures are reported. No alternate AI provider.",
          "parameters": {"type": "OBJECT", "properties": {
              "action": {"type": "STRING", "enum": ["news", "open_monitor"]},
              "topic": {"type": "STRING", "enum": ["world", "finance"]}}, "required": ["action"]}}


def _feed(pair):
    import requests
    source, url = pair
    try:
        with requests.get(url, timeout=(4, 8), stream=True) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > 2_000_000:
                    raise ValueError("Feed exceeds size limit")
        root = ET.fromstring(content)
        items = []
        for entry in root.findall(".//item")[:6]:
            link = entry.findtext("link", "")
            if urlsplit(link).scheme not in {"https", "http"}:
                continue
            items.append({"title": entry.findtext("title", ""), "url": link,
                          "published_at": entry.findtext("pubDate", "unknown"), "source": source})
        return {"source": source, "items": items}
    except Exception as exc:
        return {"source": source, "items": [], "error": type(exc).__name__}


def run(parameters, **kwargs):
    topic = parameters.get("topic", "world")
    if topic not in FEEDS:
        return json.dumps({"success": False, "error": "Unknown topic"})
    if parameters.get("action") == "open_monitor":
        from computer_control.plugin import handler
        return handler({"action": "new_tab", "value": "https://finance.worldmonitor.app/" if topic == "finance" else "https://worldmonitor.app/"})
    if parameters.get("action") != "news":
        return json.dumps({"success": False, "error": "Unknown action"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_feed, FEEDS[topic]))
    seen, items = set(), []
    for result in results:
        for item in result["items"]:
            if item["url"] not in seen:
                seen.add(item["url"]); items.append(item)
    return json.dumps({"success": bool(items), "observed_at": datetime.now(timezone.utc).isoformat(),
                       "headlines": items, "failed_sources": [r["source"] for r in results if "error" in r],
                       "content_trust": "untrusted publisher headlines"})
