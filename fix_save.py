import re

with open('ui.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Fix _save
old_save = r'''prov = {"Gemini": "gemini", "ChatGPT": "chatgpt", "Claude": 
"claude"}.get(self._provider_combo.currentText(), "gemini")
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice, prov, 
self._openai_input.text().strip(), self._anthropic_input.text().strip())'''
new_save = r'''self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)'''
text = text.replace(old_save, new_save)
text = re.sub(
    r'prov = \{"Gemini": "gemini", "ChatGPT": "chatgpt", "Claude": "claude"\}.get\(self\._provider_combo\.currentText\(\), "gemini"\)\s*self\.saved\.emit\(name, user, self\._sel_color or DEFAULT_UI_COLOR, self\._sel_voice, prov, self\._openai_input\.text\(\)\.strip\(\), self\._anthropic_input\.text\(\)\.strip\(\)\)',
    new_save,
    text
)

# Fix _apply_name_update
text = re.sub(
    r'def _apply_name_update\(self, name: str, user_name: str, ui_color: str = "",\s*voice: str = "", ai_prov: str = "gemini", o_key: str = "", a_key: str = ""\):',
    r'def _apply_name_update(self, name: str, user_name: str, ui_color: str = "", voice: str = ""):',
    text
)
text = re.sub(
    r'def _apply_name_update\(self, name: str, user_name: str, ui_color: str = "", voice: str = "", ai_prov: str = "gemini", o_key: str = "", a_key: str = ""\):',
    r'def _apply_name_update(self, name: str, user_name: str, ui_color: str = "", voice: str = ""):',
    text
)

# Fix save inside _apply_name_update
text = re.sub(
    r'cm\.save_assistant_config\(self\._assistant_name, user_name\)\s*cm\.save_ai_provider\(ai_prov\)\s*cm\.save_openai_key\(o_key\)\s*cm\.save_anthropic_key\(a_key\)',
    r'cm.save_assistant_config(self._assistant_name, user_name)',
    text
)

with open('ui.py', 'w', encoding='utf-8') as f:
    f.write(text)
