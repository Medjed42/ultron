import anthropic
from .base_agent import BaseAgent
from config import config

class ClaudeAgent(BaseAgent):
    def __init__(self):
        super().__init__("Claude")
        if config.ANTHROPIC_API_KEY:
            self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        else:
            self.client = None

    def execute_task(self, task_description: str, tools_available: list) -> dict:
        if not self.client:
            return {"status": "error", "message": "Anthropic API key not configured."}
        
        prompt = f"Task: {task_description}\nAvailable tools: {tools_available}\nExplain how you would solve this."
        try:
            message = self.client.messages.create(
                model="claude-3-5-sonnet-20240620",
                max_tokens=1024,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            return {"status": "success", "result": message.content[0].text}
        except Exception as e:
            return {"status": "error", "message": str(e)}
