import json
from pathlib import Path

class AudioState:
    def __init__(self):
        self.state_file = Path(__file__).resolve().parents[1] / "audio_state.json"
        self.previous_id = self._load()

    def _load(self):
        if self.state_file.exists():
            try: return json.loads(self.state_file.read_text("utf-8")).get("previous")
            except: return None
        return None

    def get_previous(self):
        return self.previous_id

    def set_previous(self, dev_id: str):
        self.previous_id = dev_id
        self.state_file.write_text(json.dumps({"previous": dev_id}), "utf-8")