"""
core/theme.py — ULTRON's colour themes, in one place.

    THEMES / resolve()      the named themes (RED … WHITE), RGB cycling, CUSTOM hex,
                            and "original" — what every entry point (voice tool,
                            typed command, settings panel) turns a request into.
    parse_command()         English and Serbian colour commands typed in chat
                            ("change your color to blue", "prebaci se na plavu",
                            "vrati originalnu boju", "set your theme to #00FFFF").
    CssTheme                exact, repeatable recolouring of Qt stylesheets.

Why stylesheets need more than the palette: ui.py builds most colours from the
palette class C (hue-shifted by apply_ui_accent), but many are literal hex in
the stylesheets — header gradients, chip borders, card backgrounds. The old
retheme swapped only exact palette strings, so those literals stayed cyan.
CssTheme first *normalises* a stylesheet back to the default palette (every
palette value it has ever produced is remembered, so a stylesheet built under
any earlier theme maps back exactly), then hue-shifts the whole accent family —
the cyan/navy range the default design is drawn in — to the target. Semantic
colours (green = connected, red = error, amber, purple) sit outside that range
and are never touched, so status meaning survives every theme.
"""
from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass

# ── Named themes ──────────────────────────────────────────────────────────────
THEMES: dict[str, tuple[str, str]] = {
    "red":    ("RED",    "#ff163c"),
    "blue":   ("BLUE",   "#0088ff"),
    "purple": ("PURPLE", "#a020f0"),
    "green":  ("GREEN",  "#00ff88"),
    "cyan":   ("CYAN",   "#00ffff"),
    "gold":   ("GOLD",   "#ffd700"),
    "pink":   ("PINK",   "#ff1493"),
    "orange": ("ORANGE", "#ff6500"),
    "white":  ("WHITE",  "#f5f7ff"),
}
ORIGINAL = "red"          # ULTRON's own colour: what "return to your original colour" means
RGB = "rgb"
CUSTOM = "custom"
RGB_PERIOD = 12.0         # seconds per full hue cycle in RGB mode

# Word stems → theme. English words plus Serbian (Latin, with and without
# diacritics) in every case ending the commands actually use.
_COLOR_WORDS: list[tuple[str, str]] = [
    ("red",    r"red|crimson|scarlet|crven(?:a|u|o|e|i|om|oj)?"),
    ("blue",   r"blue|plav(?:a|u|o|e|i|om|oj)?"),
    ("purple", r"purple|violet|magenta|ljubi[cč]ast(?:a|u|o|e|i|om|oj)?|lila"),
    ("green",  r"green|zelen(?:a|u|o|e|i|om|oj)?"),
    ("cyan",   r"cyan|aqua|teal|turquoise|cijan|tirkizn(?:a|u|o|e|i|om|oj)?"),
    ("gold",   r"gold|golden|yellow|zlatn(?:a|u|o|e|i|om|oj)?|[zž]ut(?:a|u|o|e|i|om|oj)?"),
    ("pink",   r"pink|rose|roze|ru[zž]i[cč]ast(?:a|u|o|e|i|om|oj)?"),
    ("orange", r"orange|narand[zž]ast(?:a|u|o|e|i|om|oj)?|naran[cč]ast(?:a|u|o|e|i|om|oj)?|oran[zž]"),
    ("white",  r"white|bel(?:a|u|o|e|i|om|oj)|bijel(?:a|u|o|e|i|om|oj)?"),
    ("rgb",    r"rgb|rainbow|dug(?:a|u|ina|inu)|[sš]aren(?:a|u|o|e|i)?"),
]
_COLOR_RX = [(name, re.compile(rf"(?<![\w#])(?:{rx})(?!\w)", re.I)) for name, rx in _COLOR_WORDS]
_HEX_RX = re.compile(r"#([0-9a-f]{6})\b", re.I)
_ORIGINAL_RX = re.compile(
    r"\b(original\w*|default|factory|izvorn\w*|podrazumevan\w*|po[cč]etn\w*)\b", re.I)
_RETURN_RX = re.compile(
    r"\b(return|revert|reset|restore|back|vrati\w*|resetuj\w*)\b", re.I)
_INTENT_RX = re.compile(
    r"\b(change|switch|set|turn|make|activate|enable|go|use|apply|paint|return|revert|"
    r"reset|restore|promeni\w*|promijeni\w*|prebaci\w*|aktiviraj\w*|uklju[cč]i\w*|"
    r"stavi\w*|postavi\w*|vrati\w*|oboji\w*|podesi\w*|koristi\w*|pre[dđ]i\w*|predji\w*)\b",
    re.I)
