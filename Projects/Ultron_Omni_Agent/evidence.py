"""Deterministic provenance gates. Model confidence is never trusted."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
import re


def normalize(value):
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def enrich_evidence(evidence, limit=5):
    """Fetch public source pages with bounded size/time. Failures keep snippets."""
    import concurrent.futures
    import ipaddress
    import socket
    import requests
    from bs4 import BeautifulSoup

    def fetch(record):
        record = dict(record)
        try:
            url = record["url"]
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.port not in (None, 443):
                return record
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                return record
            with requests.Session() as session:
                session.trust_env = False
                with session.get(url, timeout=(3, 6), stream=True, allow_redirects=False) as response:
                    if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", ""):
                        return record
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        data.extend(chunk)
                        if len(data) > 1_000_000:
                            return record
            soup = BeautifulSoup(data, "html.parser")
            for node in soup(["script", "style", "nav", "footer"]):
                node.decompose()
            text = soup.get_text(" ", strip=True)[:16000]
            if len(text) >= 100 and not any(s in text.casefold() for s in ("verify you are human", "checking your browser", "enable javascript and cookies")):
                record.update(page_text=text, observed_at=datetime.now(timezone.utc).isoformat())
        except (requests.RequestException, OSError, ValueError):
            pass
        return record
    result = dict(evidence)
    rows = evidence.get("results", [])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        result["results"] = list(pool.map(fetch, rows[:limit])) + rows[limit:]
    return result


def validate_claims(payload, evidence):
    records = {r.get("url"): r for r in evidence.get("results", []) if r.get("url")}
    claims = []
    for raw in payload.get("claims", []) if isinstance(payload, dict) else []:
        if not isinstance(raw, dict) or not raw.get("field") or raw.get("value") is None:
            continue
        sources = []
        for source in raw.get("sources", []):
            if not isinstance(source, dict):
                continue
            record = records.get(source.get("url"))
            quote = normalize(source.get("quote", ""))
            value = normalize(raw["value"])
            body = normalize(record.get("page_text") or record.get("body", "")) if record else ""
            fresh = False
            if record and record.get("observed_at"):
                try:
                    observed = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
                    if observed.tzinfo is None:
                        observed = observed.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - observed).total_seconds()
                    fresh = 0 <= age <= 7 * 86400
                except (ValueError, TypeError):
                    pass
            if record and fresh and len(quote) >= 8 and quote in body and value in quote:
                sources.append({"url": source["url"], "quote": source["quote"],
                                "observed_at": record.get("observed_at"),
                                "kind": "page" if record.get("page_text") else "search_snippet"})
        domains = {urlsplit(s["url"]).hostname for s in sources if s["kind"] == "page"}
        score = .8 if len(domains) >= 2 else .65 if domains else .35 if sources else 0.0
        # Conflicting values for the same field never pass automatically.
        claims.append({"field": raw["field"], "value": raw["value"], "sources": sources,
                       "confidence": score, "status": "verified" if score >= .8 else "probable" if score else "rejected",
                       "last_verified_at": datetime.now(timezone.utc).isoformat()})
    for claim in claims:
        if any(c["field"] == claim["field"] and normalize(c["value"]) != normalize(claim["value"])
               and c["sources"] for c in claims) and claim["sources"]:
            claim.update(status="conflicting", confidence=0.0)
    relevant = [c for c in claims if c["field"] in {"restaurant_name", "name", "address", "phone"}]
    # At least name and a direct contact fact are required for automatic handoff.
    name_ok = any(c["field"] in {"name", "restaurant_name"} and c["status"] == "verified" for c in relevant)
    contact_ok = any(c["field"] in {"address", "phone"} and c["status"] == "verified" for c in relevant)
    score = min((c["confidence"] for c in relevant), default=0) if name_ok and contact_ok else 0.0
    return {"claims": claims, "overall_confidence": score, "needs_review": not (name_ok and contact_ok)}
