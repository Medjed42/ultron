"""
core/ui_command_center.py — the widgets of ULTRON's command-center interface.

    NeuralGraph       interactive 3-D node graph of what ULTRON really has:
                      providers, MCP servers and their tools, native tools,
                      plugins, agents, memory, monitors, files.
    AICore            the animated ULTRON core; its state comes from the voice
                      loop, the router and the connection manager.
    ConnectionRow     one provider / MCP line with a verified status.
    StageStrip        the stages a command actually went through.
    ToggleSwitch, NodeInspector, icon painting helpers.

Nothing in the graph is invented. Every node is built from a registry, the
connection snapshot, the memory file or the monitor list (build_graph), and a
category with no source simply does not appear. The faint dust behind the graph
is decoration and is never connected or labelled.

All painting is QPainter — no WebEngine, no OpenGL, no extra dependency — and
the animation timers stop while a widget is hidden.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal, QSize
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QRadialGradient, QFont,
                         QPainterPath, QFontMetricsF, QIcon, QPixmap, QPolygonF,
                         QConicalGradient, QLinearGradient)
from PyQt6.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame,
                             QPushButton, QSizePolicy, QLineEdit, QComboBox,
                             QToolButton, QMenu)

from core.cinematic.core_fx import CoreFx

FONT_UI = "Segoe UI"
FONT_HEAD = "Bahnschrift"
FONT_MONO = "Consolas"

# ── Categories (fixed hues so meaning never changes with the accent colour) ──
CATS: dict[str, tuple[str, str]] = {
    "core":       ("ULTRON",       "#22e1ff"),
    "provider":   ("Providers",    "#f5b942"),
    "mcp":        ("MCP Servers",  "#2ee6a6"),
    "mcp_tool":   ("MCP Tools",    "#27c3a5"),
    "tool":       ("Tools",        "#3b82f6"),
    "agent":      ("Agents",       "#c26bff"),
    "plugin":     ("Plugins",      "#8b7bff"),
    "memory":     ("Memory",       "#4ade80"),
    "automation": ("Automation",   "#facc15"),
    "files":      ("Files",        "#60a5fa"),
}

STATUS_COLORS = {
    "CONNECTED": "#22c55e", "READY": "#f5b942", "CHECKING": "#94a3b8",
    "LIMITED": "#f97316", "DISCONNECTED": "#ef4444", "ERROR": "#ef4444",
    "NOT CONFIGURED": "#64748b", "ENABLED": "#22c55e", "DISABLED": "#64748b",
    "INVALID": "#ef4444",
}
STATUS_TEXT = {
    "CONNECTED": "Connected", "READY": "Ready · unverified", "CHECKING": "Checking…",
    "LIMITED": "Usage limit", "DISCONNECTED": "Disconnected", "ERROR": "Error",
    "NOT CONFIGURED": "Not configured", "ENABLED": "Enabled", "DISABLED": "Disabled",
    "INVALID": "Load error",
}

AGENT_TOOLS = {"dev_agent", "code_helper", "ask_claude", "antigravity_coder",
               "omni_agent_acquire_clients", "chat_takeover"}


def qc(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(max(0, min(255, int(a))))
    return c


def status_color(state: str) -> str:
    return STATUS_COLORS.get((state or "").upper(), "#94a3b8")


# ── Icons ─────────────────────────────────────────────────────────────────────

def draw_icon(p: QPainter, kind: str, r: QRectF, color: QColor) -> None:
    """Small vector glyphs, drawn with the painter's current transform."""
    p.save()
    pen = QPen(color, max(1.2, r.width() * 0.08))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy, w, h = r.center().x(), r.center().y(), r.width(), r.height()
    s = min(w, h) / 2
    if kind == "claude":
        for i in range(10):
            a = i * math.pi / 5
            p.drawLine(QPointF(cx + math.cos(a) * s * 0.22, cy + math.sin(a) * s * 0.22),
                       QPointF(cx + math.cos(a) * s * 0.92, cy + math.sin(a) * s * 0.92))
    elif kind == "gemini":
        path = QPainterPath()
        path.moveTo(cx, cy - s)
        path.quadTo(cx, cy, cx + s, cy)
        path.quadTo(cx, cy, cx, cy + s)
        path.quadTo(cx, cy, cx - s, cy)
        path.quadTo(cx, cy, cx, cy - s)
        p.setBrush(QBrush(qc(color.name(), 70)))
        p.drawPath(path)
    elif kind == "mcp":
        for i in range(3):
            y = cy - s * 0.7 + i * s * 0.55
            p.drawRoundedRect(QRectF(cx - s * 0.75, y, s * 1.5, s * 0.4), 2, 2)
            p.drawPoint(QPointF(cx - s * 0.48, y + s * 0.2))
    elif kind == "tools":
        p.drawEllipse(QPointF(cx - s * 0.35, cy - s * 0.35), s * 0.35, s * 0.35)
        p.drawLine(QPointF(cx - s * 0.1, cy - s * 0.1), QPointF(cx + s * 0.75, cy + s * 0.75))
    elif kind == "plugins":
        for dx in (-1, 1):
            for dy in (-1, 1):
                p.drawRoundedRect(QRectF(cx + (dx - 1) * s * 0.42 + s * 0.06, cy + (dy - 1) * s * 0.42 + s * 0.06,
                                         s * 0.72, s * 0.72), 2, 2)
    elif kind == "agents":
        for dx, sc in ((-0.38, 0.8), (0.38, 0.8), (0, 1.0)):
            p.drawEllipse(QPointF(cx + dx * s, cy - s * 0.35 * sc), s * 0.22 * sc, s * 0.22 * sc)
            p.drawArc(QRectF(cx + dx * s - s * 0.4 * sc, cy, s * 0.8 * sc, s * 0.8 * sc), 0, 180 * 16)
    elif kind == "memory":
        p.drawRoundedRect(QRectF(cx - s * 0.55, cy - s * 0.55, s * 1.1, s * 1.1), 3, 3)
        for i in (-1, 0, 1):
            p.drawLine(QPointF(cx + i * s * 0.35, cy - s * 0.85), QPointF(cx + i * s * 0.35, cy - s * 0.55))
            p.drawLine(QPointF(cx + i * s * 0.35, cy + s * 0.55), QPointF(cx + i * s * 0.35, cy + s * 0.85))
            p.drawLine(QPointF(cx - s * 0.85, cy + i * s * 0.35), QPointF(cx - s * 0.55, cy + i * s * 0.35))
            p.drawLine(QPointF(cx + s * 0.55, cy + i * s * 0.35), QPointF(cx + s * 0.85, cy + i * s * 0.35))
    elif kind == "automation":
        poly = QPolygonF([QPointF(cx + s * 0.15, cy - s * 0.95), QPointF(cx - s * 0.5, cy + s * 0.1),
                          QPointF(cx - s * 0.02, cy + s * 0.1), QPointF(cx - s * 0.18, cy + s * 0.95),
                          QPointF(cx + s * 0.5, cy - s * 0.12), QPointF(cx + s * 0.02, cy - s * 0.12)])
        p.setBrush(QBrush(qc(color.name(), 90)))
        p.drawPolygon(poly)
    elif kind == "files":
        path = QPainterPath()
        path.moveTo(cx - s * 0.85, cy - s * 0.55)
        path.lineTo(cx - s * 0.25, cy - s * 0.55)
        path.lineTo(cx - s * 0.1, cy - s * 0.35)
        path.lineTo(cx + s * 0.85, cy - s * 0.35)
        path.lineTo(cx + s * 0.85, cy + s * 0.65)
        path.lineTo(cx - s * 0.85, cy + s * 0.65)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "core":
        p.drawEllipse(QPointF(cx, cy), s * 0.85, s * 0.85)
        p.drawEllipse(QPointF(cx, cy), s * 0.4, s * 0.4)
        for i in range(3):
            a = i * 2 * math.pi / 3 - math.pi / 2
            p.drawLine(QPointF(cx + math.cos(a) * s * 0.4, cy + math.sin(a) * s * 0.4),
                       QPointF(cx + math.cos(a) * s * 0.85, cy + math.sin(a) * s * 0.85))
    elif kind == "home":
        poly = QPolygonF([QPointF(cx - s * 0.8, cy - s * 0.05), QPointF(cx, cy - s * 0.8),
                          QPointF(cx + s * 0.8, cy - s * 0.05)])
        p.drawPolyline(poly)
        p.drawRect(QRectF(cx - s * 0.55, cy - s * 0.1, s * 1.1, s * 0.85))
    elif kind == "chat":
        p.drawRoundedRect(QRectF(cx - s * 0.85, cy - s * 0.7, s * 1.7, s * 1.15), 4, 4)
        p.drawLine(QPointF(cx - s * 0.35, cy + s * 0.45), QPointF(cx - s * 0.6, cy + s * 0.85))
    elif kind == "settings":
        p.drawEllipse(QPointF(cx, cy), s * 0.35, s * 0.35)
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(cx + math.cos(a) * s * 0.6, cy + math.sin(a) * s * 0.6),
                       QPointF(cx + math.cos(a) * s * 0.9, cy + math.sin(a) * s * 0.9))
        p.drawEllipse(QPointF(cx, cy), s * 0.62, s * 0.62)
    elif kind == "avatar":
        p.drawEllipse(QPointF(cx, cy - s * 0.25), s * 0.4, s * 0.45)
        p.drawArc(QRectF(cx - s * 0.8, cy + s * 0.15, s * 1.6, s * 1.3), 20 * 16, 140 * 16)
    elif kind == "orbit":
        p.drawEllipse(QPointF(cx, cy), s * 0.25, s * 0.25)
        p.drawEllipse(QRectF(cx - s * 0.9, cy - s * 0.4, s * 1.8, s * 0.8))
    elif kind == "workspace":
        p.drawRoundedRect(QRectF(cx - s * 0.85, cy - s * 0.6, s * 1.7, s * 1.2), 3, 3)
        p.drawLine(QPointF(cx - s * 0.85, cy - s * 0.25), QPointF(cx + s * 0.85, cy - s * 0.25))
    elif kind == "search":
        p.drawEllipse(QPointF(cx - s * 0.15, cy - s * 0.15), s * 0.55, s * 0.55)
        p.drawLine(QPointF(cx + s * 0.25, cy + s * 0.25), QPointF(cx + s * 0.85, cy + s * 0.85))
    elif kind == "live":
        for i, hh in enumerate((0.35, 0.8, 0.55, 0.95, 0.45)):
            x = cx - s * 0.8 + i * s * 0.4
            p.drawLine(QPointF(x, cy - s * hh * 0.8), QPointF(x, cy + s * hh * 0.8))
    else:
        p.setBrush(QBrush(color))
        p.drawEllipse(QPointF(cx, cy), s * 0.35, s * 0.35)
    p.restore()


