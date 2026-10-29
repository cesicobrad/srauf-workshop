"""Tell English, Slovenian and Serbian/Croatian apart, cheaply and offline.

Slovenian and Croatian/Serbian share most of their alphabet and many words, so this scores marker words that
belong to only one of them ("kaj", "lahko", "stane" vs "što", "šta", "može", "košta"). Cyrillic means Serbian.
Claude reads the language on its own; this detector picks the language for fixed texts (the handoff form,
"I don't know") and for demo mode, and it is checked by tests/test_language.py.
"""
from __future__ import annotations

import re

LANGS = ("en", "sl", "hr")  # "hr" stands for the shared Serbian/Croatian/Bosnian standard (Latin script)

MARKERS = {
    "sl": """kaj kje kdaj kako kateri katera katero lahko ali stane stanejo koliko zakaj imate imaš ste sem smo
        mi bi rad rada želim prosim hvala živjo zdravo pozdravljeni dober dan jutri danes odprto odprti zaprto
        kolo kolesa kolesu kolo kolesom servis servisa popravilo popravila zračnica guma gumo zavore zavor
        prestave verigo veriga cena cene cenik delavnica delavnici delovni čas ura ure uri sobota soboto nedelja
        nedeljo praznik prazniki plačilo plačam kartico gotovino garancija garancijo prevzem prevzamem
        termin naročiti naročim rezervirati izposoja izposodite izposojate prodajate rabljena električno
        električna kolesar odpoved odpovedati moje mojo vaše vašo tudi še že zdaj potem pri pa
        delate popravite servisirate zamenjate pripeljem pustim dni teden tednu če
        jih jo nič vse samo prej kdo nihče nekdo tole tale ni nisem bom bomo boste hočem včeraj vedno nikoli
        sedaj tukaj zelo navodila pozabi lepo""",
    "hr": """što šta gdje gde kada kad kako koji koja koje može možete mogu mogli košta koštaju koliko zašto
        imate imaš ste sam smo bih htio htjeo hteo htjela želim molim hvala bok zdravo pozdrav dobar dan sutra
        danas otvoreno otvoreni zatvoreno bicikl bicikla biciklu biciklom servis servisa popravak popravka popravku
        zračnica guma gumu kočnice kočnica brzine lanac lanca cijena cena cijene cjenik radionica radionici
        radno vrijeme vreme sati subota subotu nedjelja nedelju nedelja praznik praznici plaćanje platiti
        karticom gotovinom garancija garanciju jamstvo preuzimanje termin naručiti rezervirati iznajmljivanje
        najam prodajete rabljeni električni otkazati moj moja vaš vaša također takođe već sada onda kod
        govorite radite popravljate zamijenite donijeti donesem ostavim dana tjedan nedelja
        nešto ništa prije treba trebam trebao li ja tko nitko netko ovo ovaj ova nije nisam jesam će ću ćemo
        hoću hoćete jučer juče uvijek nikad nikada ovdje poslije posle puno mnogo zaboravi upute uputstva""",
    # Short words that are also Slovenian/Croatian ("do", "in", "on", "to", "i", "a") are left out on purpose.
    "en": """the an is are does can could what when where how which who much many price prices cost costs
        open opening hours you your yours my we our bike bikes bicycle repair service fix brakes tyre tire
        chain gears please thanks hello hi day week tomorrow today saturday sunday pay card cash warranty
        book booking rent sell used electric e-bike cancel have has will would should about and or with
        for from of at it this that there here long take need want closed""",
}

# Words both South Slavic languages share are left out above or appear in both lists ("ste", "servis"):
# they cancel out. Words with letters that only one alphabet uses tip the balance.
_WORDS = {lang: set(words.split()) for lang, words in MARKERS.items()}
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_TOKEN = re.compile(r"[a-zčćđšžäöüé]+")


def scores(text: str) -> dict[str, float]:
    words = _TOKEN.findall(text.lower())
    s = {lang: 0.0 for lang in LANGS}
    for w in words:
        for lang in LANGS:
            if w in _WORDS[lang]:
                s[lang] += 1.0
    lowered = text.lower()
    if re.search(r"[ćđ]", lowered):
        s["hr"] += 2.0          # Slovenian has no ć or đ
    if re.search(r"[čšž]", lowered):
        s["en"] -= 1.0
    return s



def confident(text: str) -> bool:
    """True when one language clearly wins (Cyrillic, or a lead of at least one marker word)."""
    if _CYRILLIC.search(text):
        return True
    ranked = sorted(scores(text).values(), reverse=True)
    return ranked[0] > 0 and ranked[0] - ranked[1] >= 1

def detect(text: str, hint: str = "") -> str:
    """Return "en", "sl", "hr" or "sr" (Cyrillic). `hint` (e.g. the page's lang) breaks ties."""
    if _CYRILLIC.search(text):
        return "sr"
    s = scores(text)
    best = max(s.values())
    hint = normalise(hint)
    if best <= 0:
        return hint or "en"
    winners = [lang for lang in LANGS if s[lang] == best]
    if len(winners) == 1:
        return winners[0]
    if hint in winners:
        return hint
    return winners[0]


def normalise(code: str) -> str:
    """Browser codes to ours: sl-SI -> sl, hr/sr/bs (Latin) -> hr, sr-Cyrl -> sr."""
    code = (code or "").lower()
    if code.startswith("sl"):
        return "sl"
    if code.startswith("sr-cyrl"):
        return "sr"
    if code.startswith(("hr", "sr", "bs", "sh", "cnr")):
        return "hr"
    if code.startswith("en"):
        return "en"
    return ""


NAMES = {"en": "English", "sl": "Slovenian", "hr": "Croatian/Serbian (Latin script)", "sr": "Serbian (Cyrillic script)"}
