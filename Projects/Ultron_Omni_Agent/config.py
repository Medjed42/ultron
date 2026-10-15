import os
from dotenv import load_dotenv

from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parents[1] / ".env")

class Config:
    # API Keys
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
    
    # Discord Webhooks
    WEBHOOK_CAR = os.getenv("WEBHOOK_CAR")
    WEBHOOK_CLAUDE = os.getenv("WEBHOOK_CLAUDE")
    WEBHOOK_GEMINI = os.getenv("WEBHOOK_GEMINI")
    WEBHOOK_CHATGPT = os.getenv("WEBHOOK_CHATGPT")
    WEBHOOK_ANTIGRAVITY = os.getenv("WEBHOOK_ANTIGRAVITY")
    
    # Google OAuth
    GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
    
    # Settings
    SEARCH_CACHE_TTL_HOURS = int(os.getenv("SEARCH_CACHE_TTL_HOURS", "24"))
    MIN_RESEARCH_CONFIDENCE = float(os.getenv("MIN_RESEARCH_CONFIDENCE", "0.7"))
    
    @classmethod
    def get_secrets(cls):
        """Returns a list of secret values for redaction."""
        secrets = []
        for key in dir(cls):
            if not key.startswith('__') and isinstance(getattr(cls, key), str):
                val = getattr(cls, key)
                if val and len(val) > 10 and val not in ["credentials.json", "token.json"]:
                    secrets.append(val)
        return secrets

config = Config()
