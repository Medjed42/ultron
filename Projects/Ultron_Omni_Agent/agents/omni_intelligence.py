from agents.gemini_agent import GeminiAgent

class OmniIntelligenceAgent:
    def __init__(self): self.gemini = GeminiAgent()
    def execute_task(self, prompt, context, require_json=False, max_retries=2):
        return self.gemini.execute_task(prompt, context, require_json, max_retries)