def icon_pixmap(kind: str, color: str, size: int = 18) -> QIcon:
    pm = QPixmap(QSize(size * 2, size * 2))
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_icon(p, kind, QRectF(2, 2, size - 4, size - 4), QColor(color))
    p.end()
    return QIcon(pm)


# ── Graph model ───────────────────────────────────────────────────────────────

@dataclass
class GNode:
    id: str
    label: str
    cat: str
    sub: str = ""
    desc: str = ""
    status: str = ""
    hub: bool = False
    parent: str = ""
    icon: str = ""
    count: int = 0
    pos: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    # projected each frame
    sx: float = 0.0
    sy: float = 0.0
    depth: float = 0.0
    scale: float = 1.0
    radius: float = 4.0


@dataclass
class GEdge:
    a: str
    b: str
    kind: str = "tree"   # tree | hub | link


def build_graph(snap: dict, declarations: list, plugins: list, memory: dict,
                monitors: list, current_file: str = "", name: str = "ULTRON"
                ) -> tuple[dict, list]:
    """Nodes and edges from real application state only."""
    nodes: dict[str, GNode] = {}
    edges: list[GEdge] = []

    def add(nid, label, cat, parent="", hub=False, **kw) -> GNode:
        n = GNode(nid, label, cat, parent=parent, hub=hub, **kw)
        nodes[nid] = n
        if parent:
            edges.append(GEdge(parent, nid, "hub" if hub else "tree"))
        return n

    add("core", (name or "ULTRON").upper(), "core", hub=True, icon="core", sub="AI Core",
        desc="The assistant itself: routes every command to a provider and its tools.")

    c = snap.get("claude") or {}
    g = snap.get("gemini") or {}
    live = snap.get("gemini_live") or {}
    add("claude", "Claude", "provider", "core", hub=True, icon="claude", sub="AI Provider",
        status=c.get("state", "CHECKING"),
        desc=f"{c.get('integration') or 'Claude Code CLI'}. {c.get('detail') or ''}"
             + (f" Last error: {c['last_error']}" if c.get("last_error") else ""))
    add("claude.cli", "Claude Code CLI", "provider", "claude", status=c.get("state", ""),
        desc=c.get("integration", ""), sub=c.get("model", ""))
    add("gemini", "Gemini", "provider", "core", hub=True, icon="gemini", sub="AI Provider",
        status=g.get("state", "CHECKING"),
        desc=f"Google GenAI. {g.get('detail') or ''}"
             + (f" Last error: {g['last_error']}" if g.get("last_error") else ""))
    add("gemini.live", "Live voice session", "provider", "gemini",
        status="CONNECTED" if live.get("connected") else "DISCONNECTED",
        sub=(live.get("model") or "").removeprefix("models/"),
        desc="Real-time voice conversation; executes ULTRON's native tools."
             + (f" Last error: {live['error']}" if live.get("error") else ""))
    add("gemini.api", "GenAI REST API", "provider", "gemini", status=g.get("state", ""),
        desc="Model ladder for one-shot calls, grounded search and planning when Live is down.")

    mcp = snap.get("mcp") or []
    summ = snap.get("mcp_summary") or {}
    mstate = ("CONNECTED" if summ.get("connected") else
              ("DISCONNECTED" if summ.get("configured") else "NOT CONFIGURED"))
    add("mcp", "MCP Servers", "mcp", "core", hub=True, icon="mcp",
        sub=f"via Claude · {summ.get('connected', 0)}/{summ.get('configured', 0)}",
        status=mstate, count=summ.get("tools", 0),
        desc="Model Context Protocol servers connected to Claude Code (claude.ai connectors). "
             "ULTRON reaches them through Claude.")
    for s in mcp:
        sid = "mcp:" + s["label"]
        add(sid, s["label"], "mcp", "mcp", status=s.get("state", ""), count=len(s.get("tools") or []),
            desc=f"{s.get('detail') or ''}"
                 + (f" — {s['last_error']}" if s.get("last_error") else ""),
            sub=f"{len(s.get('tools') or [])} tools")
        for t in s.get("tools") or []:
            short = t.split("__")[-1]
            add("mcpt:" + t, short, "mcp_tool", sid, desc=f"MCP tool {t}")

    plugin_names = {p.get("name") for p in plugins or []}
    native = [d for d in declarations or [] if d.get("name") not in plugin_names]
    agents = [d for d in declarations or [] if d.get("name") in AGENT_TOOLS]
    tools = [d for d in native if d.get("name") not in AGENT_TOOLS]
    if tools:
        add("tools", "Tools", "tool", "core", hub=True, icon="tools",
            sub=f"Native · {len(tools)} available", count=len(tools),
            desc="ULTRON's native tools (actions and built-ins), executed locally.")
        for d in tools:
            add("tool:" + d["name"], d["name"], "tool", "tools",
                desc=str(d.get("description", ""))[:400])
    if agents:
        add("agents", "Agents", "agent", "core", hub=True, icon="agents",
            sub=f"{len(agents)} active", count=len(agents),
            desc="Multi-step workers that use Claude or Gemini as their brain.")
        for d in agents:
            add("tool:" + d["name"], d["name"], "agent", "agents",
                desc=str(d.get("description", ""))[:400])
    if plugins:
        add("plugins", "Plugins", "plugin", "core", hub=True, icon="plugins",
            sub=f"{sum(1 for p in plugins if p.get('enabled'))} enabled", count=len(plugins),
            desc="Plugins discovered in the plugins folder.")
        for p in plugins:
            st = "INVALID" if not p.get("valid") else ("ENABLED" if p.get("enabled") else "DISABLED")
            add("plugin:" + p["name"], p["name"], "plugin", "plugins", status=st,
                desc=(p.get("description") or p.get("error") or "")[:400],
                sub=str(p.get("health") or ""))

    mem_entries = 0
    if isinstance(memory, dict):
        add("memory", "Memory", "memory", "core", hub=True, icon="memory",
            sub="Long-term memory", desc="Facts ULTRON remembers about you (memory/long_term.json).")
        for cat, items in memory.items():
            if isinstance(items, dict) and items:
                cid = "memcat:" + cat
                add(cid, cat, "memory", "memory", count=len(items), sub=f"{len(items)} items")
                for k, v in items.items():
                    val = v.get("value") if isinstance(v, dict) else v
                    add(f"mem:{cat}:{k}", k, "memory", cid, desc=str(val)[:300])
                    mem_entries += 1
            elif isinstance(items, list) and items:
                add("memcat:" + cat, cat, "memory", "memory", count=len(items),
                    sub=f"{len(items)} records", desc=f"{len(items)} saved {cat}")
        nodes["memory"].sub = f"Memory system · {mem_entries} items"
        nodes["memory"].count = mem_entries

    add("automation", "Automation", "automation", "core", hub=True, icon="automation",
        sub=f"{len(monitors)} monitors", count=len(monitors),
        desc="Background topic monitors ULTRON checks daily (manage_monitor).")
    for m in monitors or []:
        add("mon:" + str(m), str(m), "automation", "automation", desc="Monitored topic")

    add("files", "Files", "files", "core", hub=True, icon="files",
        sub="1 loaded" if current_file else "No file loaded", count=1 if current_file else 0,
        desc="Files uploaded to ULTRON (drop zone / attach).")
    if current_file:
        import os
        add("file:" + current_file, os.path.basename(current_file), "files", "files",
            desc=current_file)

    # Real relationships between modules (the code paths that connect them).
    for a, b in (("claude", "mcp"), ("claude", "agents"), ("claude", "tools"),
                 ("gemini", "tools"), ("gemini", "plugins"), ("gemini", "memory"),
                 ("gemini", "automation"), ("gemini", "files"), ("gemini", "agents")):
        if a in nodes and b in nodes:
            edges.append(GEdge(a, b, "link"))
    _layout(nodes)
    return nodes, edges


