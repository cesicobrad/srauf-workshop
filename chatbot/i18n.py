"""Fixed replies in English, Slovenian, Croatian/Serbian (Latin) and Serbian (Cyrillic)."""

STRINGS = {
    "unknown": {
        "en": "I don't know that from {business}'s information, and I'd rather not guess. A person from the team can answer you: leave your name and e-mail below.",
        "sl": "Tega v podatkih podjetja {business} ne najdem in ne želim ugibati. Nekdo iz ekipe vam lahko odgovori: spodaj pustite ime in e-naslov.",
        "hr": "To ne znam iz podataka tvrtke {business}, a ne želim nagađati. Netko iz tima može vam odgovoriti: ostavite ime i e-mail ispod.",
        "sr": "То не знам из података фирме {business}, а не желим да нагађам. Неко из тима може да вам одговори: оставите име и имејл испод.",
    },
    "out_of_scope": {
        "en": "I can only help with questions about {business}: services, prices, opening hours and so on.",
        "sl": "Pomagam lahko samo z vprašanji o podjetju {business}: storitve, cene, delovni čas in podobno.",
        "hr": "Mogu pomoći samo s pitanjima o {business}: usluge, cijene, radno vrijeme i slično.",
        "sr": "Могу да помогнем само са питањима о {business}: услуге, цене, радно време и слично.",
    },
    "handoff": {
        "en": "Of course. Leave your name and e-mail below and someone from {business} will get back to you.",
        "sl": "Seveda. Spodaj pustite ime in e-naslov in nekdo iz podjetja {business} se vam bo oglasil.",
        "hr": "Naravno. Ostavite ime i e-mail ispod i netko iz {business} će vam se javiti.",
        "sr": "Наравно. Оставите име и имејл испод и неко из {business} ће вам се јавити.",
    },
    "from_docs": {
        "en": "Here is what {business}'s documents say:",
        "sl": "To piše v dokumentih podjetja {business}:",
        "hr": "Ovo piše u dokumentima tvrtke {business}:",
        "sr": "Ово пише у документима фирме {business}:",
    },
    "greeting_reply": {
        "en": "Hello! Ask me anything about {business}: services, prices, opening hours.",
        "sl": "Pozdravljeni! Vprašajte me karkoli o podjetju {business}: storitve, cene, delovni čas.",
        "hr": "Pozdrav! Pitajte me bilo što o {business}: usluge, cijene, radno vrijeme.",
        "sr": "Здраво! Питајте ме било шта о {business}: услуге, цене, радно време.",
    },
    "budget_note": {
        "en": "(The assistant is in basic mode this month, so this is a quote from the documents.)",
        "sl": "(Asistent je ta mesec v osnovnem načinu, zato je to citat iz dokumentov.)",
        "hr": "(Asistent je ovaj mjesec u osnovnom načinu, pa je ovo citat iz dokumenata.)",
        "sr": "(Асистент је овог месеца у основном режиму, па је ово цитат из докумената.)",
    },
    "error_note": {
        "en": "(The AI service didn't respond, so this is a quote from the documents.)",
        "sl": "(Storitev UI se ni odzvala, zato je to citat iz dokumentov.)",
        "hr": "(AI usluga nije odgovorila, pa je ovo citat iz dokumenata.)",
        "sr": "(AI услуга није одговорила, па је ово цитат из докумената.)",
    },
    "rate_limited": {
        "en": "That's a lot of questions in a short time. Please wait a minute and try again.",
        "sl": "To je veliko vprašanj v kratkem času. Počakajte minuto in poskusite znova.",
        "hr": "To je puno pitanja u kratkom vremenu. Pričekajte minutu i pokušajte ponovno.",
        "sr": "То је много питања за кратко време. Сачекајте минут и покушајте поново.",
    },
    "too_long": {
        "en": "Please keep your question under {n} characters.",
        "sl": "Vprašanje naj ima manj kot {n} znakov.",
        "hr": "Pitanje neka bude kraće od {n} znakova.",
        "sr": "Питање нека буде краће од {n} знакова.",
    },
    "blocked": {
        "en": "Sorry, I can't help with that. I can answer questions about {business}.",
        "sl": "Žal pri tem ne morem pomagati. Odgovarjam na vprašanja o podjetju {business}.",
        "hr": "Nažalost, u tome ne mogu pomoći. Odgovaram na pitanja o {business}.",
        "sr": "Нажалост, у томе не могу да помогнем. Одговарам на питања о {business}.",
    },
}


def t(key: str, lang: str, **kw) -> str:
    table = STRINGS[key]
    return table.get(lang, table["en"]).format(**kw)
