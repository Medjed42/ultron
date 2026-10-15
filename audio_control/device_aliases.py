import json
from pathlib import Path

class DeviceAliases:
    def __init__(self):
        self.config_file = Path(__file__).resolve().parents[1] / "audio_aliases.json"
        self.aliases = self._load()

    def _load(self):
        if self.config_file.exists():
            try: return json.loads(self.config_file.read_text("utf-8"))
            except: return {}
        return {}

    def _save(self):
        self.config_file.write_text(json.dumps(self.aliases, indent=2), "utf-8")

    def get_target(self, alias: str) -> str:
        return self.aliases.get(alias.lower().strip())

    def set_alias(self, alias: str, target: str):
        self.aliases[alias.lower().strip()] = target
        self._save()