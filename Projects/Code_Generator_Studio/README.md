# Ultron Code Generation Studio

Futuristička razvojna platforma za generisanje koda, sandbox izvršavanje i statičku analizu koda, kreirana od strane **Antigravity Coder-a** za **Ultron asistenta**.

---

## 🚀 Šta je napravljeno?

**Ultron Code Generation Studio** je full-stack aplikacija koja korisniku pruža:
1. **Biblioteku gotovih arhitektura i algoritama**:
   - **REST API sa Token Autentifikacijom** (Flask + SQLite CRUD)
   - **LRU Cache** sa garantovanom $O(1)$ vremenskom složenošću
   - **Dijkstra algoritam** za najkraći put u mreži (Min-Heap Priority Queue)
   - **Sistemski dijagnostički monitor** (CPU, memorija, benchmark, disk telemetrija)
   - **Smart File Organizer** (Automatska klasifikacija i sortiranje fajlova)
   - **Interactive CLI Toolkit** (ANSI progres barovi, subkomande)
2. **AI / Parametarski Custom Code Generator**:
   - Generisanje koda na osnovu jezika (Python, JavaScript, Bash), arhitekturalnog šablona (Modular Controller, Event Pipeline, Microservice) i željenih funkcionalnosti.
3. **Live Python Execution Sandbox**:
   - Bezbedno pokretanje koda direktno iz web browsera uz hvatanje standardnog izlaza (`stdout`), grešaka (`stderr`), koda povratka i preciznog merenja milisekundi izvršavanja.
4. **AST Code Metrics & Complexity Analyzer**:
   - Statička inspekcija koda u realnom vremenu: broj linija, funkcije, klase, uvezeni moduli, pokrivenost docstring dokumentacijom i Cyclomatic Complexity ocena.

---

## 📂 Lokacija projekta

Putanja od korena projekta:
```
Projects\Code_Generator_Studio
```

---

## ⚡ Kako pokrenuti i testirati

### Opcija 1: Jednim klikom
Dvokliknite na batch fajl:
```cmd
Projects\Code_Generator_Studio\run.bat
```

### Opcija 2: Preko terminala / PowerShell-a
Otvorite terminal i pokrenite:
```powershell
cd "Projects\Code_Generator_Studio"
python app.py
```

Server se automatski podiže na:
```
http://127.0.0.1:5050
```
i sam otvara vaš podrazumevani web browser.

---

## 🧪 Testiranje funkcionalnosti
1. U levom panelu kliknite na **LRU Cache** ili **System Performance & Health Monitor**.
2. Kliknite na zeleno dugme **Run Code** (ili pritisnite `Ctrl + Enter`).
3. U donjem terminalu odmah ćete videti izlaz izvršavanja i vreme u milisekundama!
4. Pređite na tab **Custom Generator**, unesite željene parametre i kliknite **Synthesize Code** kako biste testirali generisanje novog koda.
