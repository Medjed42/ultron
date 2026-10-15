import requests
from config import config

def redact_secrets(text: str) -> str:
    """Redacts any sensitive tokens/keys from the logs."""
    secrets = config.get_secrets()
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    return text

def send_to_discord(message: str, agent: str = "car", level: str = "INFO"):
    """Sends a structured message to the specific agent's Discord webhook."""
    webhook_url = getattr(config, f"WEBHOOK_{agent.upper()}", config.WEBHOOK_CAR)
    
    redacted_message = redact_secrets(message)
    
    # Optional structured format
    if level != "INFO":
        redacted_message = f"**[{level}]** {redacted_message}"
        
    if not webhook_url:
        print(f"[{agent.upper()}] {redacted_message}")
        return False
    
    payload = {"content": redacted_message}
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Failed to send to Discord: {redact_secrets(str(e))}")
        return False