_SUBJECT_RX = re.compile(
    r"\b(colou?rs?|theme|interface|ui|everything|mode|protocol|skin|accent|"
    r"boj\w*|tem[aeiu]\w*|interfejs\w*|sve|re[zž]im\w*|protokol\w*)\b", re.I)
# Commands that name a colour but are about something else (smart home lights).
_NOT_UI_RX = re.compile(
    r"\b(light|lights|lamp|bulb|led|tv|television|svetl\w*|svjetl\w*|sijalic\w*|lamp\w*|"
    r"televizor\w*|klim\w*)\b", re.I)


@dataclass(frozen=True)
class ThemeSpec:
    name: str          # "blue", "rgb", "custom" …
    hex: str           # accent (for "rgb": the colour the cycle starts from)
    label: str         # what the cinematics call it: "BLUE", "RGB", "CUSTOM #12AB34"

    @property
    def is_rgb(self) -> bool:
        return self.name == RGB


def _clean_hex(h: str) -> str | None:
    h = (h or "").strip().lower()
    if not h.startswith("#"):
        h = "#" + h
    return h if re.fullmatch(r"#[0-9a-f]{6}", h) else None


def resolve(value: str) -> ThemeSpec | None:
    """'blue', 'Plavu', '#00FFFF', 'rgb', 'original' … → ThemeSpec, or None."""
    v = (value or "").strip()
    if not v:
        return None
    low = v.lower()
    if low in THEMES:
        label, hx = THEMES[low]
        return ThemeSpec(low, hx, label)
    if low in (RGB, "rgb mode", "rainbow"):
        return ThemeSpec(RGB, THEMES["red"][1], "RGB")
    hx = _clean_hex(v) if re.fullmatch(r"#?[0-9a-fA-F]{6}", v) else None
    if hx:
        for name, (label, h) in THEMES.items():
            if h == hx:
                return ThemeSpec(name, h, label)
        return ThemeSpec(CUSTOM, hx, f"CUSTOM {hx.upper()}")
    if _ORIGINAL_RX.search(low) or low in ("original", "default"):
        label, hx = THEMES[ORIGINAL]
        return ThemeSpec(ORIGINAL, hx, label)
    m = _HEX_RX.search(v)
    if m:
        return resolve("#" + m.group(1))
    for name, rx in _COLOR_RX:
        if rx.search(v):
            return resolve(name)
    return None


def parse_command(text: str) -> ThemeSpec | None:
    """A typed colour command → ThemeSpec; anything else → None.

    Needs an intent ("change", "switch", "prebaci", "vrati"…) and a colour, and
    refuses anything about lights or devices, so "turn on the red light" stays
    a smart-home command."""
    t = (text or "").strip()
    if not t or len(t) > 160 or _NOT_UI_RX.search(t):
        return None
    if not _INTENT_RX.search(t):
        return None
    if _ORIGINAL_RX.search(t) and (_RETURN_RX.search(t) or _SUBJECT_RX.search(t)):
        return resolve("original")
    m = _HEX_RX.search(t)
    if m and _SUBJECT_RX.search(t):
        return resolve("#" + m.group(1))
    for name, rx in _COLOR_RX:
        if rx.search(t):
            return resolve(name)
    return None


def protocol_lines(spec: ThemeSpec) -> list[str]:
    head = "RGB" if spec.is_rgb else spec.label
    return [f"> {head} PROTOCOL INITIALIZED", "> SYSTEMS BOOTING UP...",
            "> RECONFIGURING INTERFACE...", "> ENERGY CORE ONLINE"]


def spoken_name(spec: ThemeSpec) -> str:
    return "RGB" if spec.is_rgb else ("custom" if spec.name == CUSTOM else spec.name)


# ── Colour maths ──────────────────────────────────────────────────────────────

