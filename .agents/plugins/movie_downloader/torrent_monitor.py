import sys
import time
import requests
import json
import os

def get_qb_session():
    session = requests.Session()
    login_url = "http://localhost:8080/api/v2/auth/login"
    try:
        res = session.post(login_url, data={"username": "admin", "password": "123456"}, timeout=5)
        if res.ok: return session
    except: pass
    return None

def monitor(title):
    session = get_qb_session()
    if not session: return

    url_info = "http://localhost:8080/api/v2/torrents/info"
    url_del = "http://localhost:8080/api/v2/torrents/delete"
    
    stall_count = 0
    target_hash = None
    
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
                    speed = t.get("dlspeed", 0)
                    progress = t.get("progress", 0)
                    state = t.get("state")
                    
                    if progress == 1:
                        # Gotovo!
                        # Ovde mozemo poslati na telegram
                        return
                        
                    if state == "downloading" and speed > 0:
                        stall_count = 0
                    elif state in ["stalledDL", "downloading", "metaDL"]:
                        if speed == 0:
                            stall_count += 1
                        else:
                            stall_count = 0
                            
                    break
            
            if not found:
                # Obrisao ga korisnik
                return
                
            if stall_count >= 12: # 12 * 10s = 2 minuta na 0 kbps
                # Ubi trenutni i prebaci na sledeci
                session.post(url_del, data={"hashes": target_hash, "deleteFiles": True})
                
                # Ucitaj sledeci iz last_movie_results i aktiviraj (Ovo cemo implementirati)
                # Za sad samo gasimo i skripta puca
                return
                
        except Exception as e:
            pass

if __name__ == '__main__':
    if len(sys.argv) > 1:
        monitor(sys.argv[1])
