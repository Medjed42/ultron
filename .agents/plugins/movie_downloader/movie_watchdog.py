import sys
import time
import requests
import json
import os
import movie_downloader as md

def run_watchdog():
    pass
    try:
        with open("last_movie_results.json", "r") as f:
            results = json.load(f)
    except:
        pass
        return

    if not results:
        return
        
    session = md.get_qb_session() or requests.Session()
    url_add = "http://localhost:8080/api/v2/torrents/add"
    url_info = "http://localhost:8080/api/v2/torrents/info"
    url_del = "http://localhost:8080/api/v2/torrents/delete"

    for i, res in enumerate(results):
        magnet = res.get("magnet", "")
        torrent_link = res.get("torrent", "")
        title = res.get("title", "Nepoznato")
        
        md.send_telegram_confirmation(title, f"Watchdog pokusava da skine: {title}\n(Pokusaj {i+1} od {len(results)})")
        
        # Prvo probamo magnet
        if magnet:
            session.post(url_add, data={"urls": magnet}, timeout=5)
        elif torrent_link:
            try:
                t_res = requests.get(torrent_link, timeout=10)
                if t_res.ok:
                    session.post(url_add, files={"torrents": ("movie.torrent", t_res.content)}, timeout=5)
            except:
                pass
                
        # Sada pratimo ovaj torrent 
        stall_count = 0
        target_hash = None
        is_dead = False
        
        while True:
            time.sleep(10)
            try:
                info_res = session.get(url_info, timeout=5)
                if not info_res.ok: continue
                torrents = info_res.json()
                
                found = False
                for t in torrents:
                    if title in t.get("name", "") or t.get("name", "") in title:
                        found = True
                        target_hash = t.get("hash")
                        state = t.get("state")
                        progress = t.get("progress", 0)
                        speed = t.get("dlspeed", 0)
                        
                        if progress == 1:
                            md.send_telegram_confirmation(title, f"USPESNO!\nFilm '{title}' je 100% skinut!")
                            return # Gotovo
                            
                        # Provera meta podataka i brzine
                        if state in ["metaDL"]:
                            stall_count += 1 # Ako stoji na metadata 100s, ubi ga
                            if stall_count > 6:
                                is_dead = True
                        elif state in ["stalledDL", "downloading"]:
                            if speed == 0:
                                stall_count += 1
                                if stall_count > 12: # 2 minuta bez brzine
                                    is_dead = True
                            else:
                                stall_count = 0 # reset
                        break
                
                if not found and target_hash:
                    # Korisnik ga mozda obrisao rucno, prekidamo
                    return
                    
                if is_dead:
                    # Ubi trenutni i prelazi na sledeci
                    if target_hash:
                        session.post(url_del, data={"hashes": target_hash, "deleteFiles": True})
                    md.send_telegram_confirmation(title, f"Torent '{title}' je crkao/zaboo. Brisem ga i prelazim na sledeci!")
                    break # break inner loop, continue outer loop
                    
            except Exception as e:
                pass
                pass

    md.send_telegram_confirmation("Zao mi je", "Svi ponudjeni torenti su mrtvi. Watchdog agent se gasi.")

if __name__ == '__main__':
    run_watchdog()
