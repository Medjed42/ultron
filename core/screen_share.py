"""
Screen Share & Automation Subsystem for Jarvis.

Provides:
- Screen & Window sharing state management (active / inactive, monitor or window source)
- Multi-source enumeration (Screens / Displays & Application Windows / Tabs)
- Multi-backend screen capture (mss, PIL ImageGrab, pyautogui)
- Multi-tier visual element locator with smart scoring & metric-counter disqualification:
    1. Windows UI Automation (pywinauto / accessibility) for instant native app lookup
       (prioritizes clickable Buttons over text/counters like "1.2M subscribers")
    2. Gemini Multimodal Vision API with explicit button-vs-counter differentiation
- Screen pointer overlay invocation (animated bouncing arrow + pulsing reticle)
- Complete screen automation (clicks, coordinates, typing, shortcuts, dragging, scrolling)
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Tuple, Dict, Any, List

try:
    import pyautogui
    pyautogui.FAILSAFE = False  # Avoid panic if cursor touches screen edges during automation
except ImportError:
    pyautogui = None

try:
    import mss
    import mss.tools
    _HAS_MSS = True
except ImportError:
    _HAS_MSS = False

try:
    import PIL.Image
    import PIL.ImageGrab
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

import core.gemini as gemini


# Synonym dictionary for Serbian and English terms
SYNONYMS: Dict[str, List[str]] = {
    "settings": [
        "settings", "podešavanja", "podesavanja", "postavke", "options", "opcije",
        "preferences", "preferencije", "gear", "alatke", "konfiguracija"
    ],
    "subscribe": [
        "subscribe", "pretplati se", "pretplata", "subscribed", "pretplaćen",
        "prijavi se", "učlani se", "sub", "pridruži se"
    ],
    "like": ["like", "sviđa mi se", "lajk", "palac gore", "thumbs up"],
    "dislike": ["dislike", "ne sviđa mi se", "dislajk", "palac dole", "thumbs down"],
    "share": ["share", "podeli", "šeruj", "deli"],
    "search": ["search", "pretraga", "traži", "trazi", "find", "pronadji", "pronađi", "upit"],
    "close": ["close", "zatvori", "exit", "izlaz", "ugasi", "x"],
    "minimize": ["minimize", "minimizuj", "spusti", "umanji"],
    "maximize": ["maximize", "maksimizuj", "uvećaj", "uvecaj", "fullscreen"],
    "save": ["save", "sačuvaj", "sacuvaj", "snimi"],
    "open": ["open", "otvori"],
    "new": ["new", "novo", "novi", "kreiraj", "create"],
    "profile": ["profile", "profil", "nalog", "account", "user", "korisnik", "avatar"],
    "notifications": ["notifications", "notifikacije", "obaveštenja", "obavestenja", "bell", "zvono"],
    "download": ["download", "downloads", "preuzimanja", "preuzmi", "skini"],
    "file": ["file", "fajl", "datoteka"],
    "edit": ["edit", "izmeni", "uredi", "uređivanje"],
    "help": ["help", "pomoć", "pomoc", "about", "o programu"],
    "browser": ["chrome", "edge", "firefox", "browser", "pregledač"],
}


class ScreenShareManager:
    _instance: Optional[ScreenShareManager] = None

    def __new__(cls) -> ScreenShareManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._active: bool = False
        self._ui_callback: Optional[Callable[[bool], None]] = None
        self._pointer_callback: Optional[Callable[[int, int, str, Optional[List[int]], float], None]] = None
        self._last_capture_time: float = 0.0

        # Source selection state
        self._target_type: str = "screen"       # "screen" | "window"
        self._target_hwnd: Optional[int] = None
        self._target_title: str = "Entire Screen"
        self._target_process: str = ""
        self._target_screen_index: int = 0

    # ── State & Target Management ─────────────────────────────────────────
    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> bool:
        self._active = bool(active)
        if self._ui_callback:
            try:
                self._ui_callback(self._active)
            except Exception as e:
                print(f"[ScreenShare] UI callback error: {e}")
        return self._active

    def toggle(self) -> bool:
        return self.set_active(not self._active)

    def set_target(
        self,
        target_type: str = "screen",
        hwnd: Optional[int] = None,
        screen_index: int = 0,
        title: str = "",
        process: str = ""
    ):
        """Sets the active share target (a specific monitor or application window)."""
        self._target_type = target_type
        self._target_hwnd = hwnd
        self._target_screen_index = screen_index
        self._target_title = title or ("Entire Screen" if target_type == "screen" else "Selected Window")
        self._target_process = process
        print(f"[ScreenShare] Target set to {target_type}: {self._target_title} (hwnd={hwnd})")

    def get_target_info(self) -> Dict[str, Any]:
        return {
            "type": self._target_type,
            "hwnd": self._target_hwnd,
            "title": self._target_title,
            "process": self._target_process,
            "screen_index": self._target_screen_index,
            "active": self._active
        }

    def get_available_sources(self) -> Dict[str, Any]:
        """
        Enumerates available monitors and top-level open application windows
        for the Discord-like source selection dialog.
        """
        screens = []
        try:
            from PyQt6.QtWidgets import QApplication
            app_screens = QApplication.screens()
            for idx, s in enumerate(app_screens):
                geom = s.geometry()
                is_pri = (s == QApplication.primaryScreen())
                screens.append({
                    "index": idx,
                    "name": f"Screen {idx + 1}" + (" (Primary)" if is_pri else ""),
                    "width": geom.width(),
                    "height": geom.height(),
                    "is_primary": is_pri
                })
        except Exception:
            pass
        if not screens:
            screens.append({"index": 0, "name": "Primary Screen", "width": 1920, "height": 1080, "is_primary": True})

        windows = []
        try:
            import win32gui, win32process, win32api, psutil
            win32api.SetLastError(0)

            def enum_cb(hwnd, _):
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return True
                    title = win32gui.GetWindowText(hwnd).strip()
                    if not title or len(title) < 2:
                        return True
                    # Skip common background / system utility windows
                    if title in ("Program Manager", "Settings", "Default IME", "MSCTFIME UI", "Windows Input Experience"):
                        return True
                    rect = win32gui.GetWindowRect(hwnd)
                    w, h = rect[2] - rect[0], rect[3] - rect[1]
                    if w < 140 or h < 140:
                        return True
                    pname = ""
                    try:
                        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                        pname = psutil.Process(pid).name()
                    except Exception:
                        pass
                    # Exclude Jarvis itself from the window share picker
                    if "python" in pname.lower() and ("ULTRON" in title or "JARVIS" in title):
                        return True

                    windows.append({
                        "hwnd": hwnd,
                        "title": title,
                        "process": pname,
                        "width": w,
                        "height": h,
                        "rect": [rect[0], rect[1], rect[2], rect[3]]
                    })
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(enum_cb, None)
        except Exception as e:
            print(f"[ScreenShare] Window enumeration error: {e}")

        # If EnumWindows returned empty (e.g. session permissions), populate from running GUI processes
        if not windows:
            try:
                import psutil
                known_gui = {"chrome.exe": "Google Chrome", "discord.exe": "Discord", "msedge.exe": "Microsoft Edge",
                             "code.exe": "Visual Studio Code", "spotify.exe": "Spotify", "notepad.exe": "Notepad"}
                for p in psutil.process_iter(["name", "pid"]):
                    try:
                        n = (p.info["name"] or "").lower()
                        if n in known_gui:
                            windows.append({
                                "hwnd": 0,
                                "title": known_gui[n],
                                "process": p.info["name"],
                                "width": 1920,
                                "height": 1080,
                                "rect": [0, 0, 1920, 1080]
                            })
                    except Exception:
                        pass
            except Exception:
                pass

        return {"screens": screens, "windows": windows}

    def register_ui_callback(self, cb: Callable[[bool], None]):
        self._ui_callback = cb

    def register_pointer_callback(self, cb: Callable[[int, int, str, Optional[List[int]], float], None]):
        self._pointer_callback = cb

    # ── Screen Capture ───────────────────────────────────────────────────
    def get_screen_size(self) -> Tuple[int, int]:
        if pyautogui:
            size = pyautogui.size()
            return int(size.width), int(size.height)
        return 1920, 1080

    def capture_screen(self, max_w: int = 1280, max_h: int = 720, quality: int = 85) -> Tuple[bytes, str, int, int]:
        """
        Captures the screen or targeted window using the best available backend.
        Returns: (jpeg_bytes, mime_type, real_screen_w, real_screen_h)
        """
        real_w, real_h = self.get_screen_size()
        pil_img = None

        # If a specific window target is active, focus and raise it first
        if self._target_type == "window" and self._target_hwnd:
            try:
                import win32gui, win32con
                if win32gui.IsWindow(self._target_hwnd):
                    if win32gui.IsIconic(self._target_hwnd):
                        win32gui.ShowWindow(self._target_hwnd, win32con.SW_RESTORE)
                    win32gui.BringWindowToTop(self._target_hwnd)
                    win32gui.SetForegroundWindow(self._target_hwnd)
                    time.sleep(0.08)
            except Exception:
                pass

        # Try MSS first
        if _HAS_MSS:
            try:
                with mss.mss() as sct:
                    monitors = sct.monitors
                    # If a specific monitor index was chosen
                    m_idx = self._target_screen_index + 1
                    target = monitors[m_idx] if 0 < m_idx < len(monitors) else (monitors[1] if len(monitors) > 1 else monitors[0])
                    real_w, real_h = int(target["width"]), int(target["height"])
                    shot = sct.grab(target)
                    if _HAS_PIL:
                        pil_img = PIL.Image.frombytes("RGB", shot.size, shot.rgb)
                    else:
                        png = mss.tools.to_png(shot.rgb, shot.size)
                        return png, "image/png", real_w, real_h
            except Exception:
                pass

        # Try PIL ImageGrab
        if pil_img is None and _HAS_PIL:
            try:
                pil_img = PIL.ImageGrab.grab(all_screens=False)
                real_w, real_h = pil_img.size
            except Exception:
                pass

        # Try PyAutoGUI screenshot
        if pil_img is None and pyautogui:
            try:
                pil_img = pyautogui.screenshot()
                real_w, real_h = pil_img.size
            except Exception:
                pass

        if pil_img is None:
            raise RuntimeError("Ne mogu da napravim snimak ekrana. Proverite dozvole grafike.")

        # Resize for vision API efficiency while maintaining aspect ratio
        img_copy = pil_img.copy()
        img_copy.thumbnail((max_w, max_h), PIL.Image.Resampling.BILINEAR)

        buf = io.BytesIO()
        img_copy.save(buf, format="JPEG", quality=quality, optimize=True)
        jpeg_bytes = buf.getvalue()
        self._last_capture_time = time.monotonic()

        return jpeg_bytes, "image/jpeg", real_w, real_h

    # ── Element Locator ──────────────────────────────────────────────────
    def _clean_query(self, query: str) -> str:
        q = query.lower().strip()
        patterns = [
            r"^(e\s+)?(hej\s+)?(jarvis[ea]?|ultron[ea]?)\b\s*,?\s*",
            r"^(molim\s+te\s+)?",
            r"^gde\s+je\s+(ovde\s+)?(dugme\s+(za\s+)?)?",
            r"^gde\s+se\s+nalazi\s+(ovde\s+)?(dugme\s+(za\s+)?)?",
            r"^poka[zž]i\s+mi\s+(gde\s+je\s+)?(dugme\s+(za\s+)?)?",
            r"^na[dđ]i\s+(mi\s+)?(gde\s+je\s+)?(dugme\s+(za\s+)?)?",
            r"^ozna[cč]i\s+(mi\s+)?",
            r"^where\s+is\s+(the\s+)?(button\s+)?",
            r"^show\s+me\s+(the\s+)?",
            r"^point\s+to\s+(the\s+)?",
            r"^look\s+for\s+(the\s+)?",
            r"\?+$",
            r"^dugme\s+(za\s+)?",
            r"^button\s+",
            r"\s+(button|icon|ikona|dugme|taster)$",
            r"\s+(na\s+youtube|na\s+yt|u\s+chrome|na\s+ekranu|u\s+pregleda[cč]u|ovde)$",
            r"\s+(on\s+youtube|on\s+yt|in\s+chrome|on\s+screen|here)$",
        ]
        prev = ""
        while prev != q:
            prev = q
            for p in patterns:
                q = re.sub(p, "", q).strip()
        return q or query.strip()

    def _get_target_variations(self, target: str) -> List[str]:
        target_clean = self._clean_query(target).lower()
        variations = [target_clean]
        for k, syn_list in SYNONYMS.items():
            if target_clean in syn_list or k in target_clean or any(s in target_clean for s in syn_list):
                variations.extend(syn_list)
        return list(dict.fromkeys(variations))

    def _locate_via_accessibility(self, target: str) -> Optional[Dict[str, Any]]:
        """
        Searches controls of the targeted or foreground window using UI Automation.
        Uses intelligent candidate scoring to strictly prioritize clickable Buttons
        over nearby metric counters (e.g. picks the Subscribe button over '1.2M subscribers').
        """
        try:
            import win32gui
            from pywinauto import Desktop
            from computer_control.browser.existing_browser_controller import ExistingBrowserController

            # If a specific window was chosen, use that hwnd; otherwise foreground
            hwnd = self._target_hwnd if (self._target_type == "window" and self._target_hwnd and win32gui.IsWindow(self._target_hwnd)) else win32gui.GetForegroundWindow()
            if not hwnd:
                return None
            win_title = win32gui.GetWindowText(hwnd)

            variations = self._get_target_variations(target)
            root = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
            controls = ExistingBrowserController.controls(root, limit=350)

            best_candidate = None
            highest_score = -999999

            for c in controls:
                try:
                    rect = c.rectangle()
                    if rect.width() <= 0 or rect.height() <= 0:
                        continue

                    text_raw = (c.window_text() or "").strip()
                    text_l = text_raw.lower()
                    name_l = (c.element_info.name or "").strip().lower()
                    auto_id = (c.element_info.automation_id or "").strip().lower()
                    ctrl_type = str(c.element_info.control_type or "")

                    score = 0
                    matched = False

                    # 1. Matching text/name against variations
                    for v in variations:
                        if v == text_l or v == name_l:
                            score += 1200
                            matched = True
                            break
                        elif re.search(rf"\b{re.escape(v)}\b", text_l) or re.search(rf"\b{re.escape(v)}\b", name_l):
                            score += 500
                            matched = True
                            break
                        elif len(v) > 3 and (v in text_l or v in name_l or v in auto_id):
                            score += 150
                            matched = True
                            break

                    if not matched:
                        continue

                    # 2. Control Type Bonus / Penalty
                    # Buttons and Interactive controls get strong priority
                    if ctrl_type in ("Button", "MenuItem", "Hyperlink", "TabItem"):
                        score += 600
                    elif ctrl_type in ("Text", "Static", "Pane", "Group"):
                        score -= 300

                    # 3. CRITICAL ANTI-COUNTER PENALTY:
                    # Penalize metric labels showing subscriber counts, views, or follower stats
                    # E.g. "1.25M subscribers", "240k pratilaca", "10M views"
                    if re.search(r"(\d+[\d\.,\s]*[kmb]?\s*(subscribers?|pratilaca|views|pregleda|likes))", text_l):
                        score -= 3500
                    if "subscribers" in text_l and not (ctrl_type == "Button" and text_l == "subscribers"):
                        score -= 2500

                    # 4. Button dimension sanity bonus
                    if 15 < rect.width() < 600 and 15 < rect.height() < 160:
                        score += 100

                    if score > highest_score:
                        highest_score = score
                        best_candidate = (c, rect, text_raw or ctrl_type, score)

                except Exception:
                    continue

            # If a candidate with a positive score was found, return it
            if best_candidate and highest_score > 0:
                c, rect, label, score = best_candidate
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                bbox = [rect.left, rect.top, rect.right, rect.bottom]
                desc = self._describe_coords(cx, cy, target, win_title)
                print(f"[ScreenShare] UIA found '{label}' with score {score} at ({cx}, {cy})")
                return {
                    "found": True,
                    "target": target,
                    "x": cx,
                    "y": cy,
                    "bbox": bbox,
                    "description": desc,
                    "source": "uia"
                }

        except Exception as e:
            print(f"[ScreenShare] UIA lookup exception: {e}")

        return None

    def _describe_coords(self, x: int, y: int, target: str, window_name: str = "") -> str:
        w, h = self.get_screen_size()
        pos_x = "sredini"
        pos_y = "sredini"

        if x < w * 0.33:
            pos_x = "levoj strani"
        elif x > w * 0.66:
            pos_x = "desnoj strani"

        if y < h * 0.33:
            pos_y = "vrhu"
        elif y > h * 0.66:
            pos_y = "dnu"

        if pos_y == "vrhu" and pos_x == "desnoj strani":
            quadrant = "u gornjem desnom uglu"
        elif pos_y == "vrhu" and pos_x == "levoj strani":
            quadrant = "u gornjem levom uglu"
        elif pos_y == "vrhu":
            quadrant = "na vrhu ekrana na sredini"
        elif pos_y == "dnu" and pos_x == "desnoj strani":
            quadrant = "u donjem desnom uglu"
        elif pos_y == "dnu" and pos_x == "levoj strani":
            quadrant = "u donjem levom uglu"
        elif pos_y == "dnu":
            quadrant = "pri dnu ekrana"
        elif pos_x != "sredini":
            quadrant = f"na {pos_x} ekrana"
        else:
            quadrant = "na sredini ekrana"

        win_info = f" prozora '{window_name}'" if window_name else " ekrana"
        return f"{target.capitalize()} se nalazi {quadrant}{win_info}."

    def _locate_via_gemini(self, target: str) -> Optional[Dict[str, Any]]:
        """
        Uses Gemini Vision with strict instructions distinguishing clickable action buttons
        from nearby metric text (e.g. picks the Subscribe button over subscriber count).
        """
        try:
            clean_t = self._clean_query(target)
            jpeg_bytes, mime, screen_w, screen_h = self.capture_screen()

            prompt = (
                f"You are an expert AI screen locator for JARVIS. The user wants to locate the UI element: '{clean_t}'.\n"
                f"Recognize Serbian and English equivalents (e.g. settings/podešavanja, subscribe/pretplati se, search/pretraga, close/zatvori).\n\n"
                f"CRITICAL RULES FOR BUTTONS VS METRIC COUNTERS:\n"
                f"- If looking for an action button (e.g. 'Subscribe button' on YouTube, 'Like button', 'Follow button', 'Download button'):\n"
                f"  * You MUST locate the CLICKABLE BUTTON (e.g. the rounded pill-shaped button with text 'Subscribe' or 'Pretplati se').\n"
                f"  * DO NOT select the text counter showing subscriber numbers or statistics (e.g. DO NOT select '1.45M subscribers', '250K views', '10K likes').\n"
                f"  * The Subscribe button on YouTube is typically a distinct solid or outlined pill button to the right of or below the channel name.\n"
                f"  * Point directly to the center of the CLICKABLE BUTTON itself.\n\n"
                f"Examine this screenshot carefully and locate '{clean_t}'.\n"
                f"Return ONLY a JSON dictionary with:\n"
                f'{{\n'
                f'  "found": true,\n'
                f'  "element_name": "{clean_t}",\n'
                f'  "box_2d": [ymin, xmin, ymax, xmax],\n'
                f'  "button_center_pixel": [x_pixel, y_pixel],\n'
                f'  "description_sr": "Dugme {clean_t} se nalazi u gornjem desnom delu, pored naziva kanala."\n'
                f'}}\n'
                f"If the element is definitely not present or invisible anywhere on the screen, return:\n"
                f'{{\n'
                f'  "found": false,\n'
                f'  "description_sr": "Nisam uspeo da pronađem {clean_t} na trenutnom ekranu."\n'
                f'}}\n'
                f"Note: box_2d are normalized coordinates from 0 to 1000 [ymin, xmin, ymax, xmax]. "
                f"button_center_pixel are pixel coordinates [x, y] in the image (width {screen_w}, height {screen_h})."
            )

            # Pass to Gemini with inline image data
            content_part = {"inline_data": {"mime_type": mime, "data": jpeg_bytes}}
            result = gemini.as_json([prompt, content_part], tier=gemini.SMART)

            if not result or not isinstance(result, dict) or not result.get("found"):
                desc = (result.get("description_sr") if isinstance(result, dict) else "") or f"Nisam pronašao '{clean_t}' na ekranu."
                return {"found": False, "target": clean_t, "description": desc}

            x, y = screen_w // 2, screen_h // 2
            bbox = None

            # 1. Prefer explicit button_center_pixel
            if "button_center_pixel" in result and isinstance(result["button_center_pixel"], (list, tuple)):
                bcp = result["button_center_pixel"]
                if len(bcp) == 2:
                    x = int(bcp[0])
                    y = int(bcp[1])

            # 2. Or calculate from box_2d [ymin, xmin, ymax, xmax]
            if "box_2d" in result and isinstance(result["box_2d"], (list, tuple)):
                b = result["box_2d"]
                if len(b) == 4:
                    ymin, xmin, ymax, xmax = [float(val) for val in b]
                    bx1 = int(xmin / 1000.0 * screen_w)
                    by1 = int(ymin / 1000.0 * screen_h)
                    bx2 = int(xmax / 1000.0 * screen_w)
                    by2 = int(ymax / 1000.0 * screen_h)
                    bbox = [bx1, by1, bx2, by2]
                    if not result.get("button_center_pixel"):
                        x = (bx1 + bx2) // 2
                        y = (by1 + by2) // 2

            desc = result.get("description_sr") or self._describe_coords(x, y, clean_t)
            print(f"[ScreenShare] Gemini located '{clean_t}' at ({x}, {y})")
            return {
                "found": True,
                "target": clean_t,
                "x": x,
                "y": y,
                "bbox": bbox,
                "description": desc,
                "source": "gemini"
            }
        except Exception as e:
            print(f"[ScreenShare] Gemini locator error: {e}")
            return None

    def locate_element(self, target: str) -> Dict[str, Any]:
        """
        Locates target on screen using UI Automation first, then Gemini Vision.
        """
        clean_t = self._clean_query(target)

        # Tier 1: Fast accessibility lookup with anti-counter scoring
        uia_res = self._locate_via_accessibility(clean_t)
        if uia_res and uia_res.get("found"):
            return uia_res

        # Tier 2: Universal multimodal vision lookup
        gemini_res = self._locate_via_gemini(clean_t)
        if gemini_res:
            return gemini_res

        return {
            "found": False,
            "target": clean_t,
            "description": f"Nisam uspeo da lociram '{clean_t}' na trenutnom ekranu."
        }

    # ── Visual Pointer Overlay ───────────────────────────────────────────
    def show_pointer(
        self,
        x: int,
        y: int,
        label: str = "SETTINGS",
        bbox: Optional[List[int]] = None,
        duration: float = 5.0
    ):
        """Displays the animated bouncing arrow and HUD overlay on screen."""
        if self._pointer_callback:
            try:
                self._pointer_callback(int(x), int(y), str(label), bbox, float(duration))
                return
            except Exception as e:
                print(f"[ScreenShare] Pointer callback error: {e}")

        # Fallback: directly instantiate overlay on QApplication
        try:
            from PyQt6.QtWidgets import QApplication
            from .screen_overlay import AnimatedScreenArrowOverlay

            app = QApplication.instance()
            if app:
                overlay = AnimatedScreenArrowOverlay(
                    target_x=x, target_y=y, label=label,
                    bounding_box=bbox, duration=duration
                )
                overlay.show()
        except Exception as e:
            print(f"[ScreenShare] Failed to show overlay: {e}")

    def point_to(self, target: str, duration: float = 5.0) -> Dict[str, Any]:
        """
        Full workflow for "gde je ovde settings / subscribe":
        1. Automatically activates screen sharing if not already active.
        2. Locates the target element on screen.
        3. Spawns the animated arrow pointing directly at the button.
        4. Returns natural language explanation for Jarvis to speak.
        """
        if not self._active:
            self.set_active(True)

        res = self.locate_element(target)
        if res.get("found"):
            x = res["x"]
            y = res["y"]
            bbox = res.get("bbox")
            desc = res.get("description", f"{target.capitalize()} je označen na ekranu.")
            self.show_pointer(x, y, label=target, bbox=bbox, duration=duration)
            return {
                "success": True,
                "found": True,
                "target": target,
                "x": x,
                "y": y,
                "bbox": bbox,
                "description": desc,
                "message": f"Evo, {desc} Označio sam ga animiranom strelicom na ekranu."
            }
        else:
            desc = res.get("description") or f"Nisam uspeo da pronađem '{target}' na trenutnom ekranu."
            return {
                "success": False,
                "found": False,
                "target": target,
                "description": desc,
                "message": desc
            }

    # ── Screen Control & Automation ──────────────────────────────────────
    def move_mouse(self, x: int, y: int, duration: float = 0.25) -> Dict[str, Any]:
        if not pyautogui:
            return {"success": False, "error": "PyAutoGUI nije instaliran."}
        pyautogui.moveTo(x, y, duration=duration)
        return {"success": True, "x": x, "y": y}

    def click(
        self,
        target: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        clicks: int = 1
    ) -> Dict[str, Any]:
        """Clicks at coordinates or finds the named target and clicks it."""
        if not pyautogui:
            return {"success": False, "error": "PyAutoGUI nije instaliran."}

        # If target name provided without coordinates, locate it first
        if target and (x is None or y is None):
            loc = self.locate_element(target)
            if not loc.get("found"):
                return {
                    "success": False,
                    "error": f"Nisam mogao da pronađem '{target}' na ekranu za klik."
                }
            x = loc["x"]
            y = loc["y"]
            # Show a brief pointer highlight right before click
            self.show_pointer(x, y, label=target, bbox=loc.get("bbox"), duration=1.8)

        if x is not None and y is not None:
            pyautogui.moveTo(x, y, duration=0.2)
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            return {
                "success": True,
                "clicked": True,
                "target": target,
                "x": x,
                "y": y,
                "button": button,
                "clicks": clicks,
                "message": f"Kliknuo sam na {target or f'({x}, {y})'}."
            }

        # Click at current position
        pyautogui.click(button=button, clicks=clicks)
        return {"success": True, "clicked": True, "button": button, "clicks": clicks}

    def double_click(self, target: Optional[str] = None, x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        return self.click(target=target, x=x, y=y, button="left", clicks=2)

    def right_click(self, target: Optional[str] = None, x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        return self.click(target=target, x=x, y=y, button="right", clicks=1)

    def type_text(self, text: str, target: Optional[str] = None) -> Dict[str, Any]:
        if target:
            self.click(target=target)
            time.sleep(0.15)
        try:
            from pywinauto.keyboard import send_keys
            escaped = "".join(
                {"{": "{{}", "}": "{}}", "+": "{+}", "^": "{^}", "%": "{%}", "~": "{~}", "(": "{(}", ")": "{)}"}.get(c, c)
                for c in str(text)
            )
            send_keys(escaped, with_spaces=True, with_tabs=True, with_newlines=True, vk_packet=True)
            return {"success": True, "typed": text}
        except Exception:
            if pyautogui:
                pyautogui.write(text, interval=0.01)
                return {"success": True, "typed": text}
            return {"success": False, "error": "Ne mogu da otkucam tekst."}

    def press_key(self, key: str) -> Dict[str, Any]:
        if not pyautogui:
            return {"success": False, "error": "PyAutoGUI nije instaliran."}
        k = key.lower().strip()
        aliases = {
            "enter": "enter", "return": "enter", "esc": "escape",
            "space": "space", "tab": "tab", "backspace": "backspace",
            "win": "win", "windows": "win", "ctrl": "ctrl", "alt": "alt",
            "up": "up", "down": "down", "left": "left", "right": "right"
        }
        resolved = aliases.get(k, k)
        if "+" in resolved:
            parts = [aliases.get(p.strip(), p.strip()) for p in resolved.split("+")]
            pyautogui.hotkey(*parts)
            return {"success": True, "hotkey": resolved}
        else:
            pyautogui.press(resolved)
            return {"success": True, "key": resolved}

    def scroll(self, direction: str = "down", amount: int = 5) -> Dict[str, Any]:
        if not pyautogui:
            return {"success": False, "error": "PyAutoGUI nije instaliran."}
        clicks = -amount * 120 if direction.lower() in ("down", "dole") else amount * 120
        pyautogui.scroll(clicks)
        return {"success": True, "direction": direction, "amount": amount}

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> Dict[str, Any]:
        if not pyautogui:
            return {"success": False, "error": "PyAutoGUI nije instaliran."}
        pyautogui.moveTo(x1, y1)
        pyautogui.dragTo(x2, y2, duration=0.4, button="left")
        return {"success": True, "from": [x1, y1], "to": [x2, y2]}


# Singleton instance
screen_share_manager = ScreenShareManager()
