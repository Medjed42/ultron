"""Secret-safe diagnostics. Never changes credentials or provider configuration."""
import logging
import os
import re
import threading

_PATTERNS = [
    r'https://(?:discord(?:app)?\.com)/api/webhooks/[^\s"\']+',
    r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b',
    r'\bAIza[A-Za-z0-9_-]{25,}\b',
    r'\b(?:sk-|gsk_)[A-Za-z0-9_-]{20,}\b',
    r'(?i)Bearer\s+[A-Za-z0-9_.-]+',
    r'(?i)(?:api[_-]?key|password|access_token|refresh_token|secret)\s*[=:]\s*["\']?[^\s,"\'}]+',
]
_secrets = set()
_secrets_lock = threading.RLock()


def register_secrets(value, key=""):
    if isinstance(value, dict):
        for k, v in value.items():
            register_secrets(v, str(k))
    elif isinstance(value, str) and len(value) >= 8 and any(
        word in key.lower() for word in ("key", "token", "secret", "password", "webhook")
    ):
        with _secrets_lock:
            _secrets.add(value)


def redact(value):
    text = str(value)
    with _secrets_lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    for pattern in _PATTERNS:
        text = re.sub(pattern, "[REDACTED]", text)
    return text


class RedactingStream:
    def __init__(self, stream):
        self.stream = stream
    def write(self, text):
        self.stream.write(redact(text))
        return len(text)
    def flush(self):
        self.stream.flush()
    def __getattr__(self, name):
        return getattr(self.stream, name)


def install_redaction():
    import sys
    from memory.config_manager import load_api_keys
    register_secrets(dict(os.environ))
    register_secrets(load_api_keys())
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is not None and not isinstance(stream, RedactingStream):
            setattr(sys, name, RedactingStream(stream))
