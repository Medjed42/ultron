"""
core/browser_policy.py — a browser opens only because the user asked for one.

THE PROBLEM
    Several tools reach for Chrome on their own: the weather report opened a
    Google search, a video that would not play opened YouTube, and when an
    integration was missing the model would improvise with browser_control or
    computer_control `navigate`. Each looked reasonable in isolation; together
    they meant "do X" often ended in a browser window nobody requested.

THE POLICY
    A tool call that would launch a browser or navigate one is allowed when
      1. the user's own recent words ask for a browser, website, tab, URL or
         YouTube (typed text or the live voice transcript), or
      2. the user approves it on the HUD confirmation banner (browser_policy
         "ask"; with "explicit_only" there is no banner, only an explanation).
    Otherwise the call is not executed and the model is told why, so it can
    explain the real blocker instead of trying another browser tool.

    Observing or controlling a browser window that is ALREADY open (reading
    tabs, pausing a video, scrolling) is not a launch and is not gated.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional

# How long the user's words count as intent for a tool call that follows.
INTENT_WINDOW_SECONDS = 90.0

_BROWSER_NAMES = r"(chrome|hrom\w*|krom\w*|edge|firefox|fajerfoks|opera|brave|vivaldi|safari)"
_EXPLICIT = re.compile(
    r"(" + _BROWSER_NAMES + r"|\bbrowser\w*|\bbrauzer\w*|pretra(z|ž)iva(c|č)\w*|pregleda(c|č)\w*"
    r"|\bweb\s*(site|page|stranic\w*)|\bwebsite\b|\bwebpage\b|\bsajt\w*|\bstranic\w*"
    r"|\btab\b|\btabs\b|\bkartic\w*|\burl\b|\blink\w*|https?://|www\."
    r"|\byoutube\b|\bjutjub\w*|\byt\b"
    r"|\b[\w-]+\.(com|org|net|io|rs|de|uk|eu|tv|me|app|dev|ai|co)\b"
    r"|google\s+flights|on\s+google\s+maps|u\s+google\s+maps)",
    re.IGNORECASE,
)

_BROWSER_APP = re.compile(r"^\s*(google\s+)?" + _BROWSER_NAMES + r"\b|browser|pretra", re.IGNORECASE)
_URLISH = re.compile(r"https?://|www\.|\b[\w-]+\.(com|org|net|io|rs|de|uk|eu|tv|me|app|dev|ai|co)\b", re.I)

# computer_control actions that navigate or open pages (not mere observation)
_CC_NAVIGATING = {"navigate", "search", "new_tab", "reopen_tab", "youtube_search",
                  "youtube_select_video"}
_CC_LAUNCHING = {"open_app", "focus_app"}

_lock = threading.Lock()
_utterances: list[tuple[float, str]] = []
_partial: list[str] = []


def note_user_text(text: str) -> None:
    """Record a complete user turn (typed command or finished transcript)."""
    text = (text or "").strip()
    if not text:
        return
    with _lock:
        _utterances.append((time.monotonic(), text))
        del _utterances[:-12]
        _partial.clear()


def note_user_fragment(text: str) -> None:
    """Record a live-transcription fragment of the turn still in progress.
    Tool calls can arrive before the turn completes, so fragments count."""
    text = (text or "").strip()
    if text:
        with _lock:
            _partial.append(text)
            del _partial[:-40]


def recent_user_text(window: float = INTENT_WINDOW_SECONDS) -> str:
    now = time.monotonic()
    with _lock:
        parts = [t for at, t in _utterances if now - at <= window]
        parts.extend(_partial)
    return " ".join(parts)


def has_explicit_browser_intent(text: Optional[str] = None) -> bool:
    return bool(_EXPLICIT.search(text if text is not None else recent_user_text()))


def browser_use(tool: str, args: dict) -> Optional[str]:
    """If this call would launch or navigate a browser, a short description of
    what it would do; otherwise None."""
    args = args or {}
    action = str(args.get("action", "")).lower().strip()
    if tool == "browser_control":
        return f"browser_control {action or 'action'}" + (f" → {args.get('url')}" if args.get("url") else "")
    if tool == "youtube_video":
        return "open YouTube in the browser"
    if tool == "flight_finder":
        return "search Google Flights in the browser"
    if tool == "weather_report" and action in ("open", "show_in_browser", "browser"):
        return "open a weather page in the browser"
    if tool == "open_app":
        name = str(args.get("app_name", ""))
        if _BROWSER_APP.search(name) or _URLISH.search(name):
            return f"open {name}"
    if tool == "computer_control":
        target = str(args.get("target", ""))
        if action in _CC_NAVIGATING:
            return f"computer_control {action}" + (f" → {target}" if target else "")
        if action in _CC_LAUNCHING and (_BROWSER_APP.search(target) or _URLISH.search(target)):
            return f"computer_control {action} {target}"
    if tool == "send_message":
        platform = str(args.get("platform", "")).lower()
        if platform in ("instagram", "messenger", "facebook"):
            return f"open {platform} in the browser"
    if tool == "open_holo_hands" and bool(args.get("in_browser")):
        return "open the Holo Hands deck in the browser"
    return None


def blocked_message(what: str, policy: str) -> str:
    base = (
        f"[BROWSER_BLOCKED] Not executed: this would {what}, and the user did not ask "
        f"for a browser. ULTRON never opens a browser as a fallback. Do NOT retry with "
        f"another browser tool (browser_control, computer_control navigate/search/open_app, "
        f"open_app with a browser, youtube_video). Tell the user briefly what you can do "
        f"without a browser, or which connection is missing (they can say 'check AI "
        f"connections')."
    )
    if policy == "ask":
        base += (" A confirmation banner is on the HUD: if the user approves it there, "
                 "the browser action will run. Mention that in one short sentence.")
    return base


def gate(tool: str, args: dict, run: Callable[[], str], policy: str = "ask",
         confirm: Optional[Callable[..., str]] = None,
         user_text: Optional[str] = None) -> tuple[bool, str]:
    """Decide a tool call. Returns (allowed_now, message_if_not).

    When not allowed and the policy is "ask", the real call is parked behind the
    HUD confirmation (core.confirm), which runs it only on the user's CONFIRM."""
    what = browser_use(tool, args)
    if what is None:
        return True, ""
    if has_explicit_browser_intent(user_text):
        return True, ""
    if policy == "ask" and confirm is not None:
        try:
            confirm("browser", "Open a browser?",
                    f"ULTRON wants to {what}.\n\nYou did not ask for a browser in your "
                    f"request. Confirm only if you want this to happen in the browser.",
                    run)
        except Exception as exc:
            return False, blocked_message(what, "explicit_only") + f" (confirmation unavailable: {exc})"
        return False, blocked_message(what, "ask")
    return False, blocked_message(what, "explicit_only")


def reset_for_tests() -> None:
    with _lock:
        _utterances.clear()
        _partial.clear()
