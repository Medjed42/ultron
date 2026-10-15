from pathlib import Path
import os
os.chdir(Path(__file__).resolve().parent)

import re
with open('./.agents/plugins/movie_downloader/movie_downloader.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = re.sub(r'print\(f\".*?aljem potvrdu na Telegram za: \{movie_title\}\"\)', 'print(f"Saljem potvrdu na Telegram za: {movie_title}")', c)
with open('./.agents/plugins/movie_downloader/movie_downloader.py', 'w', encoding='utf-8') as f:
    f.write(c)
