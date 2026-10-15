"""YouTube controls scoped to the existing Chrome window, with state checks."""
import time
from ..browser.existing_browser_controller import ExistingBrowserController

class YouTubeController:
    def __init__(self, browser=None): self.browser = browser or ExistingBrowserController()

    def _button(self, prefixes):
        nodes = self.browser.controls(self.browser.window(focus=True))
        matches = [n for n in nodes if n.element_info.control_type == "Button" and n.is_enabled()
                   and any(n.window_text().casefold().startswith(p) for p in prefixes)]
        return matches[0] if len(matches) == 1 else None

    def handle_action(self, action, target=None, value=None):
        if action == "youtube_search": return self.browser.route(action, target, value)
        before = self.browser.snapshot()
        if "youtube" not in before.get("title", "").lower():
            return {"success":False,"error":"Select the YouTube tab first using get_tabs/switch_tab."}
        pairs = {
            "youtube_pause": (("pause", "pauziraj", "паузирај"), ("play (", "reprodukuj", "пусти")),
            "youtube_play": (("play (", "reprodukuj", "пусти"), ("pause", "pauziraj", "паузирај")),
            "youtube_fullscreen": (("full screen", "ceo ekran", "цео екран"), ("exit full", "izađi iz", "изађи из")),
            "youtube_exit_fullscreen": (("exit full", "izađi iz", "изађи из"), ("full screen", "ceo ekran", "цео екран")),
            "youtube_mute": (("mute (", "isključi zvuk", "искључи звук"), ("unmute", "uključi zvuk", "укључи звук")),
            "youtube_unmute": (("unmute", "uključi zvuk", "укључи звук"), ("mute (", "isključi zvuk", "искључи звук")),
        }
        if action in pairs:
            desired, opposite = pairs[action]
            button = self._button(desired)
            if button is None:
                if self._button(opposite): return {"success":True,"verified":True,"status":"already in requested state"}
                return {"success":False,"verified":False,"error":"Player state unavailable in accessibility tree. Use screen_process; no global toggle sent."}
            button.click_input()
            time.sleep(0.3)
            return {"success":True,"verified":bool(self._button(opposite)),"observation":self.browser.snapshot()}
        if action in {"youtube_quality", "youtube_speed"}:
            settings = self._button(("settings", "podešavanja", "подешавања"))
            if not settings: return {"success":False,"error":"Player settings unavailable; inspect screen."}
            settings.click_input(); time.sleep(0.15)
            label = "Quality" if action == "youtube_quality" else "Playback speed"
            # Names can be localized; return a fresh observation on a mismatch.
            try:
                self.browser.click(target or label); time.sleep(0.15)
                self.browser.click(str(value)); time.sleep(0.15)
            except ValueError:
                return {"success":False,"error":"Use observed localized menu names via browser_click.","observation":self.browser.snapshot()}
            return {"success":True,"verified":False,"status":"option selected; verify player settings", "observation":self.browser.snapshot()}
        if action == "youtube_volume":
            level = float(value)
            if not 0 <= level <= 100:
                return {"success":False,"error":"Volume must be between 0 and 100"}
            sliders = [c for c in self.browser.controls(self.browser.window(focus=True))
                       if c.element_info.control_type == "Slider" and any(
                           label in c.window_text().casefold() for label in ("volume", "jačina", "јачина"))]
            if len(sliders) != 1:
                return {"success":False,"error":"Volume slider unavailable; inspect player controls", "observation":before}
            slider = sliders[0]
            wanted = slider.min_value() + (slider.max_value() - slider.min_value()) * level / 100
            slider.set_value(wanted)
            return {"success":True,"verified":abs(slider.value() - wanted) < 0.01,"requested_percent":level}
        if action == "youtube_select_video":
            if not target:
                return {"success":False,"error":"Observe search results and pass a unique visible video title as target. Numeric link positions include navigation and cannot identify videos reliably.","observation":before}
            self.browser.find(target, {"Hyperlink"}).click_input()
        else:
            keys = {"youtube_toggle":"k","youtube_next":"shift+n","youtube_prev":"alt+left",
                    "youtube_captions":"c","youtube_seek_forward":"l","youtube_seek_back":"j"}
            if action not in keys: return {"success":False,"error":"Unsupported YouTube action"}
            # Move keyboard focus out of search/edit controls before player shortcuts.
            node = self._button(("play (", "pause", "pauziraj", "reprodukuj"))
            if node is None: return {"success":False,"error":"Player focus unavailable; inspect screen first"}
            node.set_focus()
            self.browser.press(keys[action])
        time.sleep(0.3)
        return {"success":True,"verified":False,"status":"dispatched","observation":self.browser.snapshot()}
