"""Определение языка материала.

Базовый детектор — прозрачные правила: письменность, характерные буквы
(ў/қ/ғ/ҳ — узбекский, ә/ұ/і — казахский, ӣ/ӯ/ҷ — таджикский и т. д.)
и частотные служебные слова. Он работает офлайн и объясним.

Если установлен ``fasttext`` и задан путь к модели GlotLID
(``DOZOR_GLOTLID_MODEL``), используется она: GlotLID покрывает более
1600 языков, включая узбекский, казахский, киргизский, таджикский,
туркменский, каракалпакский и уйгурский.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

LANGUAGES = {
    "ru": "Русский",
    "uz": "Узбекский",
    "kk": "Казахский",
    "ky": "Киргизский",
    "tg": "Таджикский",
    "tk": "Туркменский",
    "tr": "Турецкий",
    "en": "Английский",
    "fr": "Французский",
    "de": "Немецкий",
    "es": "Испанский",
    "it": "Итальянский",
    "ko": "Корейский",
    "ja": "Японский",
    "zh": "Китайский",
    "fa": "Персидский",
    "ar": "Арабский",
    "und": "Не определён",
}

# Языки параллельного поиска по умолчанию (п. 9 ТЗ) и языки региона
PARALLEL_DEFAULT = ["ru", "en", "fr", "de", "es", "it", "ko", "ja", "zh", "fa", "ar"]
REGIONAL = ["uz", "kk", "ky", "tg", "tk"]

_STOPWORDS = {
    "ru": "и в не на что я он она это как с по но они мы ты все его так же уже был будет меня тебя вас нас для от если или когда только".split(),
    "uz": "va bu uchun bilan ham emas bir deb juda men sen biz siz ular endi hamma nima qanday kerak edi".split()
    + "ва бу учун билан ҳам эмас бир деб жуда мен сен биз сиз улар энди ҳамма нима керак эди".split(),
    "kk": "және бұл үшін мен деп емес бір жоқ бар сен біз сіз олар енді барлық қалай керек еді болады жатыр мәлімдеді хабарлады".split(),
    "ky": "жана бул үчүн менен эмес бир жок сен биз силер алар эми баары кантип эле болот эмне кандай жатат билдирди кармалды кылып".split(),
    "tg": "ва дар ки ба аз ин бо барои мо шумо ман ту онҳо ҳам не ҳамаи чӣ бояд буд".split(),
    "tk": "we bu üçin bilen hem däl bir men sen biz siz olar indi hemme näme gerek".split(),
    "tr": "ve bir bu için ile değil çok ben sen biz siz onlar şimdi hepsi ne nasıl gerek".split(),
    "en": "the and is are to of a in i you it not will for on with this that be have we they".split(),
    "fr": "le la les et est une des je pas que pour dans ce il nous vous sont avec sur qu c j n l d".split(),
    "de": "und der die das ist nicht ich mit ein eine zu wir sie auf für den dem sich".split(),
    "es": "el la los las y es que de no un una por para con se lo como pero muy".split(),
    "it": "il la che e non di un una per sono gli lo con si come ma molto anche della del dei delle nel nella alla dal dalla questo questa ha hanno prima dopo dell nell dall all sull".split(),
    "ru_latn": "ya ty ne na chto eto kak menya tebya vse budet uzhe tolko esli".split(),
}

_SPECIAL = {
    "uz_cyr": set("ўқғҳ"),
    "kk": set("әғқңөұүһі"),
    "ky": set("ңөү"),
    "tg": set("ғӣқӯҳҷ"),
    "tk": set("äňşýžüöç"),
    "tr": set("ğışçöü"),
    "de": set("äöüß"),
    "fr": set("éèêàçùâîôûëœ"),
    "es": set("ñáíóúü¿¡"),
    "it": set("àèéìòù"),
}

_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*", re.UNICODE)


@dataclass
class LangGuess:
    lang: str
    confidence: float
    script: str
    method: str = "rules"

    @property
    def label(self) -> str:
        return LANGUAGES.get(self.lang, self.lang)


def _script_counts(text: str) -> dict[str, int]:
    counts = {"Cyrl": 0, "Latn": 0, "Arab": 0, "Hang": 0, "Kana": 0, "Hani": 0}
    for ch in text:
        if not ch.isalpha():
            continue
        cp = ord(ch)
        if 0x0400 <= cp <= 0x052F:
            counts["Cyrl"] += 1
        elif cp < 0x0250 or 0x1E00 <= cp <= 0x1EFF:
            counts["Latn"] += 1
        elif 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0xFB50 <= cp <= 0xFEFF:
            counts["Arab"] += 1
        elif 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
            counts["Hang"] += 1
        elif 0x3040 <= cp <= 0x30FF:
            counts["Kana"] += 1
        elif 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            counts["Hani"] += 1
    return counts


def _score(words: list[str], chars: set[str], lang_keys: list[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    wordset = words
    for key in lang_keys:
        sw = set(_STOPWORDS.get(key, []))
        score = sum(1.0 for w in wordset if w in sw)
        special = _SPECIAL.get(key)
        if special:
            score += 2.0 * len(chars & special)
        scores[key] = score
    return scores


def _confidence(top: float, second: float, n_words: int) -> float:
    if top <= 0:
        return 0.25
    margin = (top - second) / (top + 1.0)
    length_factor = min(1.0, 0.4 + n_words / 20.0)
    return round(max(0.3, min(0.95, (0.5 + margin * 0.5) * length_factor + 0.1)), 2)


def detect_rules(text: str) -> LangGuess:
    sample = unicodedata.normalize("NFKC", text or "")
    counts = _script_counts(sample)
    total = sum(counts.values())
    if total == 0:
        return LangGuess("und", 0.0, "Zyyy")
    lowered = sample.lower().replace("ʻ", "'").replace("’", "'").replace("`", "'")
    words = _WORD.findall(lowered)
    # Элизия: «dell'aggressore», «l'homme» — учитываем часть до апострофа
    words += [w.split("'")[0] for w in words if "'" in w]
    chars = set(lowered)

    if counts["Hang"] / total > 0.3:
        return LangGuess("ko", 0.95, "Hang")
    if counts["Kana"] / total > 0.05:
        return LangGuess("ja", 0.93, "Jpan")
    if counts["Hani"] / total > 0.5:
        return LangGuess("zh", 0.85, "Hani")

    if counts["Arab"] / total > 0.5:
        fa_hits = sum(sample.count(c) for c in "پچژگیک")
        ar_hits = sum(sample.count(c) for c in "ةيىك") + 0.5 * sample.count("ال")
        if fa_hits > ar_hits:
            return LangGuess("fa", _confidence(fa_hits, ar_hits, len(words)), "Arab")
        return LangGuess("ar", _confidence(ar_hits, fa_hits, len(words)), "Arab")

    if counts["Cyrl"] >= counts["Latn"]:
        # Уникальные буквы — сильный признак
        if chars & set("ӣӯҷ"):
            return LangGuess("tg", 0.9, "Cyrl")
        if chars & set("әұі"):
            return LangGuess("kk", 0.9, "Cyrl")
        if "ў" in chars:
            return LangGuess("uz", 0.9, "Cyrl")
        scores = _score(words, chars, ["ru", "uz", "kk", "ky", "tg"])
        if chars & set("ңөү") and not chars & set("әұіғқ"):
            scores["ky"] += 3
        if chars & set("қғҳ"):
            scores["uz"] += 1.5
            scores["tg"] += 1.5
            scores["ru"] -= 2
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        lang, top = ranked[0]
        if top <= 0:
            lang = "ru"
        if lang == "kk" and not chars & set("әғқңөұүһі"):
            # Казахский текст почти всегда содержит специфические буквы
            lang = "ky"
        return LangGuess(lang, _confidence(top, ranked[1][1], len(words)), "Cyrl")

    # Латиница
    keys = ["en", "fr", "de", "es", "it", "uz", "tk", "tr", "ru_latn"]
    scores = _score(words, chars, keys)
    uz_marks = len(re.findall(r"\b\w*(?:o'|g')\w*", lowered))
    scores["uz"] += 1.5 * uz_marks
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    lang, top = ranked[0]
    if top <= 0:
        return LangGuess("und", 0.25, "Latn")
    if lang == "ru_latn":
        lang = "ru"
    return LangGuess(lang, _confidence(top, ranked[1][1], len(words)), "Latn")


@lru_cache(maxsize=1)
def _glotlid():
    path = os.environ.get("DOZOR_GLOTLID_MODEL")
    if not path:
        return None
    try:
        import fasttext  # type: ignore

        return fasttext.load_model(path)
    except Exception:  # noqa: BLE001 — модель необязательна
        return None


_GLOT_MAP = {
    "rus_Cyrl": "ru", "uzn_Latn": "uz", "uzn_Cyrl": "uz", "kaz_Cyrl": "kk", "kir_Cyrl": "ky",
    "tgk_Cyrl": "tg", "tuk_Latn": "tk", "tur_Latn": "tr", "eng_Latn": "en", "fra_Latn": "fr",
    "deu_Latn": "de", "spa_Latn": "es", "ita_Latn": "it", "kor_Hang": "ko", "jpn_Jpan": "ja",
    "cmn_Hani": "zh", "zho_Hani": "zh", "pes_Arab": "fa", "fas_Arab": "fa", "arb_Arab": "ar",
}


def detect(text: str) -> LangGuess:
    model = _glotlid()
    if model is not None and text and text.strip():
        try:
            labels, probs = model.predict(text.replace("\n", " "), k=1)
            code = labels[0].replace("__label__", "")
            if code in _GLOT_MAP:
                rules = detect_rules(text)
                return LangGuess(_GLOT_MAP[code], round(float(probs[0]), 2), rules.script, "glotlid")
        except Exception:  # noqa: BLE001
            pass
    return detect_rules(text)
