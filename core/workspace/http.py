"""Polite outbound HTTP for the workspace providers.

Every public service used here publishes a usage policy, and the common rules
are enforced in one place instead of in each adapter:

  * an identifying User-Agent (Nominatim and the OSM tile/routing servers
    require one),
  * a minimum interval between requests per host (Nominatim: 1 request/s),
  * a response cache, so repeating a question does not repeat the request,
  * bounded timeouts and response sizes,
  * errors that say what failed without leaking keys (core.security.redact).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import requests

from core.security import redact

USER_AGENT = "ULTRON-Workspace/2.0 (personal desktop assistant; low-volume, user-initiated)"
MAX_BYTES = 4_000_000

# Seconds between two requests to the same host. Hosts not listed get 0.15 s.
MIN_INTERVAL = {
    "nominatim.openstreetmap.org": 1.1,
    "overpass-api.de": 1.0,
    "overpass.kumi.systems": 1.0,
    "router.project-osrm.org": 0.3,
    "routing.openstreetmap.de": 0.3,
    "staninadan.com": 0.6,
}

_host_locks: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_registry_lock = threading.Lock()


class ProviderError(RuntimeError):
    """A provider failed. The message is safe to show to the user."""

    def __init__(self, message: str, *, status: str = "error", provider: str = ""):
        super().__init__(message)
        self.status = status          # error | rate_limited | unauthorized | timeout | offline
        self.provider = provider


class _Cache:
    """Small in-memory LRU with per-entry expiry, optionally mirrored to disk."""

    def __init__(self, size: int = 512, path: Optional[Path] = None):
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()
        self._path = path
        self._dirty = False
        if path and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                now = time.time()
                for k, (exp, val) in raw.items():
                    if exp > now:
                        self._data[k] = (exp, val)
            except Exception:
                self._data.clear()

    def get(self, key: str):
        with self._lock:
            hit = self._data.get(key)
            if not hit:
                return None
            if hit[0] < time.time():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return hit[1]

    def put(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._size:
                self._data.popitem(last=False)
            self._dirty = True
        if self._path:
            self.flush()

    def flush(self) -> None:
        if not self._path:
            return
        with self._lock:
            if not self._dirty:
                return
            snapshot = dict(self._data)
            self._dirty = False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            pass

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._dirty = True


def _cache_dir() -> Path:
    from core.workspace import storage
    return storage.data_dir() / "cache"


_memory_cache = _Cache(size=768)
_disk_caches: dict[str, _Cache] = {}


def disk_cache(name: str) -> _Cache:
    with _registry_lock:
        if name not in _disk_caches:
            _disk_caches[name] = _Cache(size=4000, path=_cache_dir() / f"{name}.json")
        return _disk_caches[name]


def _throttle(host: str) -> None:
    with _registry_lock:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        wait = MIN_INTERVAL.get(host, 0.15) - (time.monotonic() - _host_last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.monotonic()


def _key(method: str, url: str, params, data, json_body) -> str:
    blob = json.dumps([method, url, params, data, json_body], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def request(method: str, url: str, *, params=None, data=None, json_body=None,
            headers: Optional[dict] = None, timeout: float = 15.0, ttl: float = 0,
            provider: str = "", expect: str = "json", cache: Optional[_Cache] = None,
            cancel=None):
    """One HTTPS request with throttling, caching and safe errors.

    `expect` is "json" or "text". `ttl` > 0 caches the decoded response.
    `cancel` is an optional threading.Event checked before the request."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ProviderError("Only HTTPS provider endpoints are allowed.", provider=provider)
    key = _key(method, url, params, data, json_body)
    store = cache or _memory_cache
    if ttl:
        hit = store.get(key)
        if hit is not None:
            return hit
    if cancel is not None and cancel.is_set():
        raise ProviderError("Cancelled.", status="cancelled", provider=provider)
    _throttle(parts.hostname)
    hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "sr-Latn,sr;q=0.9,en;q=0.8"}
    hdrs.update(headers or {})
    try:
        resp = requests.request(method, url, params=params, data=data, json=json_body,
                                headers=hdrs, timeout=(5, timeout), stream=True)
        chunks, size = [], 0
        for chunk in resp.iter_content(65536):
            size += len(chunk)
            if size > MAX_BYTES:
                resp.close()
                raise ProviderError(f"{provider or parts.hostname} returned an oversized response.",
                                    provider=provider)
            chunks.append(chunk)
        body = b"".join(chunks)
    except requests.Timeout as exc:
        raise ProviderError(f"{provider or parts.hostname} did not answer in time.",
                            status="timeout", provider=provider) from exc
    except requests.RequestException as exc:
        raise ProviderError(f"{provider or parts.hostname} is unreachable: {redact(type(exc).__name__)}.",
                            status="offline", provider=provider) from exc
    if resp.status_code in (401, 403):
        raise ProviderError(f"{provider or parts.hostname} rejected the request (authorization).",
                            status="unauthorized", provider=provider)
    if resp.status_code == 429:
        raise ProviderError(f"{provider or parts.hostname} rate limit reached; try again shortly.",
                            status="rate_limited", provider=provider)
    if resp.status_code >= 400:
        raise ProviderError(f"{provider or parts.hostname} returned HTTP {resp.status_code}.",
                            provider=provider)
    if expect == "json":
        try:
            value = json.loads(body.decode(resp.encoding or "utf-8", errors="replace"))
        except ValueError as exc:
            raise ProviderError(f"{provider or parts.hostname} returned unreadable data.",
                                provider=provider) from exc
    else:
        value = body.decode(resp.encoding or "utf-8", errors="replace")
    if ttl:
        store.put(key, value, ttl)
    return value


def get_json(url: str, **kw):
    return request("GET", url, expect="json", **kw)


def get_text(url: str, **kw):
    return request("GET", url, expect="text", **kw)


def post_json(url: str, **kw):
    return request("POST", url, expect="json", **kw)