def _seed(s: str) -> random.Random:
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return random.Random(h)


def _layout(nodes: dict[str, GNode]) -> None:
    """Deterministic 3-D layout: hubs on a tilted ring around the core, children
    in clusters pushed outward from their hub. Same data → same picture."""
    order = ["claude", "gemini", "tools", "plugins", "files", "automation", "memory",
             "agents", "mcp"]
    hubs = [h for h in order if h in nodes]
    R = 250.0
    for i, h in enumerate(hubs):
        a = -math.pi / 2 + i * 2 * math.pi / max(1, len(hubs))
        r = _seed(h)
        nodes[h].pos = [math.cos(a) * R, math.sin(a) * R * 0.78, (r.random() - 0.5) * 120]
    nodes["core"].pos = [0.0, 0.0, 0.0]

    children: dict[str, list[GNode]] = {}
    for n in nodes.values():
        if n.parent and n.id not in hubs:
            children.setdefault(n.parent, []).append(n)

    def place(parent: GNode, depth: int):
        kids = children.get(parent.id, [])
        if not kids:
            return
        px, py, pz = parent.pos
        norm = math.sqrt(px * px + py * py + pz * pz) or 1.0
        ox, oy, oz = px / norm, py / norm, pz / norm
        spread = 95.0 if depth == 1 else 46.0
        base = 70.0 if depth == 1 else 38.0
        golden = math.pi * (3 - math.sqrt(5))
        for k, n in enumerate(kids):
            rr = _seed(n.id)
            t = (k + 0.5) / len(kids)
            yy = 1 - 2 * t
            rad = math.sqrt(max(0.0, 1 - yy * yy))
            th = golden * k + rr.random() * 0.4
            dx, dy, dz = math.cos(th) * rad, yy, math.sin(th) * rad
            dist = base + spread * (0.55 + 0.45 * rr.random()) * min(1.6, 0.6 + len(kids) / 18)
            n.pos = [px + (dx * 0.75 + ox * 0.55) * dist,
                     py + (dy * 0.75 + oy * 0.55) * dist,
                     pz + (dz * 0.75 + oz * 0.55) * dist]
            place(n, depth + 1)

    for h in hubs:
        place(nodes[h], 1)


# ── Neural graph widget ───────────────────────────────────────────────────────

