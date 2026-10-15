from pathlib import Path
import os
import subprocess
import re

PLUGIN = {
    "name": "antigravity_coder",
    "description": (
        "Use this tool when the user asks to write code, build a project, create a website, "
        "or do any programming task. Only use this for generating code/files."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "request": {"type": "STRING", "description": "The exact coding request from the user."},
        },
        "required": ["request"],
    },
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    request = parameters.get("request", "")
    if not request:
        return "Gospodine, morate mi reći šta tačno želite da isprogramiram."
    
    projects_dir = str(Path(__file__).resolve().parents[1] / "Projects")
    os.makedirs(projects_dir, exist_ok=True)
    
    # Dodajemo instrukciju direktno u komandu
    full_prompt = (
        "Ti si Antigravity Coder koji radi za Ultron asistenta. "
        f"SVE fajlove i projekte obavezno napravi u folderu: {projects_dir} "
        "Kada završiš, napiši izveštaj. U njemu JASNO navedi: \n"
        "1. Šta si tačno napravio\n"
        "2. Tačnu apsolutnu putanju direktorijuma gde se projekat nalazi\n"
        "3. Tačnu komandu kojom korisnik to može da pokrene i testira.\n"
        f"Korisnikov zahtev: {request}"
    )
    
    if player:
        try:
            player.write_log(f"ULTRON: Pokrećem Antigravity za zahtev: '{request}'...")
            player.write_log(f"ULTRON: Molim Vas sačekajte, ovo može potrajati...")
        except Exception:
            pass
            
    try:
        process = subprocess.Popen(
            ["agy", "--print", full_prompt, "--dangerously-skip-permissions"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8"
        )
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            if player:
                player.write_log(f"GREŠKA U ANTIGRAVITY:\n{stderr}")
            return "Gospodine, došlo je do greške u Antigravity sistemu. Detalji su ispisani u konzoli."
            
        # Prikazujemo CEO chat (izveštaj, lokaciju, komande) direktno u Ultronov interfejs!
        if player and stdout:
            try:
                player.write_log("\n======= ANTIGRAVITY IZVEŠTAJ =======")
                for line in stdout.split('\n'):
                    if line.strip():
                        player.write_log(line)
                player.write_log("====================================\n")
            except Exception:
                pass
                
    except FileNotFoundError:
        return "Gospodine, komanda nije pronađena. Molim vas proverite da li je Antigravity instaliran."
    except Exception as e:
        return f"Gospodine, nisam uspeo da pokrenem komandu: {e}"
    
    # Vraćamo kratak, izgovorljiv tekst za Ultron Voice (TTS), da ne bi čitao kod naglas
    return (
        "Gospodine, program je uspešno napisan. "
        "U vašem interfejsu se nalazi izveštaj sa tačnom lokacijom i instrukcijama kako da pokrenete i testirate program."
    )
