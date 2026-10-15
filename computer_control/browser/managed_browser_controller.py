"""Explicit opt-in adapter to the existing Playwright implementation."""
class ManagedBrowserController:
    def route(self, parameters):
        from actions.browser_control import browser_control
        return browser_control(dict(parameters, mode="managed"))