class NeuralGraph(QWidget):
    node_selected = pyqtSignal(str)
    node_hovered = pyqtSignal(str)

    FPS_MS = 33

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self.P = palette
        self.setMouseTracking(True)
        self.setMinimumSize(360, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.nodes: dict[str, GNode] = {}
        self.edges: list[GEdge] = []
        self._adj: dict[str, set] = {}
        # camera
        self.yaw, self.pitch, self.zoom = 0.0, 0.0, 1.0
        self._t_yaw, self._t_pitch, self._t_zoom = 0.0, 0.0, 1.0
        self.focus = [0.0, 0.0, 0.0]
        self._t_focus = [0.0, 0.0, 0.0]
        self.pan = QPointF(0, 0)
        self.mode3d = True
        self.auto_rotate = True
        # filters
        self.show_edges = True
        self.show_leaf = {"tool": True, "mcp_tool": True, "plugin": True, "memory": True,
                          "agent": True, "automation": True, "files": True, "mcp": True,
                          "provider": True}
        self.show_status = True
        self.hidden_cats: set[str] = set()
        self.search = ""
        self.selected = ""
        self.hovered = ""
        self._drag = None
        self._last_interaction = 0.0
        self._t0 = time.monotonic()
        rnd = random.Random(7)
        self._dust = [(rnd.uniform(-1, 1) * 700, rnd.uniform(-1, 1) * 520, rnd.uniform(-1, 1) * 600,
                       rnd.random()) for _ in range(260)]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._order: list[GNode] = []
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    # ── data ─────────────────────────────────────────────────────────────
    def fit_zoom(self) -> float:
        """Zoom at which every node fits the widget with a margin."""
        if not self.nodes or self.width() < 10:
            return 1.0
        ext_x = max(abs(n.pos[0]) for n in self.nodes.values()) + 40
        ext_y = max(abs(n.pos[1]) for n in self.nodes.values()) + 40
        base = min(self.width(), self.height()) / 700.0
        zx = (self.width() * 0.47) / (ext_x * base)
        zy = (self.height() * 0.46) / (ext_y * base)
        return max(0.35, min(1.3, zx, zy))

    def set_graph(self, nodes: dict, edges: list) -> None:
        first = not self.nodes
        self.nodes, self.edges = nodes, edges
        if first:
            self.zoom = self._t_zoom = self.fit_zoom()
        self._adj = {}
        for e in edges:
            self._adj.setdefault(e.a, set()).add(e.b)
            self._adj.setdefault(e.b, set()).add(e.a)
        if self.selected and self.selected not in self.nodes:
            self.selected = ""
        self.update()

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.nodes.values():
            if not n.hub:
                out[n.cat] = out.get(n.cat, 0) + 1
        return out

    def neighbours(self, nid: str) -> list[str]:
        return sorted(self._adj.get(nid, set()))

    def matches(self, text: str, limit: int = 12) -> list[GNode]:
        t = (text or "").lower().strip()
        if not t:
            return []
        scored = []
        for n in self.nodes.values():
            hay = f"{n.label} {n.sub} {n.desc} {CATS.get(n.cat, ('', ''))[0]}".lower()
            if t in hay:
                scored.append((0 if n.label.lower().startswith(t) else (1 if t in n.label.lower() else 2),
                               0 if n.hub else 1, n.label.lower(), n))
        scored.sort(key=lambda x: x[:3])
        return [s[3] for s in scored[:limit]]

    # ── camera API ───────────────────────────────────────────────────────
    def focus_node(self, nid: str, select: bool = True) -> None:
        n = self.nodes.get(nid)
        if not n:
            return
        self._t_focus = list(n.pos) if self.mode3d else [n.pos[0], n.pos[1], 0.0]
        fit = self.fit_zoom()
        self._t_zoom = fit * (1.9 if not n.hub else 1.45)
        if nid == "core":
            self._t_zoom = fit
        if select:
            self.selected = nid
            self.node_selected.emit(nid)
        self._last_interaction = time.monotonic()
        self._ensure_running()

    def reset_view(self) -> None:
        self._t_focus = [0.0, 0.0, 0.0]
        self._t_zoom, self._t_yaw, self._t_pitch = self.fit_zoom(), 0.0, 0.0
        self.pan = QPointF(0, 0)
        self._ensure_running()

    def zoom_by(self, f: float) -> None:
        self._t_zoom = max(0.35, min(4.0, self._t_zoom * f))
        self._ensure_running()

    def set_mode3d(self, on: bool) -> None:
        self.mode3d = bool(on)
        if not self.mode3d:
            self._t_yaw = self._t_pitch = 0.0
        self._ensure_running()

    def set_search(self, text: str) -> None:
        self.search = (text or "").strip().lower()
        self.update()

    # ── animation ────────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        self._ensure_running()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.selected and abs(self._t_focus[0]) + abs(self._t_focus[1]) < 1:
            self._t_zoom = self.fit_zoom()
            self._ensure_running()

    def _ensure_running(self):
        if self.isVisible() and not self._timer.isActive():
            self._timer.start(self.FPS_MS)

    def _tick(self):
        k = 0.12
        idle = time.monotonic() - self._last_interaction > 3.0
        if self.auto_rotate and self.mode3d and idle and self._drag is None:
            self._t_yaw += 0.0028
        self.yaw += (self._t_yaw - self.yaw) * k
        self.pitch += (self._t_pitch - self.pitch) * k
        self.zoom += (self._t_zoom - self.zoom) * k
        for i in range(3):
            self.focus[i] += (self._t_focus[i] - self.focus[i]) * k
        self.update()

    # ── projection ───────────────────────────────────────────────────────
    def _project(self, x, y, z):
        x -= self.focus[0]
        y -= self.focus[1]
        z -= self.focus[2]
        if self.mode3d:
            cy, sy = math.cos(self.yaw), math.sin(self.yaw)
            x, z = x * cy + z * sy, -x * sy + z * cy
            cp, sp = math.cos(self.pitch), math.sin(self.pitch)
            y, z = y * cp - z * sp, y * sp + z * cp
            f = 900.0 / (900.0 + z)
        else:
            f = 1.0
        base = min(self.width(), self.height()) / 700.0
        s = f * self.zoom * base
        return (self.width() / 2 + self.pan.x() + x * s,
                self.height() / 2 + self.pan.y() + y * s, z, s)

    def _visible(self, n: GNode) -> bool:
        if n.cat in self.hidden_cats and n.id != "core":
            return False
        if not n.hub and not self.show_leaf.get(n.cat, True):
            return False
        if n.parent and n.parent in self.nodes and not n.hub:
            par = self.nodes[n.parent]
            if not par.hub and not self._visible(par):
                return False
        return True

    # ── painting ─────────────────────────────────────────────────────────
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bg = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.75)
        bg.setColorAt(0.0, qc("#0a1a2c"))
        bg.setColorAt(0.55, qc(self.P.BG))
        bg.setColorAt(1.0, qc("#010205"))
        p.fillRect(self.rect(), QBrush(bg))

        t = time.monotonic() - self._t0
        # dust (decorative only)
        p.setPen(Qt.PenStyle.NoPen)
        for (x, y, z, r) in self._dust:
            sx, sy, sz, s = self._project(x + self.focus[0] * 0.3, y + self.focus[1] * 0.3, z)
            if 0 <= sx <= w and 0 <= sy <= h:
                a = 30 + 50 * r * (0.6 + 0.4 * math.sin(t * 0.7 + r * 9))
                p.setBrush(QBrush(qc("#5cc8ff", a)))
                p.drawEllipse(QPointF(sx, sy), 0.7 + r, 0.7 + r)

        if not self.nodes:
            p.setPen(qc(self.P.TEXT_DIM))
            p.setFont(QFont(FONT_UI, 10))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Building the system graph…")
            return

        vis = {}
        for n in self.nodes.values():
            if not self._visible(n):
                continue
            n.sx, n.sy, n.depth, n.scale = self._project(*n.pos)
            base = 26 if n.id == "core" else (19 if n.hub else (6 if n.cat != "mcp_tool" else 3.6))
            if n.cat == "mcp" and not n.hub:
                base = 9
            n.radius = base * max(0.45, min(2.4, n.scale))
            vis[n.id] = n

        focus_set = set()
        sel = self.selected or self.hovered
        if sel and sel in vis:
            focus_set = {sel} | self._adj.get(sel, set())
        search_hits = set()
        if self.search:
            for n in vis.values():
                if self.search in n.label.lower() or self.search in n.sub.lower():
                    search_hits.add(n.id)

        # edges
        if self.show_edges:
            for e in sorted(self.edges, key=lambda e: -(vis[e.a].depth + vis[e.b].depth)
                            if e.a in vis and e.b in vis else 0):
                a, b = vis.get(e.a), vis.get(e.b)
                if not a or not b:
                    continue
                col_a = qc(CATS.get(b.cat if e.kind != "link" else a.cat, ("", self.P.PRI))[1])
                depth_a = max(0.25, min(1.0, 1.1 - (a.depth + b.depth) / 1600))
                alpha = (40 if e.kind == "tree" else 85 if e.kind == "hub" else 70) * depth_a
                hl = e.a in focus_set and e.b in focus_set
                dim = (focus_set and not hl) or (search_hits and not (e.a in search_hits or e.b in search_hits))
                if hl:
                    alpha = 220
                elif dim:
                    alpha *= 0.25
                broken = any(self._is_down(x) for x in (a, b)) and e.kind != "tree"
                width = (1.8 if e.kind != "tree" else 0.9) * (1.6 if hl else 1.0)
                if e.kind != "tree":
                    glow = QPen(qc(col_a.name(), alpha * 0.25), width * 4)
                    glow.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(glow)
                    p.drawLine(QPointF(a.sx, a.sy), QPointF(b.sx, b.sy))
                pen = QPen(qc("#ef4444" if broken else col_a.name(), alpha), width)
                if broken:
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(a.sx, a.sy), QPointF(b.sx, b.sy))
                # signal pulses along live provider/hub links
                if (e.kind in ("hub", "link") and not broken and self.show_status
                        and self._is_live(a) and self._is_live(b)):
                    ph = (t * 0.35 + (hash(e.a + e.b) % 100) / 100.0) % 1.0
                    px, py = a.sx + (b.sx - a.sx) * ph, a.sy + (b.sy - a.sy) * ph
                    p.setPen(Qt.PenStyle.NoPen)
                    g = QRadialGradient(QPointF(px, py), 6)
                    g.setColorAt(0, qc("#ffffff", 220))
                    g.setColorAt(0.4, qc(col_a.name(), 160))
                    g.setColorAt(1, qc(col_a.name(), 0))
                    p.setBrush(QBrush(g))
                    p.drawEllipse(QPointF(px, py), 6, 6)

        # nodes, far to near
        self._order = sorted(vis.values(), key=lambda n: -n.depth)
        for n in self._order:
            self._paint_node(p, n, t, focus_set, search_hits)

        # hover tooltip
        hv = vis.get(self.hovered)
        if hv and not hv.hub:
            self._paint_tip(p, hv)
        p.end()

    def _is_down(self, n: GNode) -> bool:
        return (n.status or "").upper() in ("DISCONNECTED", "ERROR", "NOT CONFIGURED")

    def _is_live(self, n: GNode) -> bool:
        st = (n.status or "").upper()
        return st in ("CONNECTED", "ENABLED", "") and n.cat != "mcp_tool"

    def _paint_node(self, p: QPainter, n: GNode, t: float, focus_set: set, hits: set):
        col = qc(CATS.get(n.cat, ("", self.P.PRI))[1])
        if n.id == "core":
            col = qc(self.P.PRI)
        r = n.radius
        depth_a = max(0.35, min(1.0, 1.15 - n.depth / 900)) if self.mode3d else 1.0
        dim = (focus_set and n.id not in focus_set) or (hits and n.id not in hits)
        a = depth_a * (0.3 if dim else 1.0)
        c = QPointF(n.sx, n.sy)
        p.setPen(Qt.PenStyle.NoPen)
        glow_r = r * (2.6 if n.hub else 2.2)
        pulse = 1.0 + (0.08 * math.sin(t * 2.2 + len(n.id)) if n.hub else 0)
        g = QRadialGradient(c, glow_r * pulse)
        g.setColorAt(0, qc(col.name(), 120 * a))
        g.setColorAt(0.45, qc(col.name(), 45 * a))
        g.setColorAt(1, qc(col.name(), 0))
        p.setBrush(QBrush(g))
        p.drawEllipse(c, glow_r * pulse, glow_r * pulse)
        if n.hub:
            p.setBrush(QBrush(qc("#050b14", 235 * max(a, 0.6))))
            p.setPen(QPen(qc(col.name(), 255 * a), 2.0))
            p.drawEllipse(c, r, r)
            p.setPen(QPen(qc(col.name(), 90 * a), 1.0))
            p.drawEllipse(c, r * 1.22, r * 1.22)
            # rotating segment ring on hubs
            seg = QPen(qc(col.name(), 180 * a), 1.6)
            seg.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(seg)
            rr = QRectF(n.sx - r * 1.22, n.sy - r * 1.22, r * 2.44, r * 2.44)
            start = int((t * 40 + len(n.id) * 30) % 360 * 16)
            p.drawArc(rr, start, 50 * 16)
            p.drawArc(rr, start + 180 * 16, 50 * 16)
            draw_icon(p, n.icon, QRectF(n.sx - r * 0.55, n.sy - r * 0.55, r * 1.1, r * 1.1),
                      qc(col.name() if n.id != "core" else "#e8fbff", 255 * a))
            if n.id == "core":
                p.setFont(QFont(FONT_HEAD, max(7, int(8 * n.scale)), QFont.Weight.Bold))
                p.setPen(qc("#e8fbff", 255 * a))
                p.drawText(QRectF(n.sx - 60, n.sy + r * 0.45, 120, 18),
                           Qt.AlignmentFlag.AlignCenter, n.label)
            self._paint_hub_label(p, n, col, a)
        else:
            p.setBrush(QBrush(qc(col.name(), 230 * a)))
            p.setPen(QPen(qc("#ffffff", 120 * a), 0.8))
            p.drawEllipse(c, r, r)
            show_label = (n.id in (self.selected, self.hovered) or n.id in hits
                          or (n.cat == "mcp" and r > 7) or (self.zoom > 1.9 and r > 5))
            if show_label:
                p.setFont(QFont(FONT_UI, 8))
                p.setPen(qc("#dbeafe", 230 * a))
                p.drawText(QPointF(n.sx + r + 4, n.sy + 4), n.label)
        if self.show_status and n.status and (n.hub or n.cat in ("mcp", "provider", "plugin")):
            sc = qc(status_color(n.status), 255 * a)
            p.setPen(QPen(qc("#020409"), 1.2))
            p.setBrush(QBrush(sc))
            d = max(3.0, r * 0.28)
            p.drawEllipse(QPointF(n.sx + r * 0.72, n.sy - r * 0.72), d, d)
        if n.id == self.selected:
            sp = QPen(qc("#ffffff", 220), 1.4)
            sp.setStyle(Qt.PenStyle.DashLine)
            p.setPen(sp)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r + 6, r + 6)

    def _paint_hub_label(self, p: QPainter, n: GNode, col: QColor, a: float):
        if n.id == "core":
            return
        r = n.radius
        right = n.sx >= self.width() / 2 + self.pan.x() - 2
        x = n.sx + r * 1.45 + 6 if right else n.sx - r * 1.45 - 6 - 170
        align = Qt.AlignmentFlag.AlignLeft if right else Qt.AlignmentFlag.AlignRight
        y = n.sy - 22
        p.setFont(QFont(FONT_HEAD, 11, QFont.Weight.DemiBold))
        p.setPen(qc("#f1f8ff", 255 * a))
        p.drawText(QRectF(x, y, 170, 18), align | Qt.AlignmentFlag.AlignVCenter, n.label)
        p.setFont(QFont(FONT_UI, 8))
        p.setPen(qc("#9fb6cc", 230 * a))
        p.drawText(QRectF(x, y + 17, 170, 15), align | Qt.AlignmentFlag.AlignVCenter, n.sub)
        if n.status and self.show_status:
            st = STATUS_TEXT.get(n.status.upper(), n.status.title())
            p.setPen(qc(status_color(n.status), 255 * a))
            p.drawText(QRectF(x, y + 31, 170, 15), align | Qt.AlignmentFlag.AlignVCenter, "● " + st)

    def _paint_tip(self, p: QPainter, n: GNode):
        lines = [n.label, CATS.get(n.cat, ("", ""))[0]]
        if n.status:
            lines[1] += " · " + STATUS_TEXT.get(n.status.upper(), n.status)
        if n.desc:
            d = n.desc.strip().replace("\n", " ")
            lines.append(d[:110] + ("…" if len(d) > 110 else ""))
        p.setFont(QFont(FONT_UI, 8))
        fm = QFontMetricsF(p.font())
        wdt = min(300.0, max(fm.horizontalAdvance(l) for l in lines) + 18)
        hgt = 16 * len(lines) + 8
        x = min(self.width() - wdt - 6, n.sx + 12)
        y = max(6, n.sy - hgt - 10)
        p.setPen(QPen(qc(CATS.get(n.cat, ("", "#22e1ff"))[1], 200), 1))
        p.setBrush(QBrush(qc("#06101c", 240)))
        p.drawRoundedRect(QRectF(x, y, wdt, hgt), 6, 6)
        for i, l in enumerate(lines):
            p.setPen(qc("#f1f8ff" if i == 0 else "#9fb6cc"))
            p.drawText(QRectF(x + 9, y + 4 + i * 16, wdt - 14, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(l, Qt.TextElideMode.ElideRight, wdt - 16))

    # ── interaction ──────────────────────────────────────────────────────
    def _hit(self, pos: QPointF) -> Optional[GNode]:
        best, bd = None, 1e9
        for n in reversed(self._order):
            d = math.hypot(n.sx - pos.x(), n.sy - pos.y())
            if d <= max(n.radius + 4, 8) and d < bd:
                best, bd = n, d
                if n.hub:
                    break
        return best

    def mousePressEvent(self, e):
        self._last_interaction = time.monotonic()
        pan = (e.button() == Qt.MouseButton.RightButton or
               (e.modifiers() & Qt.KeyboardModifier.ShiftModifier) or not self.mode3d)
        self._drag = (e.position(), "pan" if pan else "rotate", False)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._ensure_running()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag:
            start, kind, moved = self._drag
            d = pos - start
            if abs(d.x()) + abs(d.y()) > 3:
                moved = True
            if kind == "rotate":
                self._t_yaw += d.x() * 0.008
                self._t_pitch = max(-1.2, min(1.2, self._t_pitch + d.y() * 0.008))
                self.yaw, self.pitch = self._t_yaw, self._t_pitch
            else:
                self.pan += d
            self._drag = (pos, kind, moved)
            self._last_interaction = time.monotonic()
            self.update()
            return
        n = self._hit(pos)
        nid = n.id if n else ""
        if nid != self.hovered:
            self.hovered = nid
            self.setCursor(Qt.CursorShape.PointingHandCursor if n else Qt.CursorShape.OpenHandCursor)
            if nid:
                self.node_hovered.emit(nid)
            self.update()

    def mouseReleaseEvent(self, e):
        moved = self._drag[2] if self._drag else False
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if not moved and e.button() == Qt.MouseButton.LeftButton:
            n = self._hit(e.position())
            if n:
                self.focus_node(n.id)
            else:
                self.selected = ""
                self.node_selected.emit("")
                self.update()

    def mouseDoubleClickEvent(self, e):
        if not self._hit(e.position()):
            self.reset_view()

    def wheelEvent(self, e):
        self._last_interaction = time.monotonic()
        self.zoom_by(1.12 if e.angleDelta().y() > 0 else 1 / 1.12)

    def leaveEvent(self, e):
        self.hovered = ""
        self.update()


# ── Graph chrome: toolbar, legend, zoom controls, compass ─────────────────────

def _chip_style(P) -> str:
    return (f"QPushButton, QToolButton {{ color: #cfe6f7; background: rgba(8,20,34,0.85); "
            f"border: 1px solid #17324a; border-radius: 7px; padding: 4px 10px; "
            f"font-family: '{FONT_UI}'; font-size: 9pt; }}"
            f"QPushButton:hover, QToolButton:hover {{ border-color: {P.PRI}; color: #ffffff; }}"
            f"QPushButton:checked {{ border-color: {P.PRI}; color: {P.PRI}; }}"
            f"QToolButton::menu-indicator {{ image: none; }}")


class GraphPanel(QFrame):
    """NeuralGraph plus the overlays drawn over it in the reference design."""

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self.P = palette
        self.setObjectName("GraphPanel")
        self.setStyleSheet(f"QFrame#GraphPanel {{ background: {palette.BG}; border: 1px solid #10263a; "
                           f"border-radius: 12px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        self.graph = NeuralGraph(palette, self)
        lay.addWidget(self.graph)

        st = _chip_style(palette)
        self._bar = QWidget(self)
        bl = QHBoxLayout(self._bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        self.focus_btn = QPushButton("  Focus")
        self.focus_btn.setIcon(icon_pixmap("core", "#cfe6f7", 14))
        self.focus_btn.setToolTip("Centre the selected node (or the core)")
        self.focus_btn.clicked.connect(lambda: self.graph.focus_node(self.graph.selected or "core",
                                                                     select=bool(self.graph.selected)))
        self.filter_btn = QToolButton()
        self.filter_btn.setText("  Filter")
        self.filter_btn.setIcon(icon_pixmap("plugins", "#cfe6f7", 14))
        self.filter_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.filter_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._filter_menu = QMenu(self.filter_btn)
        self._filter_menu.setStyleSheet(f"QMenu {{ background: #07121f; color: #d8eefc; border: 1px solid #17324a; }}"
                                        f"QMenu::item:selected {{ background: #0d2236; }}")
        for cat, (label, colr) in CATS.items():
            if cat == "core":
                continue
            act = self._filter_menu.addAction(icon_pixmap("dot", colr, 12), label)
            act.setCheckable(True)
            act.setChecked(True)
            act.toggled.connect(lambda on, c=cat: self._toggle_cat(c, on))
        self.filter_btn.setMenu(self._filter_menu)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search nodes")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon_pixmap("search", "#7f9bb3", 14), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setFixedWidth(180)
        self.search.setStyleSheet(f"QLineEdit {{ color: #e6f4ff; background: rgba(8,20,34,0.85); "
                                  f"border: 1px solid #17324a; border-radius: 7px; padding: 3px 6px; "
                                  f"font-family: '{FONT_UI}'; font-size: 9pt; }}"
                                  f"QLineEdit:focus {{ border-color: {palette.PRI}; }}")
        self.search.textChanged.connect(self.graph.set_search)
        self.search.returnPressed.connect(self._search_go)
        for wdg in (self.focus_btn, self.filter_btn):
            wdg.setStyleSheet(st)
            wdg.setCursor(Qt.CursorShape.PointingHandCursor)
            bl.addWidget(wdg)
        bl.addWidget(self.search)

        self._zoom = QWidget(self)
        zl = QVBoxLayout(self._zoom)
        zl.setContentsMargins(0, 0, 0, 0)
        zl.setSpacing(4)
        for txt, tip, fn in (("+", "Zoom in", lambda: self.graph.zoom_by(1.25)),
                             ("−", "Zoom out", lambda: self.graph.zoom_by(0.8)),
                             ("⤢", "Reset view", self.graph.reset_view)):
            b = QPushButton(txt)
            b.setFixedSize(28, 28)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(st + "QPushButton { padding: 0; font-size: 12pt; }")
            b.clicked.connect(fn)
            zl.addWidget(b)

        self.mode_btn = QPushButton("3D", self)
        self.mode_btn.setCheckable(True)
        self.mode_btn.setChecked(True)
        self.mode_btn.setFixedSize(36, 26)
        self.mode_btn.setToolTip("Switch between 3-D and flat 2-D view")
        self.mode_btn.setStyleSheet(st + "QPushButton { padding: 0; font-weight: bold; }")
        self.mode_btn.toggled.connect(self._mode)

        self.legend = _Legend(self)
        self.compass = _Compass(self.graph, self)

    def _toggle_cat(self, cat: str, on: bool):
        if on:
            self.graph.hidden_cats.discard(cat)
        else:
            self.graph.hidden_cats.add(cat)
        self.graph.update()

    def _mode(self, on: bool):
        self.mode_btn.setText("3D" if on else "2D")
        self.graph.set_mode3d(on)

    def _search_go(self):
        hits = self.graph.matches(self.search.text(), 1)
        if hits:
            self.graph.focus_node(hits[0].id)

    def update_legend(self):
        self.legend.set_counts(self.graph.counts(), self.graph.nodes)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._bar.adjustSize()
        self._bar.move(14, 12)
        self._zoom.adjustSize()
        self._zoom.move(14, self.height() - self._zoom.height() - 14)
        self.legend.adjustSize()
        self.legend.move(self.width() - self.legend.width() - 12, 12)
        self.mode_btn.move(self.width() - self.mode_btn.width() - 14, self.height() - 40)
        self.compass.move(self.width() - self.compass.width() - 14, self.height() - 40 - self.compass.height() - 6)


class _Legend(QWidget):
    """Category counts, painted. Rows come from the real graph."""

    def __init__(self, parent):
        super().__init__(parent)
        self._rows: list[tuple[str, str, int]] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(10, 10)

    def set_counts(self, counts: dict, nodes: dict):
        rows = [("provider", "Providers", sum(1 for n in nodes.values() if n.cat == "provider" and n.hub)),
                ("mcp", "MCP Servers", sum(1 for n in nodes.values() if n.cat == "mcp" and not n.hub)),
                ("mcp_tool", "MCP Tools", counts.get("mcp_tool", 0)),
                ("tool", "Tools", counts.get("tool", 0)),
                ("agent", "Agents", counts.get("agent", 0)),
                ("plugin", "Plugins", counts.get("plugin", 0)),
                ("memory", "Memory", sum(1 for n in nodes.values() if n.id.startswith("mem:"))),
                ("automation", "Automation", counts.get("automation", 0)),
                ("files", "Files", counts.get("files", 0))]
        self._rows = [(c, l, n) for c, l, n in rows if not (c == "agent" and n == 0)]
        if self.parent() is not None and self.parent().width() < 620:
            self.hide()
        self.setFixedSize(168, 14 + 18 * len(self._rows))
        if self.parent():
            self.move(self.parent().width() - self.width() - 12, 12)
        self.update()

    def paintEvent(self, _e):
        if not self._rows:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qc("#17324a"), 1))
        p.setBrush(QBrush(qc("#06101b", 225)))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 9, 9)
        p.setFont(QFont(FONT_UI, 8))
        for i, (cat, label, n) in enumerate(self._rows):
            y = 8 + i * 18
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qc(CATS[cat][1])))
            p.drawEllipse(QPointF(16, y + 9), 4, 4)
            p.setPen(qc("#cfe0ee"))
            p.drawText(QRectF(28, y, 100, 18), Qt.AlignmentFlag.AlignVCenter, label)
            p.setPen(qc("#e6f4ff"))
            p.drawText(QRectF(100, y, self.width() - 112, 18),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(n))


