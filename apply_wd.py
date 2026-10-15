from pathlib import Path
import os
os.chdir(Path(__file__).resolve().parent)

import sys
import os
import json

with open('./plugins/movie_downloader_plugin.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_start = content.find('            if need_search or not results:')
old_end = content.find('        return f"Nepoznata akcija: {action}"')

if old_start != -1 and old_end != -1:
    old_chunk = content[old_start:old_end]
    new_chunk = """            if need_search or not results:
                if player: player.write_log(f"Pretrazujem prvo: '{val}'...")
                results = md.search_torrentgalaxy(val)
                if not results: return f"Nisam nasao nijedan torrent za '{val}'."
                with open("last_movie_results.json", "w") as f: json.dump(results, f)
                best = results[0]
                details = f"Naziv: {best['title']}\\nSeeders: {best['seeders']}\\nVelicina: {best['size']}"
                md.send_telegram_confirmation(val, details + "\\n\\nWatchdog preuzima kontrolu!")
                
            # Pokreni Watchdog u pozadini
            import subprocess
            watchdog_path = os.path.join(r".\\.agents\\plugins\\movie_downloader", "movie_watchdog.py")
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_CONSOLE = 0x00000010
            subprocess.Popen([sys.executable, watchdog_path], creationflags=DETACHED_PROCESS | CREATE_NEW_CONSOLE, cwd=r".")
            
            return "Nemilosrdni Watchdog agent je pusten s lanca u pozadini! Prati tvoj Telegram za live izvestaje dok Ultron moze da radi nesto drugo!"
"""
    content = content.replace(old_chunk, new_chunk)
    with open('./plugins/movie_downloader_plugin.py', 'w', encoding='utf-8') as f:
        f.write(content)
