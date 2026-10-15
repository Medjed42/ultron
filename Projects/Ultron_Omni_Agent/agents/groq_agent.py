from openai import OpenAI
import time
from config import config

class GroqAgent:
    def __init__(self):
        self.api_key = config.GROQ_API_KEY
        if self.api_key:
            # Groq uses standard OpenAI client syntax, just with a custom base URL
            self.client = OpenAI(
                api_key=self.api_key,
                base_url="https://api.groq.com/openai/v1"
            )
            # This Groq account has access to powerful enterprise/oss models
            self.model_waterfall = ['openai/gpt-oss-120b', 'qwen/qwen3.8-27b', 'allam-2-7b']
        else:
            self.client = None

    def execute_task(self, prompt: str, context: list, require_json: bool = False, max_retries: int = 3) -> dict:
        if not self.client:
            return {"status": "error", "message": "Nisi uneo GROQ_API_KEY u .env fajl!"}
            
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
                    
                    if "429" in error_str or "rate limit" in error_str:
                        # Groq has strict rate limits on the free tier, especially per minute/day
                        if "limit: 14400" in error_str or "daily" in error_str:
                            print(f"[{model_name}] Dnevni limit na Groq-u ispunjen. Menjam model...")
                            break
                            
                        if attempt < max_retries - 1:
                            time.sleep((2 ** attempt) * 2)
                            continue
                            
                    return {"status": "error", "message": str(e)}
                    
        return {"status": "error", "message": "Svi Groq modeli i pokušaji su propali."}
