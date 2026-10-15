import re

with open("ULTRON_SETUP_ALL_ACCOUNTS.bat", "r", encoding="utf-8-sig") as f:
    text = f.read()

# 1. Update builder list
text = text.replace(
    "for builder in (self.overview, self.google_page, self.maps_page, self.apple_page, self.amadeus_page, self.finish_page):",
    "for builder in (self.overview, self.gemini_page, self.google_page, self.maps_page, self.apple_page, self.amadeus_page, self.finish_page):"
)

# 2. Update step titles
text = text.replace(
    'names = ["Before you start", "Google: Gmail and Drive", "Google Maps and property discovery",',
    'names = ["Before you start", "Gemini AI", "Google: Gmail and Drive", "Google Maps and property discovery",'
)

# 3. Add gemini_page method before overview
gemini_method = """
    def gemini_page(self, layout):
        layout.addWidget(label("Gemini AI Account", "title"))
        layout.addWidget(label("Connect Gemini AI via API Key. Ultron requires this for its core intelligence."))
        self.link(layout, "Get Gemini API Key", "https://aistudio.google.com/app/apikey")
        form = QFormLayout()
        self.gemini_key = field("Gemini API Key; blank keeps saved", True)
        form.addRow("Gemini API Key", self.gemini_key)
        layout.addLayout(form)
        self.action(layout, "Save Gemini Connection", self.save_gemini, True)
        self.gemini_status = label("Gemini configuration controls the main brain of Ultron.")
        layout.addWidget(self.gemini_status)

    def save_gemini(self):
        key = self.gemini_key.text().strip()
        if key:
            from memory.config_manager import save_api_keys
            save_api_keys(key)
            self.set_status("Gemini AI", "Verified and saved Gemini API key.")
            self.gemini_status.setText("Gemini API key saved.")
            self.gemini_key.clear()
        else:
            self.gemini_status.setText("No key entered. Existing configuration kept.")
            self.set_status("Gemini AI", "Existing configuration kept.")

    def overview(self, layout):"""

text = text.replace("    def overview(self, layout):", gemini_method)

with open("ULTRON_SETUP_ALL_ACCOUNTS.bat", "w", encoding="utf-8-sig") as f:
    f.write(text)
