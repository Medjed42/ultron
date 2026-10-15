from mcp.server.mcpserver import MCPServer
import sys
import os

# Dodajemo direktorijum u putanju da bi mogli da importujemo movie_downloader.py
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

import movie_downloader

mcp = MCPServer("movie-downloader-mcp")

@mcp.tool()
def search_movie(query: str) -> str:
    """
    Pretražuje TorrentGalaxy za dati film i vraća najbolje rezultate.
    Ovo je prvi korak kada korisnik traži film.
    """
    try:
        results = movie_downloader.search_torrentgalaxy(query)
        if not results:
            return "Nema rezultata za pretragu na TorrentGalaxy."
            
        output = "Pronađeni torrenti:\n\n"
        for i, r in enumerate(results):
            output += f"{i+1}. Naslov: {r['title']}\n   Seeders: {r['seeders']} | Size: {r['size']}\n   Magnet: {r['magnet']}\n\n"
        return output
    except Exception as e:
        return f"Greška prilikom pretrage: {str(e)}"

@mcp.tool()
def send_telegram_confirmation(movie_title: str, details: str, image_url: str = "") -> str:
    """
    Šalje poruku na Telegram korisniku da bi potvrdio da li je to pravi film pre skidanja.
    """
    try:
        success = movie_downloader.send_telegram_confirmation(movie_title, details, image_url)
        if success:
            return "Poruka za potvrdu uspešno poslata na Telegram. Čekaj korisnikov odgovor."
        else:
            return "Neuspešno slanje poruke na Telegram. Proverite tokene."
    except Exception as e:
        return f"Greška prilikom slanja na Telegram: {str(e)}"

@mcp.tool()
def download_movie_magnet(magnet_link: str) -> str:
    """
    Dodaje magnet link u qBittorrent i proverava da li je uspešno počeo da preuzima meta podatke.
    Pozovi ovo NAKON što korisnik potvrdi skidanje.
    """
    try:
        success = movie_downloader.add_to_qbittorrent(magnet_link)
        if success:
            return "Magnet link uspešno dodat u qBittorrent i meta podaci se preuzimaju!"
        else:
            return "Nisam uspeo da dodam torrent ili nema seedera (metadata failed)."
    except Exception as e:
        return f"Greška prilikom dodavanja u qBittorrent: {str(e)}"

if __name__ == "__main__":
    mcp.run()