def hex_to_hsv(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return colorsys.rgb_to_hsv(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


def hsv_to_hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return "#{:02x}{:02x}{:02x}".format(int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5))


def vivid(h: str) -> str:
    """The accent the effects draw with: any custom colour, however dark,
    is lifted to a brightness that can glow on black (hue kept)."""
    hh, s, v = hex_to_hsv(h)
    return hsv_to_hex(hh, s, max(v, 0.78))


def rgb_hex_at(t: float, period: float = RGB_PERIOD) -> str:
    """The RGB-mode accent at time t (seconds): a full saturated hue wheel."""
    return hsv_to_hex((t / max(0.5, period)) % 1.0, 0.92, 1.0)


def is_greyish(h: str) -> bool:
    return hex_to_hsv(h)[1] < 0.08


# ── Stylesheet recolouring ────────────────────────────────────────────────────
_CSS_HEX = re.compile(r"#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?(?![0-9a-fA-F])")
_CSS_RGBA = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(\s*,\s*[\d.]+\s*)?\)")


class CssTheme:
    """Normalise-then-shift stylesheet recolouring (see the module docstring)."""

    FAMILY = (172.0, 238.0)      # degrees: the default design's cyan → navy range
    MIN_SAT = 0.10

    def __init__(self, defaults: dict[str, str]):
        self.defaults = {k: v.lower() for k, v in defaults.items()}
        self.base_hue = hex_to_hsv(self.defaults["PRI"])[0]
        self._history: dict[str, str] = {}       # any palette value ever used → key
        self.remember(self.defaults)
        self._shift_cache: dict[tuple[str, str], str] = {}

    def remember(self, palette: dict[str, str]) -> None:
        if len(self._history) > 6000:
            self._history = {}
            for k, v in self.defaults.items():
                self._history[v] = k
        for k, v in palette.items():
            self._history.setdefault(v.lower(), k)

    def normalize(self, css: str) -> str:
        """Map every palette value from any past theme back to its default."""
        def sub(m: re.Match) -> str:
            hx = "#" + m.group(1).lower()
            key = self._history.get(hx)
            if key is None:
                return m.group(0)
            return self.defaults[key] + (m.group(2) or "")
        return _CSS_HEX.sub(sub, css)

    def _shift_rgb(self, r: int, g: int, b: int, target: str) -> tuple[int, int, int] | None:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        deg = h * 360.0
        if s < self.MIN_SAT or not (self.FAMILY[0] <= deg <= self.FAMILY[1]):
            return None
        th, ts, _tv = hex_to_hsv(target)
        h2 = (h + (th - self.base_hue)) % 1.0
        s2 = s * 0.15 if ts < 0.08 else s
        r2, g2, b2 = colorsys.hsv_to_rgb(h2, s2, v)
        return int(r2 * 255 + 0.5), int(g2 * 255 + 0.5), int(b2 * 255 + 0.5)

    def shift(self, css: str, target: str) -> str:
        """Recolour a (normalised) stylesheet for accent `target`."""
        key = (css, target)
        hit = self._shift_cache.get(key)
        if hit is not None:
            return hit

        def sub_hex(m: re.Match) -> str:
            hx = m.group(1)
            out = self._shift_rgb(int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16), target)
            if out is None:
                return m.group(0)
            return "#{:02x}{:02x}{:02x}".format(*out) + (m.group(2) or "")

        def sub_rgba(m: re.Match) -> str:
            r, g, b = (min(255, int(x)) for x in m.group(1, 2, 3))
            out = self._shift_rgb(r, g, b, target)
            if out is None:
                return m.group(0)
            alpha = m.group(4) or ""
            return f"rgba({out[0]},{out[1]},{out[2]}{alpha})" if alpha else f"rgb({out[0]},{out[1]},{out[2]})"

        out = _CSS_RGBA.sub(sub_rgba, _CSS_HEX.sub(sub_hex, css))
        if len(self._shift_cache) > 4000:
            self._shift_cache.clear()
        self._shift_cache[key] = out
        return out

    def shift_color(self, hex_color: str, target: str) -> str:
        """One colour (e.g. an icon tint) through the same mapping."""
        hx = _clean_hex(hex_color) or hex_color
        key = self._history.get(hx.lower())
        if key is not None:
            hx = self.defaults[key]
        return self.shift(hx, target)

    def restyle(self, widget, target: str) -> bool:
        """Recolour one widget's stylesheet. Returns True if it changed."""
        css = widget.styleSheet()
        if not css or widget.property("_ultron_no_theme"):   # e.g. colour swatches
            return False
        base = widget.property("_ultron_css_base")
        if base is None or css != widget.property("_ultron_css_out"):
            base = self.normalize(css)          # new widget, or the app restyled it
            widget.setProperty("_ultron_css_base", base)
        out = self.shift(base, target)
        widget.setProperty("_ultron_css_out", out)
        if out != css:
            widget.setStyleSheet(out)
            return True
        return False
