"""Нормализация текста для сопоставления и снятие типовой обфускации.

Две ступени:

* ``fold_basic`` — регистр, Unicode NFKC, невидимые символы, апострофы,
  арабская/персидская графика, диакритика латиницы. Применяется одинаково
  к текстам и к терминам лексикона, поэтому сравнение симметрично.
* ``deobfuscate`` — снятие приёмов обхода фильтров («алгоспик»): смешение
  кириллицы и латиницы (омоглифы), цифры вместо букв (leet), буквы через
  разделители («у.б.и.т.ь»), растянутые буквы («убиииить»).

Совпадения, найденные только после снятия обфускации, помечаются отдельно:
это факт о форме записи, а не о намерениях автора.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Невидимые и форматирующие символы, которыми разбивают слова
_INVISIBLE = dict.fromkeys(
    map(ord, "​‌‍⁠﻿­᠎⁡⁢⁣⁤"), None
)
# Апострофы и похожие знаки (узбекская латиница: oʻ, gʻ, ʼ)
_APOSTROPHES = str.maketrans({c: "'" for c in "ʻʼ‘’`´ʹ′＇"})

# Арабская и персидская графика
_ARABIC_DIACRITICS = re.compile("[ً-ٰٟۖ-ۭـ]")
_ARABIC_MAP = str.maketrans(
    {
        "أ": "ا",  # أ -> ا
        "إ": "ا",  # إ -> ا
        "آ": "ا",  # آ -> ا
        "ٱ": "ا",  # ٱ -> ا
        "ي": "ی",  # ي -> ی
        "ى": "ی",  # ى -> ی
        "ئ": "ی",  # ئ -> ی
        "ك": "ک",  # ك -> ک
        "ة": "ه",  # ة -> ه
        "ؤ": "و",  # ؤ -> و
        **{chr(0x0660 + i): str(i) for i in range(10)},  # арабско-индийские цифры
        **{chr(0x06F0 + i): str(i) for i in range(10)},  # персидские цифры
    }
)
# Латинские буквы без разложения в NFD
_LATIN_SPECIAL = str.maketrans({"ı": "i", "ø": "o", "æ": "ae", "œ": "oe", "ł": "l", "đ": "d", "ð": "d", "þ": "th"})

_WS = re.compile(r"\s+")
_FA_MI = re.compile("(?<![\\w])(\u0646?\u0645\u06cc)\\s+(?=[\u0600-\u06ff])")


def _is_latin(ch: str) -> bool:
    cp = ord(ch)
    return cp < 0x0250 or 0x1E00 <= cp <= 0x1EFF


def _fold_latin(text: str) -> str:
    """Снимает диакритику только с латиницы (кириллицу «й», «ў» не трогаем)."""
    out = []
    for ch in text:
        if ch.isascii() or not _is_latin(ch):
            out.append(ch)
            continue
        decomposed = unicodedata.normalize("NFD", ch)
        out.append("".join(c for c in decomposed if not unicodedata.combining(c)))
    return "".join(out)


def fold_basic(text: str) -> str:
    """Базовая нормализация для сопоставления."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_INVISIBLE)
    text = text.translate(_APOSTROPHES)
    text = text.casefold()
    text = text.replace("ё", "е")
    text = _ARABIC_DIACRITICS.sub("", text)
    text = text.translate(_ARABIC_MAP)
    # Персидская приставка «می/نمی» пишется слитно, через ZWNJ или через пробел
    text = _FA_MI.sub(r"\1", text)
    text = text.translate(_LATIN_SPECIAL)
    text = _fold_latin(text)
    # U+0307 (точка сверху) остаётся после casefold турецкой «İ»
    text = text.replace("̇", "")
    return _WS.sub(" ", text).strip()


# --- Снятие обфускации -----------------------------------------------------

_LAT_TO_CYR = {
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "n": "п",
    "o": "о", "p": "р", "r": "р", "t": "т", "u": "и", "x": "х", "y": "у",
    "0": "о", "3": "з", "4": "ч", "6": "б", "@": "а",
}
_CYR_TO_LAT = {
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
}
_LEET_LAT = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"}

