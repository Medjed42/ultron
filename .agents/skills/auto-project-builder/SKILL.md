---
name: auto-project-builder
description: >-
  Koristi ovaj skill kada korisnik traži da mu automatski napraviš ceo projekat (npr. sajt), proveriš sa njim na kraju i kompletiraš projekat do kraja.
---

# Auto Project Builder Workflow

Ovo je tvoj workflow kada korisnik traži da mu napraviš i prepraviš projekat (npr. web sajt) od početka do kraja. Uvek strogo prati sledeće korake:

## Koraci

1. **Inicijalizacija i Planiranje**:
   - Ako korisnik nije dao dovoljno detalja, postavi kratka pitanja (npr. koji dizajn želi, koje su glavne funkcionalnosti). Ako jeste, pređi na korak 2.
   - Sve nove projekte OBAVEZNO kreiraj u direktorijumu: `Projects (relative to the project root)`. Za svaki novi projekat tamo napravi odgovarajući podfolder.

2. **Pisanje Koda**:
   - Napiši kompletan kod za projekat (HTML, CSS, JS, React, ili koji god framework/jezik je izabran).
   - Kreiraj sve potrebne fajlove i poveži ih logično.

3. **Pokretanje i Interno Testiranje**:
   - Pokreni lokalni server ako je potrebno (npr. `python -m http.server`, `npm run start`) u pozadini.
   - Proveri da li se kod uspešno kompajlira/pokreće i da nema grešaka u logovima.

4. **Provera sa Korisnikom (Review)**:
   - Obavesti korisnika da je osnovna verzija završena.
   - Daj mu instrukcije kako da pregleda sajt (npr. otvaranjem `index.html` fajla ili odlaskom na određeni `localhost` port).
   - Aktivno ga pitaj: "Pogledaj projekat pa mi reci šta želiš da prepravimo, dodamo ili promenimo?"

5. **Iteracija i Prepravke**:
   - Na osnovu korisničkih povratnih informacija, prepravi kod.
   - Nakon svake prepravke obavesti korisnika šta je promenjeno i traži novu potvrdu.
   - Ponavljaj ovaj korak dok korisnik ne bude potpuno zadovoljan.

6. **Završetak Projekta**:
   - Kada korisnik potvrdi da je to to, obavesti ga da je projekat kompletiran.
   - Ugasi sve eventualne pozadinske procese (servere) i čestitaj na završenom projektu!