class _Compass(QWidget):
    def __init__(self, graph: NeuralGraph, parent):
        super().__init__(parent)
        self.g = graph
        self.setFixedSize(56, 56)
        t = QTimer(self)
        t.timeout.connect(self.update)
        t.start(100)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(28, 28)
        p.setPen(QPen(qc("#1f3d57"), 1))
        p.drawEllipse(c, 20, 20)
        p.setFont(QFont(FONT_UI, 6))
        p.setPen(qc("#7f9bb3"))
        for lbl, a in (("N", -90), ("E", 0), ("S", 90), ("W", 180)):
            r = math.radians(a)
            p.drawText(QRectF(c.x() + math.cos(r) * 25 - 5, c.y() + math.sin(r) * 25 - 5, 10, 10),
                       Qt.AlignmentFlag.AlignCenter, lbl)
        a = -self.g.yaw
        tip = QPointF(c.x() + math.sin(a) * 14, c.y() - math.cos(a) * 14)
        l = QPointF(c.x() + math.sin(a + 2.5) * 8, c.y() - math.cos(a + 2.5) * 8)
        r = QPointF(c.x() + math.sin(a - 2.5) * 8, c.y() - math.cos(a - 2.5) * 8)
        p.setBrush(QBrush(qc("#22e1ff", 200)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([tip, l, c, r]))


# ── AI core ───────────────────────────────────────────────────────────────────

CORE_STATES = {
    # state: (colour, speed, intensity)
    "IDLE":         ("#22d3ee", 0.45, 0.55),
    "LISTENING":    ("#2ee6a6", 0.8, 0.85),
    "THINKING":     ("#a78bfa", 1.6, 0.9),
    "PROCESSING":   ("#f5b942", 1.9, 1.0),
    "RESPONDING":   ("#38e1ff", 1.2, 1.0),
    "CONNECTED":    ("#22d3ee", 0.6, 0.7),
    "DISCONNECTED": ("#64748b", 0.2, 0.35),
    "MUTED":        ("#64748b", 0.3, 0.45),
    "SLEEPING":     ("#3b6b8a", 0.18, 0.35),
    "ERROR":        ("#ef4444", 0.9, 0.8),
}


class AICore(QWidget):
    """Concentric animated rings with U L T R O N at the centre.

    `fx` (core/cinematic/core_fx.py) layers the motion that keeps it alive —
    listening rings, processing streams, speech arcs, idle sparks, the error
    glitch — and its power state: dormant during the boot cinematic, spun up
    by power_on() once the dashboard is revealed."""

    def __init__(self, name: str = "ULTRON", parent=None):
        super().__init__(parent)
        self.name = (name or "ULTRON").upper()
        self.state = "IDLE"
        self.substate = ""
        self.level = 0.0
        self._phase = 0.0
        self._last = time.monotonic()
        self.fx = CoreFx()
        self.accent = ""         # theme colour; "" = the built-in state colours
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(230)

    def set_state(self, state: str, substate: str = ""):
        state = (state or "IDLE").upper()
        if state not in CORE_STATES:
            state = "IDLE"
        self.state, self.substate = state, substate
        self.fx.on_state(state, time.monotonic())
        self.update()

    def set_level(self, level: float):
        self.level = max(0.0, min(1.0, float(level or 0)))

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: ULTRON's own speech levels, scheduled on the wall clock."""
        try:
            self.fx.push_schedule([f[0] for f in frames], hop, at)
        except Exception:
            pass

    # The states that show the theme colour; error / muted / offline keep their
    # meaning in every theme.
    THEMED = frozenset({"IDLE", "LISTENING", "THINKING", "PROCESSING", "RESPONDING", "CONNECTED"})

    def set_accent(self, hex_color: str) -> None:
        self.accent = (hex_color or "").strip()
        self.update()

    def state_color(self, state: str | None = None) -> str:
        state = (state or self.state).upper()
        if self.accent and state in self.THEMED:
            return self.accent
        return CORE_STATES.get(state, CORE_STATES["IDLE"])[0]

    def power_on(self) -> None:
        self.fx.power_on(time.monotonic())
        if self.isVisible():
            self._timer.start(16)

    def glitch(self) -> None:
        self.fx.glitch(time.monotonic())

    def pulse(self, strength: float = 1.0) -> None:
        """One energy ring out of the core — e.g. a new colour has landed."""
        self.fx.pulses.append((time.monotonic(), max(0.0, min(1.0, strength))))
        if self.isVisible():
            self._timer.start(16)

    def showEvent(self, e):
        super().showEvent(e)
        self._last = time.monotonic()
        self._timer.start(33)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def _radius(self) -> float:
        return min(self.width(), self.height()) / 2 - 6

    def _fx_broken(self) -> None:
        """The motion layer threw: switch it off and keep the plain core
        running — decoration must never take the dashboard down with it."""
        import traceback
        traceback.print_exc()
        self.fx.enabled = False
        self.fx._power, self.fx._power_t0, self.fx._glitch_until = 1.0, None, 0.0

    def _tick(self):
        now = time.monotonic()
        dt, self._last = now - self._last, now
        dt = min(0.1, max(0.0, dt))
        try:
            boost = self.fx.spin_boost(now)
            self.fx.step(dt, now, self.level, self._radius())
            busy = self.fx.busy(now)
        except Exception:
            self._fx_broken()
            boost, busy = 1.0, False
        self._phase += dt * CORE_STATES[self.state][1] * boost
        self.level *= 0.9 ** (dt / 0.033)       # same decay at 30 or 60 fps
        # 60 fps only while something is actually moving fast.
        want = 16 if busy else 33
        if self._timer.interval() != want:
            self._timer.setInterval(want)
        self.update()

    def paintEvent(self, _e):
        now = time.monotonic()
        try:
            glitching = self.fx.glitching(now)
        except Exception:
            self._fx_broken()
            glitching = False
        if glitching:
            dpr = self.devicePixelRatioF() or 1.0
            frame = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            frame.setDevicePixelRatio(dpr)
            frame.fill(Qt.GlobalColor.transparent)
            q = QPainter(frame)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            p = None
            try:
                self._paint_core(q, now)
                q.end()
                p = QPainter(self)
                self.fx.paint_glitch(p, frame, self.width(), self.height(), now)
                return
            except Exception:
                self._fx_broken()
            finally:
                if q.isActive():
                    q.end()
                if p is not None:
                    p.end()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._paint_core(p, now)
        except Exception:
            self._fx_broken()
        finally:
            p.end()

    def _paint_core(self, p: QPainter, now: float):
        _col, _speed, inten = CORE_STATES[self.state]
        col_hex = self.state_color()
        inten *= self.fx.intensity(now)
        col = QColor(col_hex)
        w, h = self.width(), self.height()
        c = QPointF(w / 2, h / 2)
        R = self._radius()
        ph = self._phase
        sway = self.fx.sway(now)
        if sway:
            p.translate(c)
            p.rotate(sway)
            p.translate(-c)

        glow = QRadialGradient(c, R * 1.05)
        glow.setColorAt(0, qc(col_hex, 40 * inten))
        glow.setColorAt(0.6, qc(col_hex, 18 * inten))
        glow.setColorAt(1, qc(col_hex, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(c, R, R)

        # outer tick ring
        p.save()
        p.translate(c)
        p.rotate(ph * 6)
        for i in range(72):
            a = math.radians(i * 5)
            long = i % 6 == 0
            p.setPen(QPen(qc(col_hex, (150 if long else 60) * inten), 1.3 if long else 1))
            r1 = R * (0.9 if long else 0.93)
            p.drawLine(QPointF(math.cos(a) * r1, math.sin(a) * r1),
                       QPointF(math.cos(a) * R * 0.97, math.sin(a) * R * 0.97))
        p.restore()

        def arc_ring(radius, width, segs, rot, alpha, span_frac=0.7):
            pen = QPen(qc(col_hex, alpha), width)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(c.x() - radius, c.y() - radius, radius * 2, radius * 2)
            step = 360.0 / segs
            for i in range(segs):
                start = rot + i * step
                p.drawArc(rect, int(start * 16), int(step * span_frac * 16))

        arc_ring(R * 0.82, 6, 12, ph * 30, 170 * inten, 0.62)
        arc_ring(R * 0.71, 2, 3, -ph * 55, 230 * inten, 0.22)
        arc_ring(R * 0.66, 1.2, 36, ph * 12, 90 * inten, 0.5)

        # radar sweep
        sweep = QConicalGradient(c, -(ph * 70) % 360)
        sweep.setColorAt(0.0, qc(col_hex, 110 * inten))
        sweep.setColorAt(0.12, qc(col_hex, 0))
        sweep.setColorAt(1.0, qc(col_hex, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sweep))
        p.drawEllipse(c, R * 0.6, R * 0.6)

        # inner disc
        pulse = 1.0 + 0.04 * math.sin(ph * 4) + 0.12 * self.level
        inner = QRadialGradient(c, R * 0.52 * pulse)
        inner.setColorAt(0, qc("#071a2a"))
        inner.setColorAt(0.8, qc("#04101b"))
        inner.setColorAt(1, qc(col_hex, 160 * inten))
        p.setBrush(QBrush(inner))
        p.setPen(QPen(qc(col_hex, 220 * inten), 2))
        p.drawEllipse(c, R * 0.5 * pulse, R * 0.5 * pulse)

        power = self.fx.power(now)
        f = QFont(FONT_HEAD, max(10, int(R * 0.15)), QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, max(2, R * 0.035))
        p.setFont(f)
        p.setPen(qc("#eafcff", 60 + 195 * power))
        p.drawText(QRectF(c.x() - R, c.y() - R * 0.16, R * 2, R * 0.3), Qt.AlignmentFlag.AlignCenter,
                   self.name)
        p.setFont(QFont(FONT_UI, max(7, int(R * 0.065))))
        p.setPen(qc(col_hex, 230 * power) if power > 0.05 else qc("#94a3b8", 120))
        p.drawText(QRectF(c.x() - R, c.y() + R * 0.13, R * 2, R * 0.14), Qt.AlignmentFlag.AlignCenter,
                   (self.state + (f" · {self.substate}" if self.substate else "")) if power > 0.05
                   else "OFFLINE")
        self.fx.paint(p, c, R, now)


# ── Small widgets ─────────────────────────────────────────────────────────────

class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = True, color: str = "#22d3ee", parent=None):
        super().__init__(parent)
        self._on = bool(checked)
        self._color = color
        self.setFixedSize(34, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, on: bool):
        if bool(on) != self._on:
            self._on = bool(on)
            self.update()
            self.toggled.emit(self._on)

    def mouseReleaseEvent(self, _e):
        self.setChecked(not self._on)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qc(self._color, 200) if self._on else qc("#1c2c3c")))
        p.drawRoundedRect(QRectF(0, 2, 34, 14), 7, 7)
        p.setBrush(QBrush(qc("#ffffff")))
        p.drawEllipse(QPointF(25 if self._on else 9, 9), 6.5, 6.5)


