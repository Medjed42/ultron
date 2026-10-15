"""Retired compatibility API. Inactivity never initiates speech."""
class ProactiveEngine:
    def __init__(self, *args, **kwargs): pass
    def should_trigger(self, last_user_speech): return False
    def mark_triggered(self): pass
    def build_prompt(self, *args, **kwargs): return ""
