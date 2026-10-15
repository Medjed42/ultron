from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")
import sys
import requests
from bs4 import BeautifulSoup
import subprocess
import time
import urllib.parse
import os
import json

# Konstante za Telegram bota (treba da se popune od strane korisnika ili kroz env)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1814113436")

def search_torrentgalaxy(query):
    print(f"Pretrazujem TorrentGalaxy za: {query}")
    url = f"https://torrentgalaxy.one/get-posts/?keywords={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Greska prilikom pretrage: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    results = []
    
    # Prilagodi selektore zavisno od strukture sajta
    rows = soup.find_all("div", class_="tgxtablerow")
    for row in rows:
        # Naslov: a tag sa href="/torrent/..." ili "/post-detail/..."
        title = ""
        detail_url = ""
        for a in row.find_all("a"):
            href = a.get("href", "")
            if href.startswith("/torrent/") or href.startswith("/post-detail/"):
                title = a.get("title") or a.get_text(strip=True)
                detail_url = "https://torrentgalaxy.one" + href
                if title:
                    break
        
        if not title or not detail_url:
            continue
            
        # Ekstraktuj magnet (nalazi se na detail stranici, TorrentGalaxy ga je sakrio sa searcha)
        magnet_link = ""
        torrent_link = ""
        try:
            detail_soup = BeautifulSoup(requests.get(detail_url, headers=headers, timeout=10).text, 'html.parser')
            for a in detail_soup.find_all("a"):
                href = a.get("href", "")
                if href.startswith("magnet:") and not magnet_link:
                    magnet_link = href
                elif ".torrent" in href and not torrent_link:
                    torrent_link = href
                if magnet_link and torrent_link:
                    break
        except Exception as e:
            print(f"Greska pri ucitavanju detalja: {e}")
            pass
                
        # Seeders
        seeders_font = row.find("font", color="green")
        seeders_span = row.find("span", title="Seeders")
        seeders = 0
        if seeders_font and seeders_font.get_text(strip=True).isdigit():
            seeders = int(seeders_font.get_text(strip=True))
        elif seeders_span and seeders_span.get_text(strip=True).isdigit():
            seeders = int(seeders_span.get_text(strip=True))
        else:
            # Pokusaj bilo koji font ili tekst koji lici na broj (grubo)
            for f in row.find_all(["font", "span"]):
                if f.get("color") == "green" or f.get("title") == "Seeders":
                    try:
                        seeders = int(f.get_text(strip=True).split('/')[0].replace('[','').strip())
                    except:
                        pass

        # Size
        size = "N/A"
        size_span = row.find("span", class_="badge badge-secondary txt-black")
        if size_span:
            size = size_span.get_text(strip=True)
        else:
            for s in row.find_all("span"):
                if "GB" in s.text or "MB" in s.text:
                    size = s.text.strip()
                    break
        
        if magnet_link and seeders > 0:
            results.append({
                "title": title,
                "magnet": magnet_link,
                "torrent": torrent_link,
                "seeders": seeders,
                "size": size
            })
            if len(results) >= 5:
                break
            
    return results

def get_movie_poster(title):
    import json
    import re
    try:
        url = f"https://sg.media-imdb.com/suggests/{title[0].lower()}/{urllib.parse.quote(title.lower())}.json"
        res = requests.get(url, timeout=5).text
        # response is JSONP like: imdb$holidate({ ... })
        match = re.search(r'^\w+\$[^(]+\((.*)\)$', res)
        if match:
            data = json.loads(match.group(1))
            if "d" in data and len(data["d"]) > 0:
                for item in data["d"]:
                    if "i" in item and len(item["i"]) > 0:
                        return item["i"][0]
    except Exception as e:
        print(f"Greska pri trazenju postera: {e}")
    return None

def send_telegram_confirmation(movie_title, details, image_url=None):
    pass
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or TELEGRAM_CHAT_ID == "YOUR_CHAT_ID_HERE":
        print("Telegram tokeni nisu podeĹˇeni. PreskaÄŤem slanje.")
        return False
        
    caption = f"đźŽ¬ *TraĹľili ste film:* {movie_title}\n\n*Detalji najboljeg torenta:*\n{details}\n\nDa li da zapoÄŤnem skidanje?"
    
    if not image_url:
        image_url = get_movie_poster(movie_title)
        
    if not image_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": caption,
            "parse_mode": "Markdown"
        }
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "Markdown"
        }
    
    try:
        response = requests.post(url, data=data)
        return response.ok
    except Exception as e:
        print(f"Greska prilikom slanja na Telegram: {e}")
        return False

def get_qb_session():
    import requests
    import time
    import subprocess
    session = requests.Session()
    login_url = "http://localhost:8080/api/v2/auth/login"
    for attempt in range(2):
        try:
            res = session.post(login_url, data={"username": "admin", "password": "123456"}, timeout=5)
            if res.ok:
                return session
        except:
            if attempt == 0:
                print("qBittorrent nije pokrenut. Pokrecem ga...")
                try:
                    import os
                    os.startfile(r"C:\Program Files\qBittorrent\qbittorrent.exe")
                    time.sleep(10)
                except Exception as e:
                    print(f"Greska pri paljenju: {e}")
    return None

def check_qbittorrent_metadata(magnet_hash, max_retries=10):
    print("Proveravam da li torrent dobija meta podatke...")
    url_info = f"http://localhost:8080/api/v2/torrents/info?hashes={magnet_hash}"
    url_del = "http://localhost:8080/api/v2/torrents/delete"
    import time
    import requests
    session = get_qb_session() or requests.Session()
    for i in range(max_retries):
        try:
            response = session.get(url_info, timeout=5)
            if response.ok:
                torrents = response.json()
                if torrents and len(torrents) > 0:
                    status = torrents[0].get("state")
                    if status not in ["metaDL", "allocating", "checkingResumeData"]:
                        print("Meta podaci uspesno preuzeti. Skidanje pocinje!")
                        return True
        except requests.exceptions.RequestException:
            pass
        print(f"Cekam meta podatke... ({i+1}/{max_retries})")
        time.sleep(2)
    print("Torrent nije uspeo da preuzme meta podatke u 20 sekundi. Brisem ga iz liste!")
    try:
        session.post(url_del, data={"hashes": magnet_hash, "deleteFiles": True}, timeout=5)
    except:
        pass
    return False

def add_to_qbittorrent(magnet_link):
    import re
    import requests
    match = re.search(r'xt=urn:btih:([a-zA-Z0-9]+)', magnet_link)
    magnet_hash = match.group(1).lower() if match else None
    session = get_qb_session() or requests.Session()
    url_add = "http://localhost:8080/api/v2/torrents/add"
    try:
        res = session.post(url_add, data={"urls": magnet_link}, timeout=5)
        if not res.ok:
            print("Web API nije dostupan ili je vratio gresku.")
            return False
    except requests.exceptions.RequestException:
        print("Greska: Nema konekcije sa qBittorrent Web UI. Proveri da li je ukljucen na portu 8080.")
        return False
    if not magnet_hash:
        return True
    return check_qbittorrent_metadata(magnet_hash)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Upotreba: python movie_downloader.py <ime filma>')
        sys.exit(1)
        
    query = sys.argv[1]
    print(f'Zapocinjem Movie Downloader za: {query}')
    results = search_torrentgalaxy(query)
    
    if not results:
        print('Nema rezultata ili sajt nije dostupan.')
        sys.exit(1)
        
    best_torrent = results[0]
    print(f'\nNajbolji rezultat: {best_torrent["title"]}')
    print(f'Magnet: {best_torrent["magnet"]}')
