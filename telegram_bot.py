import telebot
import os
import subprocess
import speech_recognition as sr
import time
import requests
import urllib3
import psutil
import threading

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def auto_exit_monitor():
    time.sleep(5)
    failures = 0
    while True:
        time.sleep(3)
        found = False
        for p in psutil.process_iter(['cmdline']):
            try:
                cmd = p.info.get('cmdline')
                if cmd and "python" in cmd[0].lower() and any("main.py" in c.lower() for c in cmd):
                    found = True
                    break
            except:
                pass
        if not found:
            failures += 1
            if failures >= 5:
                os._exit(0)
        else:
            failures = 0

threading.Thread(target=auto_exit_monitor, daemon=True).start()

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")
from core.security import install_redaction
install_redaction()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
bot = telebot.TeleBot(TOKEN, threaded=False)
recognizer = sr.Recognizer()

ULTRON_DIR = str(Path(__file__).resolve().parent)
TEMP_DIR = os.path.join(ULTRON_DIR, "telegram_temp")
os.makedirs(TEMP_DIR, exist_ok=True)
# Restrict remote control to the previously paired chat or explicit configuration.
_chat_file = Path(TEMP_DIR) / "chat_id.txt"
_allowed_chat = os.getenv("TELEGRAM_ALLOWED_CHAT_ID") or (_chat_file.read_text().strip() if _chat_file.exists() else "")
def authorized(message):
    return bool(_allowed_chat and str(message.chat.id) == _allowed_chat and message.chat.type == "private")


@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not authorized(message): return
    bot.reply_to(message, "✅ Sistem je direktno priključen na Ultrona! \nSve što napišeš ili izgovoriš biće prosleđeno direktno u glavni program na tvom računaru.")

def send_to_car(text):
    ports_to_try = [8000, 8001]
    for port in ports_to_try:
        try:
            res = requests.post(f"https://127.0.0.1:{port}/api/command", json={"text": text}, timeout=5, verify=False)
            if res.status_code == 200: return True
        except: pass
        try:
            res = requests.post(f"http://127.0.0.1:{port}/api/command", json={"text": text}, timeout=5)
            if res.status_code == 200: return True
        except: pass
    return False

@bot.message_handler(content_types=['text'])
def handle_text(message):
    if not authorized(message): return
    try:
        text = message.text
        chat_id = message.chat.id
        with open(os.path.join(TEMP_DIR, "chat_id.txt"), "w") as f:
            f.write(str(chat_id))
        print(f"\n[Telegram Tekst] Ti: {text}")
        
        # Brisemo stari speech fajl
        speech_file = os.path.join(TEMP_DIR, "last_speech.txt")
        if os.path.exists(speech_file):
            os.remove(speech_file)
            
        if send_to_car(text):
            bot.reply_to(message, "⏳ Ultron radi...")
            
            for _ in range(150):
                time.sleep(0.2)
                if os.path.exists(speech_file):
                    break
                    
            if os.path.exists(speech_file):
                with open(speech_file, "r", encoding="utf-8") as f:
                    car_answer = f.read().strip()
                
                tts_file = os.path.join(TEMP_DIR, "response.mp3")
                
                subprocess.run(["edge-tts","--voice","hr-HR-SreckoNeural","--text",car_answer,"--write-media",tts_file],timeout=30,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                
                if os.path.exists(tts_file):
                    with open(tts_file, 'rb') as voice:
                        bot.send_voice(message.chat.id, voice)
                else:
                    bot.reply_to(message, car_answer)
            else:
                bot.reply_to(message, "Komanda prosleđena; rezultat nije potvrđen. (Ultron nije nista rekao)")
        else:
            bot.reply_to(message, "❌ Ultronov server nije dostupan.")
            
    except Exception as e:
        bot.reply_to(message, f"Sistemska greška: {e}")

@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    if not authorized(message): return
    try:
        chat_id = message.chat.id
        with open(os.path.join(TEMP_DIR, "chat_id.txt"), "w") as f:
            f.write(str(chat_id))
        bot.reply_to(message, "⏳ Slušam i prevodim...")
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        ogg_path = os.path.join(TEMP_DIR, "input.ogg")
        wav_path = os.path.join(TEMP_DIR, "input.wav")
        
        with open(ogg_path, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        subprocess.run(['ffmpeg', '-y', '-i', ogg_path, '-ar', '16000', '-ac', '1', wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        text = ""
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="sr-RS")
            
        print(f"\n[Telegram Glas] Ti: {text}")
        
        speech_file = os.path.join(TEMP_DIR, "last_speech.txt")
        if os.path.exists(speech_file):
            os.remove(speech_file)
            
        if send_to_car(text):
            for _ in range(150):
                time.sleep(0.2)
                if os.path.exists(speech_file): break
                
            if os.path.exists(speech_file):
                with open(speech_file, "r", encoding="utf-8") as f:
                    ans = f.read().strip()
                tts_file = os.path.join(TEMP_DIR, "response.mp3")
                subprocess.run(["edge-tts","--voice","hr-HR-SreckoNeural","--text",ans,"--write-media",tts_file],timeout=30,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
                if os.path.exists(tts_file):
                    with open(tts_file, 'rb') as voice:
                        bot.send_voice(message.chat.id, voice)
                else:
                    bot.reply_to(message, ans)
            else:
                bot.reply_to(message, "Komanda prosleđena; rezultat nije potvrđen. (Ultron je oćutao)")
        else:
            bot.reply_to(message, "❌ Ultronov server ne odgovara. Da li je upaljen?")
            
    except sr.UnknownValueError:
        bot.reply_to(message, "Nisam uspeo lepo da te čujem. Ponovi malo jasnije.")
    except Exception as e:
        bot.reply_to(message, f"Sistemska greška: {e}")

print("[USPESNO] Telegram Bot radi u direktnom modu! Ocekujem poruke...")
bot.infinity_polling(timeout=20, logger_level=None)
