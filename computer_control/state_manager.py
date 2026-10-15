class StateManager:
    def __init__(self):
        self.active_app = None
        self.app_history = []
        self.recent_actions = []

    def set_active_app(self, app_name: str):
        if self.active_app and self.active_app != app_name:
            if self.active_app in self.app_history:
                self.app_history.remove(self.active_app)
            self.app_history.append(self.active_app)
        self.active_app = app_name

    def record_action(self, action: str, target: str, value: str):
        self.recent_actions.append({"action": action, "target": target, "value": value})
        if len(self.recent_actions) > 10:
            self.recent_actions.pop(0)