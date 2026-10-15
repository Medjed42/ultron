import json
import os
import hashlib
import threading
_cache_lock = threading.RLock()
import time
import requests
from bs4 import BeautifulSoup
from config import config

from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs
CACHE_FILE = str(Path(__file__).resolve().parents[1] / "cache/search_cache.json")

def get_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_cache(cache_data):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with _cache_lock:
        temporary = CACHE_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        os.replace(temporary, CACHE_FILE)

class SearchProvider:
    def search(self, query: str, max_results: int = 15) -> dict:
        raise NotImplementedError

class DuckDuckGoLiteProvider(SearchProvider):
    def search(self, query: str, max_results: int = 15) -> dict:
        try:
            url = "https://html.duckduckgo.com/html/"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            res = requests.post(url, data={"q": query}, headers=headers, timeout=10)
            res.raise_for_status()
            
            soup = BeautifulSoup(res.text, "html.parser")
            results = []
            for card in soup.select(".result")[:max_results]:
                a, snippet = card.select_one(".result__a"), card.select_one(".result__snippet")
                if not a: continue
                href = a.get("href", "")
                if href.startswith("//"): href = "https:" + href
                params = parse_qs(urlsplit(href).query)
                href = params.get("uddg", [href])[0]
                if urlsplit(href).scheme not in {"http", "https"}: continue
                results.append({"title":a.get_text(" ",strip=True),"url":href,
                                "body":snippet.get_text(" ",strip=True) if snippet else "",
                                "observed_at":datetime.now(timezone.utc).isoformat(),"kind":"search_snippet"})
            if not results:
                return {"status":"error","message":"Search returned no evidence; possibly a challenge page", "results":[]}
            return {"status": "success", "results": results}
        except Exception as e:
            return {"status": "error", "message": str(e)}

def deep_search(query: str, max_results: int = 15, use_cache: bool = True):
    """
    Performs a web search with caching and provider fallback.
    """
    cache_key = hashlib.md5(f"{query}_{max_results}".encode()).hexdigest()
    
    if use_cache:
        cache = get_cache()
        if cache_key in cache:
            entry = cache[cache_key]
            # Check TTL
            if time.time() - entry.get("timestamp", 0) < config.SEARCH_CACHE_TTL_HOURS * 3600:
                return entry["data"]
    
    # Execute search
    provider = DuckDuckGoLiteProvider()
    result = provider.search(query, max_results)
    
    if result["status"] != "success":
        try:
            from ddgs import DDGS
            rows = list(DDGS(timeout=10).text(query,max_results=max_results))
            items = [{"title":r.get("title",""),"url":r.get("href",""),"body":r.get("body",""),
                      "observed_at":datetime.now(timezone.utc).isoformat(),"kind":"search_snippet"} for r in rows if r.get("href","").startswith(("http://","https://"))]
            if items: result = {"status":"success","results":items}
        except Exception:
            pass
    if result["status"] == "success" and use_cache:
        cache = get_cache()
        cache[cache_key] = {
            "timestamp": time.time(),
            "query": query,
            "data": result
        }
        save_cache(cache)
        
    return result
