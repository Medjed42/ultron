from openai import OpenAI
import time
from config import config

class ChatGPTAgent:
    def __init__(self):
        self.api_key = config.OPENAI_API_KEY
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
            self.model_waterfall = ['gpt-4o-mini', 'gpt-4o']
        else:
            self.client = None

    def execute_task(self, prompt: str, context: list, require_json: bool = False, max_retries: int = 2) -> dict:
        if not self.client:
            return {"status": "error", "message": "OpenAI API key ne postoji u .env fajlu!"}
            
        full_prompt = "\n".join(context) + "\n\n" + prompt
        
        kwargs = {}
        if require_json:
            kwargs["response_format"] = { "type": "json_object" }

        for model_name in self.model_waterfall:
            for attempt in range(max_retries):
                try:
                    response = self.client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": "You are a precise B2B software engineering and research assistant."},
                            {"role": "user", "content": full_prompt}
                        ],
                        **kwargs
                    )
                    return {"status": "success", "result": response.choices[0].message.content}
                except Exception as e:
                    error_str = str(e).lower()
                    
                    if "insufficient_quota" in error_str or "billing" in error_str:
                        return {"status": "error", "message": "Nemaš kredita na OpenAI nalogu!"}
                        
                    if "429" in error_str or "rate limit" in error_str:
                        if attempt < max_retries - 1:
                            time.sleep((2 ** attempt) * 2)
                            continue
                            
                    return {"status": "error", "message": str(e)}
                    
        return {"status": "error", "message": "OpenAI API failed."}
