from pathlib import Path
import sys
import os
import json
import time

def _ensure_movie_path():
    movie_path = str(Path(__file__).resolve().parents[1] / ".agents" / "plugins" / "movie_downloader")
    if movie_path not in sys.path:
        sys.path.insert(0, movie_path)

PLUGIN = {
    "name": "movie_downloader",
    "description": "Obavezno prvo koristi 'search' za pretragu filma, pa kad korisnik potvrdi koristi 'download'. Ako korisnik zeli direktno skidanje bez pitanja, mozes pozvati 'download' sa imenom filma.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "'search' ili 'download'"},
            "query_or_magnet": {"type": "STRING", "description": "Ime filma"}
        },
        "required": ["action", "query_or_magnet"],
    },
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = parameters.get("action", "")
    val = parameters.get("query_or_magnet", "")
    _ensure_movie_path()
    try:
        import movie_downloader as md
        if action == "search":
            if player: player.write_log(f"MOVIE DOWNLOADER: Pretrazujem film '{val}'...")
            results = md.search_torrentgalaxy(val)
            if not results: return f"Nisam nasao nijedan torrent za '{val}'."
            with open("last_movie_results.json", "w") as f: json.dump(results, f)
            best = results[0]
            details = f"Naziv: {best['title']}\nSeeders: {best['seeders']}\nVelicina: {best['size']}"
            md.send_telegram_confirmation(val, details)
            return f"Film '{best['title']}' pronadjen. Poslata potvrda na Telegram. Kada korisnik potvrdi, pozovi akciju 'download'."
            
        elif action == "download":
            if player: player.write_log("MOVIE DOWNLOADER: Pripremam skidanje...")
            
            # Ako je prosledjeno ime filma (nije start i nije magnet), a to nije isti onaj iz fajla, pretrazi prvo
            try:
                with open("last_movie_results.json", "r") as f: results = json.load(f)
            except:
                results = []
                
            need_search = False
            if val and val != "start" and not val.startswith("magnet:"):
                if not results or val.lower().replace(" ", "") not in results[0].get("title", "").lower().replace(" ", "").replace(".", ""):
                    need_search = True
                    
            if need_search or not results:
                if player: player.write_log(f"Pretrazujem prvo: '{val}'...")
                results = md.search_torrentgalaxy(val)
                if not results: return f"Nisam nasao nijedan torrent za '{val}'."
                with open("last_movie_results.json", "w") as f: json.dump(results, f)
                best = results[0]
                details = f"Naziv: {best['title']}\nSeeders: {best['seeders']}\nVelicina: {best['size']}"
                md.send_telegram_confirmation(val, details + "\n\nWatchdog preuzima kontrolu!")
                
            import os, sys, subprocess
            watchdog_path = os.path.join(str(Path(__file__).resolve().parents[1] / ".agents" / "plugins" / "movie_downloader"), "movie_watchdog.py")
            CREATE_NO_WINDOW = 0x08000000
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            f_log = open("watchdog.log", "w", encoding="utf-8")
            subprocess.Popen(["python", watchdog_path], creationflags=CREATE_NO_WINDOW, cwd=str(Path(__file__).resolve().parents[1]), env=env, stdout=f_log, stderr=f_log)
            
            return "Nemilosrdni Watchdog agent je pusten s lanca u pozadini! Prati tvoj Telegram za live izvestaje dok Ultron moze da radi nesto drugo!"
        return f"Nepoznata akcija: {action}"
    except Exception as e:
        return f"Greska: {e}"