_TOKEN = re.compile(r"[\w@$!]+", re.UNICODE)
_SEP_LETTERS = re.compile(r"(?<![\w])((?:[^\W\d_][.\-_*·•|/\\+]+){2,}[^\W\d_])(?![\w])")
_SPACED_LETTERS = re.compile(r"(?<![\w])((?:[^\W\d_] ){3,}[^\W\d_])(?![\w])")
_REPEATS = re.compile(r"([a-zа-яәғқңөұүһіўҳӣӯҷ])\1{2,}")


def _is_cyr(ch: str) -> bool:
    return "Ѐ" <= ch <= "ԯ"


def _fix_token(token: str) -> tuple[str, set[str]]:
    # Символы по краям слова — пунктуация или упоминание (@handle), не leet
    lead = len(token) - len(token.lstrip("@$!"))
    trail = len(token) - len(token.rstrip("@$!"))
    if lead or trail:
        core = token[lead:len(token) - trail]
        fixed, used = _fix_token(core) if core else (core, set())
        return token[:lead] + fixed + token[len(token) - trail:], used
    techniques: set[str] = set()
    letters = [c for c in token if c.isalpha()]
    if not letters:
        return token, techniques
    cyr = sum(1 for c in letters if _is_cyr(c))
    lat = sum(1 for c in letters if c.isascii())
    has_digit_or_sym = any(c.isdigit() or c in "@$!" for c in token)

    if cyr and (lat or has_digit_or_sym) and cyr >= lat:
        fixed = "".join(_LAT_TO_CYR.get(c, c) if (c.isascii()) else c for c in token)
        if fixed != token:
            techniques.add("homoglyph" if lat else "leet")
            if has_digit_or_sym:
                techniques.add("leet")
        return fixed, techniques
    if lat and cyr and lat > cyr:
        fixed = "".join(_CYR_TO_LAT.get(c, c) for c in token)
        if fixed != token:
            techniques.add("homoglyph")
        token = fixed
    if lat and has_digit_or_sym and not cyr:
        # Цифры внутри латинского слова (k1ll, b0mb). Чистые числа не трогаем.
        if sum(1 for c in token if c.isalpha()) >= 2:
            fixed = "".join(_LEET_LAT.get(c, c) for c in token)
            if fixed != token:
                techniques.add("leet")
            token = fixed
    return token, techniques


@dataclass
class Normalized:
    original: str
    basic: str
    deobf: str
    techniques: set[str] = field(default_factory=set)

    @property
    def variants(self) -> list[str]:
        return [self.basic] if self.deobf == self.basic else [self.basic, self.deobf]


def deobfuscate(basic: str) -> tuple[str, set[str]]:
    techniques: set[str] = set()

    def join_sep(m: re.Match) -> str:
        techniques.add("separators")
        return re.sub(r"[.\-_*·•|/\\+ ]", "", m.group(1))

    text = _SEP_LETTERS.sub(join_sep, basic)
    text = _SPACED_LETTERS.sub(join_sep, text)

    def fix(m: re.Match) -> str:
        fixed, used = _fix_token(m.group(0))
        techniques.update(used)
        return fixed

    text = _TOKEN.sub(fix, text)

    def collapse(m: re.Match) -> str:
        techniques.add("stretching")
        return m.group(1)

    text = _REPEATS.sub(collapse, text)
    return text, techniques


def normalize(text: str) -> Normalized:
    basic = fold_basic(text)
    deobf, techniques = deobfuscate(basic)
    return Normalized(original=text, basic=basic, deobf=deobf, techniques=techniques if deobf != basic else set())


TECHNIQUE_LABELS = {
    "homoglyph": "смешение кириллицы и латиницы (омоглифы)",
    "leet": "цифры/символы вместо букв",
    "separators": "буквы через разделители",
    "stretching": "растянутые буквы",
}


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+(?:'\w+)*", text)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…。！？؟])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]
