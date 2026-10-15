import json
import os
import time

from pathlib import Path
import threading
_lock = threading.RLock()
DB_FILE = str(Path(__file__).resolve().parent / "db/leads.json")

def get_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_db(data):
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    temporary = DB_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(temporary, DB_FILE)

def update_lead(restaurant_id: str, updates: dict):
    db = get_db()
    if restaurant_id not in db:
        db[restaurant_id] = {
            "restaurant_id": restaurant_id,
            "created_at": time.time(),
            "pipeline_state": "DISCOVERED",
            "outreach_history": []
        }
    
    db[restaurant_id].update(updates)
    db[restaurant_id]["last_updated_at"] = time.time()
    save_db(db)
    return db[restaurant_id]

def get_lead(restaurant_id: str):
    db = get_db()
    return db.get(restaurant_id)

# Serialize full read-modify-write transactions.
_update_lead = update_lead
def update_lead(restaurant_id, updates):
    with _lock:
        return _update_lead(restaurant_id, updates)
