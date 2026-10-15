"""Shared encrypted Google OAuth; no automatic login from a tool."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.append(str(ROOT))
from core.integrations.google_auth import credentials
def get_google_credentials():
    return credentials("gmail")