class ConnectionRow(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, key: str, title: str, icon: str, color: str, parent=None, compact=False):
        super().__init__(parent)
        self.key = key
        self.setObjectName("ConnRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QFrame#ConnRow { background: rgba(9,22,37,0.75); border: 1px solid #15304a; "
                           "border-radius: 9px; } QFrame#ConnRow:hover { border-color: #2b6f96; }"
                           "QLabel { background: transparent; border: none; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7 if compact else 9, 10, 7 if compact else 9)
        lay.setSpacing(10)
        self._icon = _IconBadge(icon, color, 30 if compact else 34)
        lay.addWidget(self._icon)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = QLabel(title)
        self.title.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        self.title.setStyleSheet("color: #eaf6ff;")
        self.sub = QLabel("—")
        self.sub.setFont(QFont(FONT_UI, 7))
        self.sub.setStyleSheet("color: #7f9bb3;")
        for lab in (self.title, self.sub):
            lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lab.setMinimumWidth(40)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.state = QLabel("● Checking…")
        self.state.setFont(QFont(FONT_UI, 8))
        self.state.setStyleSheet("color: #94a3b8;")
        lay.addWidget(self.state, 0, Qt.AlignmentFlag.AlignTop if not compact else Qt.AlignmentFlag.AlignVCenter)
        if not compact:
            chev = QLabel("›")
            chev.setFont(QFont(FONT_UI, 13))
            chev.setStyleSheet("color: #4f6d85;")
            lay.addWidget(chev)

    def set_status(self, state: str, sub: str = "", tip: str = ""):
        colr = status_color(state)
        self.state.setText(f"● {STATUS_TEXT.get((state or '').upper(), state or '—')}")
        self.state.setStyleSheet(f"color: {colr};")
        self._full_sub = sub or "—"
        self._elide()
        self.setToolTip("\n".join(x for x in (sub, tip) if x))

    def _elide(self):
        fm = QFontMetricsF(self.sub.font())
        self.sub.setText(fm.elidedText(getattr(self, "_full_sub", "—"), Qt.TextElideMode.ElideRight,
                                       max(30.0, self.sub.width() - 2)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def mouseReleaseEvent(self, e):
        self.clicked.emit(self.key)
        super().mouseReleaseEvent(e)


class _IconBadge(QWidget):
    def __init__(self, icon: str, color: str, size: int = 32, parent=None):
        super().__init__(parent)
        self._icon, self._color = icon, color
        self.setFixedSize(size, size)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.width() / 2
        g = QRadialGradient(QPointF(r, r), r)
        g.setColorAt(0, qc(self._color, 70))
        g.setColorAt(1, qc(self._color, 10))
        p.setBrush(QBrush(g))
        p.setPen(QPen(qc(self._color, 200), 1.4))
        p.drawEllipse(QPointF(r, r), r - 1.5, r - 1.5)
        draw_icon(p, self._icon, QRectF(r * 0.45, r * 0.45, r * 1.1, r * 1.1), QColor(self._color))


class StageStrip(QWidget):
    """Checking provider → Checking tools → Executing → Completed, from the
    stages the router and the tool gate really report."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(2, 0, 2, 0)
        self._lay.setSpacing(6)
        self._stages: list[str] = []
        self._final = ""
        self._label = QLabel("")
        self._label.setFont(QFont(FONT_UI, 8))
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._lay.addWidget(self._label, 1)
        self._clear = QTimer(self)
        self._clear.setSingleShot(True)
        self._clear.timeout.connect(self.reset)
        self.reset()

    def reset(self):
        self._stages, self._final = [], ""
        self._label.setText("<span style='color:#4f6d85'>Ready</span>")

    def update_task(self, task: dict):
        state = (task.get("state") or "").upper()
        stage = task.get("stage") or ""
        if state == "PROCESSING":
            if self._final:
                self._stages, self._final = [], ""
            if stage and (not self._stages or self._stages[-1] != stage):
                self._stages.append(stage)
            self._clear.stop()
        elif state in ("COMPLETED", "FAILED"):
            self._final = state
            if stage and (not self._stages or self._stages[-1] != stage):
                self._stages.append(stage)
            self._clear.start(9000)
        else:
            return
        parts = []
        for i, s in enumerate(self._stages[-6:]):
            last = i == len(self._stages[-6:]) - 1
            if last and self._final == "COMPLETED":
                c = "#22c55e"
            elif last and self._final == "FAILED":
                c = "#ef4444"
            elif last:
                c = "#38e1ff"
            else:
                c = "#7f9bb3"
            parts.append(f"<span style='color:{c}'>{'●' if last else '○'} {s}</span>")
        prov = task.get("provider") or ""
        head = f"<span style='color:#4f6d85'>{prov.title()} </span>" if prov else ""
        self._label.setText(head + " <span style='color:#2b4a63'>→</span> ".join(parts))


class NodeInspector(QFrame):
    action = pyqtSignal(str, str)   # (action, node_id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Inspector")
        self.setStyleSheet("QFrame#Inspector { background: rgba(9,22,37,0.6); border: 1px solid #15304a; "
                           "border-radius: 9px; } QLabel { background: transparent; border: none; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)
        self.title = QLabel("Select a node")
        self.title.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        self.title.setStyleSheet("color: #eaf6ff;")
        self.meta = QLabel("Click any node in the graph to inspect it.")
        self.meta.setFont(QFont(FONT_UI, 7))
        self.meta.setStyleSheet("color: #7f9bb3;")
        self.meta.setWordWrap(True)
        self.desc = QLabel("")
        self.desc.setFont(QFont(FONT_UI, 8))
        self.desc.setStyleSheet("color: #b6cadb;")
        self.desc.setWordWrap(True)
        self.desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for w_ in (self.title, self.meta, self.desc):
            w_.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            w_.setWordWrap(True)
            lay.addWidget(w_)
        self.btns = QHBoxLayout()
        self.btns.setSpacing(5)
        lay.addLayout(self.btns)
        self._nid = ""

    def show_node(self, n: Optional[GNode], neighbours: list, actions: list):
        while self.btns.count():
            it = self.btns.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if n is None:
            self._nid = ""
            self.title.setText("Select a node")
            self.meta.setText("Click any node in the graph to inspect it.")
            self.desc.setText("")
            return
        self._nid = n.id
        cat_label, colr = CATS.get(n.cat, ("", "#22e1ff"))
        self.title.setText(f"<span style='color:{colr}'>●</span> {n.label}")
        st = (" · " + STATUS_TEXT.get(n.status.upper(), n.status)) if n.status else ""
        self.meta.setText(f"{cat_label}{st} · {len(neighbours)} connections"
                          + (f" · {n.sub}" if n.sub else ""))
        self.desc.setText((n.desc or "")[:420])
        for act, label in actions:
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("QPushButton { color: #cfe6f7; background: #0b1c2e; border: 1px solid #1d3d58; "
                            "border-radius: 6px; padding: 3px 8px; font-size: 8pt; }"
                            "QPushButton:hover { border-color: #22d3ee; color: #ffffff; }")
            b.clicked.connect(lambda _c=False, a=act: self.action.emit(a, self._nid))
            self.btns.addWidget(b)
        self.btns.addStretch()


def section_label(text: str) -> QLabel:
    l = QLabel(text.upper())
    l.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
    l.setStyleSheet("color: #7dd3fc; background: transparent; letter-spacing: 1px;")
    return l
