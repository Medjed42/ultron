import re
with open('.agents/plugins/movie_downloader/movie_downloader.py', 'r', encoding='utf-8') as f: content = f.read()
content = re.sub(r'                if href.startswith\("magnet:"\):\n                    magnet_link = href\n                elif ".torrent" in href:\n                    torrent_link = href', '''                if href.startswith("magnet:") and not magnet_link:\n                    magnet_link = href\n                elif ".torrent" in href and not torrent_link:\n                    torrent_link = href\n                if magnet_link and torrent_link:\n                    break''', content)
with open('.agents/plugins/movie_downloader/movie_downloader.py', 'w', encoding='utf-8') as f: f.write(content)
