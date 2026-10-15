"""Serbian/English text handling for intent detection.

Three jobs:
  * `latin()`  — Serbian Cyrillic to Latin, so one rule set serves both scripts.
  * `fold()`   — lower-case and strip diacritics (š→s, đ→dj) for matching only;
                 the user's original spelling is what gets displayed.
  * `place_lemma()` — Serbian place names are declined ("od Kuršumlije",
                 "u Nišu", "u Novom Sadu", "do Vrbasa"). Geocoders want the
                 base form, so known towns are matched through every case form
                 and unknown names get rule-based candidates.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Optional

_CYR = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Ђ": "Đ", "Е": "E", "Ж": "Ž", "З": "Z",
    "И": "I", "Ј": "J", "К": "K", "Л": "L", "Љ": "Lj", "М": "M", "Н": "N", "Њ": "Nj", "О": "O",
    "П": "P", "Р": "R", "С": "S", "Т": "T", "Ћ": "Ć", "У": "U", "Ф": "F", "Х": "H", "Ц": "C",
    "Ч": "Č", "Џ": "Dž", "Ш": "Š",
}
_CYR.update({k.lower(): v.lower() for k, v in _CYR.items()})


def latin(text: str) -> str:
    return "".join(_CYR.get(ch, ch) for ch in (text or ""))


def fold(text: str) -> str:
    """Matching key: Latin script, lower case, no diacritics, single spaces."""
    t = latin(text).replace("đ", "dj").replace("Đ", "Dj")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.lower().replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


_SR_WORDS = {"je", "i", "u", "od", "do", "za", "na", "mi", "me", "koliko", "nadji", "pronadji", "pokazi",
             "prikazi", "gde", "koji", "koja", "koje", "sta", "stan", "stana", "smestaj", "samo", "evra",
             "kolima", "autobusom", "blizu", "sutra", "danas", "ima", "treba", "daj", "moj", "moje", "mapi",
             "najjeftiniji", "najjeftiniju", "apartman", "sledeci", "tamo", "li", "da", "se", "su", "sam",
             "kako", "zasto", "molim", "hvala", "napisi", "objasni", "ovo", "ono", "nije", "jeste", "dan",
             "noc", "osobe", "dinara", "centru", "najblizi", "rezervaciju", "link", "proveri", "istrazi"}
_EN_WORDS = {"the", "and", "find", "me", "how", "far", "is", "it", "from", "to", "an", "a", "in", "show",
             "what", "where", "which", "near", "please", "for", "my", "of", "with", "only", "under",
             "cheapest", "closest", "give", "map", "on", "at", "can", "you", "i", "are", "this", "that",
             "apartment", "hotel", "stay", "night", "nights", "people", "tomorrow", "today", "write",
             "explain", "why", "does", "do", "long", "take", "by", "car", "bus", "check", "search"}


def is_serbian(text: str) -> bool:
    """Language guess for replies: Cyrillic → Serbian; otherwise the language
    with more of its common words wins. Serbian letters only break a tie, so
    "Find an apartment in Niš" stays English."""
    if re.search(r"[Ѐ-ӿ]", text or ""):
        return True
    words = re.findall(r"[a-z]+", fold(text))
    sr = sum(1 for w in words if w in _SR_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    if sr != en:
        return sr > en
    return bool(re.search(r"[čćžšđČĆŽŠĐ]", text or ""))


# ── Gazetteer ────────────────────────────────────────────────────────────────
# Base name → (country code, display name). Serbian exonyms map to the
# name a geocoder resolves reliably. Towns not listed still work: the
# geocoder is asked with rule-based base-form candidates.

_SERBIA = """
Beograd, Novi Sad, Niš, Kragujevac, Subotica, Zrenjanin, Pančevo, Čačak, Novi Pazar, Kraljevo,
Smederevo, Leskovac, Užice, Vranje, Valjevo, Šabac, Sombor, Požarevac, Pirot, Zaječar, Kikinda,
Sremska Mitrovica, Jagodina, Vršac, Bor, Prokuplje, Loznica, Kruševac, Vrbas, Kuršumlija, Ruma,
Inđija, Stara Pazova, Bačka Palanka, Paraćin, Ćuprija, Aranđelovac, Gornji Milanovac, Lazarevac,
Obrenovac, Mladenovac, Negotin, Kladovo, Majdanpek, Knjaževac, Aleksinac, Sokobanja, Soko Banja,
Vrnjačka Banja, Trstenik, Ivanjica, Arilje, Požega, Prijepolje, Priboj, Nova Varoš, Sjenica, Tutin,
Raška, Brus, Blace, Lebane, Bojnik, Medveđa, Vladičin Han, Surdulica, Bujanovac, Preševo,
Dimitrovgrad, Babušnica, Bela Palanka, Despotovac, Svilajnac, Velika Plana, Smederevska Palanka,
Topola, Rača, Batočina, Lapovo, Kovin, Bela Crkva, Alibunar, Kovačica, Opovo, Senta, Ada, Kanjiža,
Bečej, Novi Bečej, Temerin, Žabalj, Titel, Bačka Topola, Mali Iđoš, Kula, Odžaci, Apatin, Bač, Šid,
Irig, Pećinci, Sremski Karlovci, Zlatibor, Kopaonik, Divčibare, Tara, Palić, Golubac,
Donji Milanovac, Veliko Gradište, Petrovac na Mlavi, Žagubica, Boljevac, Zemun, Novi Beograd,
Surčin, Grocka, Barajevo, Sopot, Ub, Lajkovac, Ljig, Mionica, Osečina, Krupanj, Ljubovija,
Mali Zvornik, Bogatić, Vladimirci, Koceljeva, Čajetina, Kosjerić, Bajina Bašta, Lučani, Guča, Knić,
Varvarin, Ćićevac, Merošina, Doljevac, Gadžin Han, Žitorađa, Vlasotince, Crna Trava, Trgovište,
Bosilegrad, Futog, Veternik, Sremska Kamenica, Petrovaradin, Čoka, Novi Kneževac, Srbobran,
Bačko Petrovo Selo, Kać, Čelarevo, Beočin, Vrdnik, Kosovska Mitrovica, Gnjilane, Priština,
Prizren, Peć, Leposavić, Zubin Potok, Zvečan, Novi Banovci, Stari Banovci, Batajnica,
Nova Pazova, Šimanovci, Lovćenac, Crvenka, Feketić, Bački Petrovac
"""
_FOREIGN = {
    "beč": ("at", "Wien"), "budimpešta": ("hu", "Budapest"), "solun": ("gr", "Thessaloniki"),
    "atina": ("gr", "Athens"), "minhen": ("de", "München"), "rim": ("it", "Roma"),
    "pariz": ("fr", "Paris"), "prag": ("cz", "Praha"), "skoplje": ("mk", "Skopje"),
    "sofija": ("bg", "Sofia"), "temišvar": ("ro", "Timișoara"), "zagreb": ("hr", "Zagreb"),
    "ljubljana": ("si", "Ljubljana"), "sarajevo": ("ba", "Sarajevo"),
    "banja luka": ("ba", "Banja Luka"), "podgorica": ("me", "Podgorica"),
    "budva": ("me", "Budva"), "kotor": ("me", "Kotor"), "herceg novi": ("me", "Herceg Novi"),
    "istanbul": ("tr", "İstanbul"), "carigrad": ("tr", "İstanbul"), "berlin": ("de", "Berlin"),
    "milano": ("it", "Milano"), "venecija": ("it", "Venezia"), "trst": ("it", "Trieste"),
    "split": ("hr", "Split"), "dubrovnik": ("hr", "Dubrovnik"), "segedin": ("hu", "Szeged"),
    "london": ("gb", "London"), "amsterdam": ("nl", "Amsterdam"), "cirih": ("ch", "Zürich"),
    "ženeva": ("ch", "Genève"), "brisel": ("be", "Bruxelles"), "madrid": ("es", "Madrid"),
    "barselona": ("es", "Barcelona"), "lisabon": ("pt", "Lisboa"), "varšava": ("pl", "Warszawa"),
    "bukurešt": ("ro", "București"), "tirana": ("al", "Tirana"), "ohrid": ("mk", "Ohrid"),
    "tivat": ("me", "Tivat"), "bar": ("me", "Bar"), "ulcinj": ("me", "Ulcinj"),
}
_ALIASES = {
    "belgrade": "Beograd", "nis": "Niš", "nish": "Niš", "novisad": "Novi Sad",
    "vienna": "Beč", "budapest": "Budimpešta", "thessaloniki": "Solun", "athens": "Atina",
    "munich": "Minhen", "rome": "Rim", "paris": "Pariz", "prague": "Prag", "skopje": "Skoplje",
    "sofia": "Sofija", "timisoara": "Temišvar",
}


@lru_cache(maxsize=1)
def gazetteer() -> dict[str, tuple[str, str]]:
    """fold(base name) → (country code, display name)."""
    out: dict[str, tuple[str, str]] = {}
    for name in (n.strip() for n in _SERBIA.replace("\n", " ").split(",")):
        if name:
            out[fold(name)] = ("rs", name)
    for k, (cc, display) in _FOREIGN.items():
        out[fold(k)] = (cc, display)
    return out


def _spoken_name(base_key: str, display: str) -> str:
    """The Serbian name a user declines: 'beč' for Wien, 'Niš' for Niš."""
    for k in _FOREIGN:
        if fold(k) == base_key:
            return k
    return display


# ── Declension ───────────────────────────────────────────────────────────────

def _noun_forms(w: str) -> set[str]:
    """Case forms of one Serbian noun-like word (lower-case, diacritics kept)."""
    forms = {w}
    if w.endswith(("ci", "vi", "ni", "ri", "ti")) and len(w) > 4:
        # plural town names: Karlovci → Karlovaca, Karlovcima, Karlovce
        s = w[:-1]
        forms |= {s + "ima", s + "e", s + "a"}
        m = re.match(r"^(.*[^aeiou])([cvnrt])$", s)
        if m:
            forms.add(m.group(1) + "a" + m.group(2) + "a")
    if w.endswith("a"):
        s = w[:-1]
        forms |= {s + "e", s + "i", s + "u", s + "om", s + "o"}
        if s.endswith("k"):
            forms.add(s[:-1] + "ci")
        if s.endswith("g"):
            forms.add(s[:-1] + "zi")
        if s.endswith("ij"):
            forms |= {s + "e", s + "i"}
    elif w.endswith(("o", "e")):
        s = w[:-1]
        forms |= {s + "a", s + "u", s + "om", s + "em"}
    else:
        stems = {w}
        # fleeting a: Kragujevac → Kragujevca, Čačak → Čačka, Zaječar stays.
        m = re.match(r"^(.*[^aeiou])a([cklnrtj])$", w)
        if m and len(w) > 4 and w.endswith(("ac", "ak", "al", "an", "at")):
            stems.add(m.group(1) + m.group(2))
        for s in stems:
            soft = s.endswith(("š", "ž", "č", "ć", "dž", "đ", "j", "lj", "nj", "c"))
            forms |= {s + "a", s + "u", s + "e", s + ("em" if soft else "om")}
    return forms


def _adj_forms(w: str) -> set[str]:
    forms = {w}
    if w.endswith("i"):             # Novi → Novog, Novom, Novim; Sremski (pl.) → Sremskih, Sremskim
        s = w[:-1]
        forms |= {s + "og", s + "oga", s + "om", s + "ome", s + "im", s + "i", s + "ih", s + "e", s + "ima"}
    elif w.endswith("a"):           # Sremska → Sremske, Sremskoj, Sremsku
        s = w[:-1]
        forms |= {s + "e", s + "oj", s + "u", s + "om"}
    elif w.endswith("o"):           # Veliko → Velikog, Velikom
        s = w[:-1]
        forms |= {s + "og", s + "om", s + "a"}
    return forms


_ADJ_SUFFIX = ("ski", "ska", "sko", "čki", "čka", "čko", "ški", "ška", "ški", "ni", "na", "no",
               "ji", "ja", "ki", "ka", "ko", "vi", "va", "vo", "lji", "lja", "nji", "nja")
_ADJ_WORDS = {"novi", "nova", "novo", "stari", "stara", "staro", "veliki", "velika", "veliko",
              "mali", "mala", "malo", "donji", "donja", "gornji", "gornja", "bela", "beli",
              "crna", "crni", "sremska", "sremski", "backa", "backo", "vrnjacka", "bajina",
              "smederevska", "vladicin", "gadzin", "soko"}


def _word_forms(word: str, position: int, total: int) -> set[str]:
    lw = word.lower()
    if lw in ("na", "nad", "pod"):
        return {lw}
    if total > 1 and position < total - 1 and (fold(lw) in _ADJ_WORDS or lw.endswith(_ADJ_SUFFIX)):
        return _adj_forms(lw)
    if fold(lw) in ("han", "palanka", "banja", "bašta", "basta", "selo") or position == total - 1:
        return _noun_forms(lw)
    return _noun_forms(lw)


@lru_cache(maxsize=1)
def _form_index() -> dict[str, str]:
    """fold(declined form) → fold(base name), for every gazetteer entry."""
    index: dict[str, str] = {}
    for base_key, (_cc, display) in gazetteer().items():
        parts = latin(_spoken_name(base_key, display)).split()
        if len(parts) == 1:
            combos = {f for f in _noun_forms(parts[0].lower())}
        else:
            # Decline all words in the same case together is the norm; allow
            # any combination — the matching is still against whole phrases.
            combos = {""}
            for i, p in enumerate(parts):
                if p.lower() == "na":
                    # "Petrovac na Mlavi": only the head word declines.
                    combos = {c + " na" if c else "na" for c in combos}
                    continue
                if i > 0 and parts[i - 1].lower() == "na":
                    combos = {c + " " + p.lower() for c in combos}
                    continue
                combos = {(c + " " + f).strip() for c in combos for f in _word_forms(p, i, len(parts))}
        for f in combos:
            index.setdefault(fold(f), base_key)
        index[base_key] = base_key
    for alias, target in _ALIASES.items():
        index[fold(alias)] = fold(target)
    return index


def lookup_place(phrase: str) -> Optional[tuple[str, str]]:
    """Known place for an exact (possibly declined) phrase → (country, display)."""
    key = _form_index().get(fold(phrase))
    return gazetteer().get(key) if key else None


def place_lemma(phrase: str) -> tuple[str, Optional[str], list[str]]:
    """Base form of a place phrase.

    Returns (display, country_or_None, candidates). Known towns resolve to
    their gazetteer entry; unknown phrases return the phrase itself first and
    then rule-based nominative guesses for the geocoder to try in order."""
    phrase = re.sub(r"\s+", " ", (phrase or "").strip(" ,.?!:;\"'"))
    hit = lookup_place(phrase)
    if hit:
        return hit[1], hit[0], [hit[1]]
    candidates = [phrase]
    words = latin(phrase).split()
    if words:
        last = words[-1]
        stem_guesses = []
        lw = last.lower()
        for suf, rep in (("oj", "a"), ("om", ""), ("em", ""), ("u", ""), ("a", ""), ("e", "a"),
                         ("i", "a"), ("u", "a"), ("ca", "ac"), ("ka", "ak"), ("ije", "ija"),
                         ("iji", "ija")):
            if lw.endswith(suf) and len(lw) - len(suf) >= 2:
                stem_guesses.append(lw[: len(lw) - len(suf)] + rep)
        for g in stem_guesses:
            cand = " ".join(words[:-1] + [g[:1].upper() + g[1:]])
            if cand not in candidates:
                candidates.append(cand)
    return phrase, None, candidates[:6]


def title_place(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] if w.lower() not in ("na",) else w for w in text.split())
