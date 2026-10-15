"""Run manually to authorize Gmail through Google's desktop OAuth flow."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from core.integrations.google_auth import connect
if __name__ == "__main__":
    connect("gmail",write=True)
    print("Gmail authorization saved in the Windows encrypted credential store.")
