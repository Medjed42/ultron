# Midea Cloud Plugin za Ultron

Ovaj plugin omogućava Ultronu da kontroliše Midea klima-uređaje (Air Conditioners) direktno preko Midea Cloud servisa, bez potrebe za pristupom lokalnoj mreži uređaja (LAN).

## Funkcionalnosti
- **Uključivanje i isključivanje klime**
- **Podešavanje ciljne temperature** (u okviru dozvoljenog opsega uređaja)
- **Promena režima rada:** Auto, Hlađenje (Cool), Grejanje (Heat), Isušivanje (Dry), Ventilator (Fan)
- **Promena brzine ventilatora:** Auto, Low, Medium, High, Silent
- **Provera statusa:** Prijavljuje trenutno stanje, režim rada, ciljnu i sobnu temperaturu
- **Pozadinska sesija:** Održava aktivnu konekciju i osvežava token po potrebi
- **Dry-run režim:** Za testiranje voice komandi bez slanja stvarnih signala klimi

## Instalacija
Plugin zavisi od `midea-beautiful-air` i `python-dotenv` biblioteka. 
Zavisnosti su dodate u `requirements.txt` fajl u korenu Ultron projekta.

Pokreni komandu iz korena projekta kako bi instalirao potrebne pakete:
```bash
pip install -r requirements.txt
```

## Konfiguracija
Credentials i podešavanja se čuvaju u `.env` fajlu u korenu projekta (isti direktorijum gde se nalazi `main.py`). Fajl `.env` je dodan u `.gitignore` iz bezbednosnih razloga i nikada se ne commit-uje.

1. Iskopiraj `.env.example` fajl kao `.env`:
```bash
cp .env.example .env
```
2. Otvori `.env` i popuni svoje podatke za MSmartHome / NetHome Plus aplikaciju:
```env
MIDEA_EMAIL=tvoj_email@primer.com
MIDEA_PASSWORD=tvoja_lozinka
MIDEA_DEVICE_NAME="Dnevna soba" # Opciono, u slučaju da imaš više klima na istom nalogu
MIDEA_DRY_RUN=false
```

## Podržane Ultron komande
Ultron će automatski prepoznati NLP namere zahvaljujući definiciji unutar plugin-a. Neke od podržanih komandi:

- *"Ultron, uključi klimu."*
- *"Ultron, ugasi klimu."*
- *"Postavi klimu na 23 stepena."*
- *"Uključi hlađenje na klimi."*
- *"Prebaci klimu na grejanje."*
- *"Pojačaj ventilator na maksimum."*
- *"Stavi ventilator na klimi na auto."*
- *"Koja je temperatura klime?"*
- *"Kakvo je stanje klime?"*

## Dijagnostika
Ako imaš problema sa prijavljivanjem ili klima ne reaguje, možeš pokrenuti CLI dijagnostiku direktno. Iz korena Ultron projekta, pokreni:

```bash
python -m plugins.midea_cloud
```

Ovo će se povezati sa Midea Cloud-om (pomoću `.env` podataka), pokušati da pročita spisak uređaja i testirati čitanje stanja klime, nakon čega će ispisati trenutno stanje i sve vrednosti senzora bez pokretanja celog Ultron interfejsa.

## Napomene i ograničenja
- Inicijalizacija konekcije prema Midea Cloud-u radi se asinhrono pri startovanju Ultrona (u pozadinskom thread-u) i ne blokira startup proces.
- Ukoliko Midea Cloud servis nije dostupan pri paljenju sistema, Ultron neće "pući", već će logovati grešku. Prva upućena komanda preko glasa prijaviće grešku i pokušaće da se ponovo poveže.
- Za MSmartHome i NetHome Plus koristi se isti provajder i API koji implementira `midea-beautiful-air`.
