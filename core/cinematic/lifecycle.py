"""
core/cinematic/lifecycle.py — the one place that knows which state ULTRON's
window is in:

    OFFLINE → BOOTING → INITIALIZING → ONLINE → SHUTTING_DOWN → OFFLINE

Every animation asks it before starting, which is what keeps the boot and the
shutdown from ever running on top of each other, makes a second click on the
power button a no-op, and lets a shutdown cut a boot short cleanly.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

OFFLINE = "OFFLINE"
BOOTING = "BOOTING"
INITIALIZING = "INITIALIZING"
ONLINE = "ONLINE"
SHUTTING_DOWN = "SHUTTING_DOWN"

_ALLOWED: dict[str, frozenset[str]] = {
    OFFLINE:       frozenset({BOOTING, ONLINE}),           # ONLINE = intro skipped
    BOOTING:       frozenset({INITIALIZING, ONLINE, SHUTTING_DOWN}),
    INITIALIZING:  frozenset({ONLINE, SHUTTING_DOWN}),
    ONLINE:        frozenset({SHUTTING_DOWN}),
    SHUTTING_DOWN: frozenset({OFFLINE}),
}


class Lifecycle(QObject):
    changed = pyqtSignal(str, str)   # (old, new)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = OFFLINE
        self._seen_shutdown = False

    @property
    def state(self) -> str:
        return self._state

    def can(self, new: str) -> bool:
        return new in _ALLOWED.get(self._state, frozenset())

    def go(self, new: str) -> bool:
        """Move to `new` if that is a legal step. Returns False (and changes
        nothing) otherwise, so callers can use it as a guard."""
        if not self.can(new):
            return False
        old, self._state = self._state, new
        try:
            print(f"[Lifecycle] {old} -> {new}")
        except Exception:
            pass
        self.changed.emit(old, new)
        return True

    @property
    def is_shutting_down(self) -> bool:
        """True from the moment a shutdown starts until the process ends —
        including the final OFFLINE, which is not the same as never booted."""
        return self._seen_shutdown

    def begin_shutdown(self) -> bool:
        if self.go(SHUTTING_DOWN):
            self._seen_shutdown = True
            return True
        return False
