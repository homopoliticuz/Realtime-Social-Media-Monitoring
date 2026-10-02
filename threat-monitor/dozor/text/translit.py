"""Транслитерация для языков региона.

Используется в двух направлениях:

* генерация латинских вариантов терминов лексикона (люди пишут по-русски
  латиницей «ub'yu», по-узбекски — и кириллицей, и латиницей);
* интерпретация запроса пользователя, набранного транслитом.

Все функции возвращают строки в нижнем регистре; диакритику латиницы
затем снимает ``normalize.fold_basic``.
"""

from __future__ import annotations

import re

# --- Русский ---------------------------------------------------------------

_RU_A = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch",
    "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_RU_B = {**_RU_A, "х": "kh", "ц": "ts", "щ": "shch", "й": "j", "ю": "ju", "я": "ja", "ь": "'"}
_RU_C = {**_RU_A, "ь": "'", "ж": "j", "х": "x"}


def ru_to_latin_variants(word: str) -> set[str]:
    word = word.lower().replace("ё", "е")
    out = set()
    for table in (_RU_A, _RU_B, _RU_C):
        out.add("".join(table.get(ch, ch) for ch in word))
    return {v for v in out if v and v != word}


_RU_REVERSE = [
    ("shch", "щ"), ("sch", "щ"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"),
    ("sh", "ш"), ("yu", "ю"), ("ju", "ю"), ("ya", "я"), ("ja", "я"), ("yo", "е"),
    ("jo", "е"), ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"), ("d", "д"), ("e", "е"),
    ("z", "з"), ("i", "и"), ("j", "й"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"),
    ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"),
    ("h", "х"), ("c", "ц"), ("y", "ы"), ("w", "в"), ("x", "кс"), ("q", "к"), ("'", "ь"),
]


def latin_to_ru(word: str) -> str:
    """Обратная транслитерация русского транслита (приблизительная)."""
    return _greedy(word.lower(), _RU_REVERSE)


# --- Узбекский (официальная латиница 1995 г.) -------------------------------

_UZ_C2L = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "s",
    "ч": "ch", "ш": "sh", "ъ": "'", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}
_UZ_VOWELS_CYR = set("аеёиоуэюяў")


def uz_cyr_to_lat(text: str) -> str:
    out = []
    prev = ""
    for ch in text.lower():
        if ch == "е" and (not prev or not prev.isalpha() or prev in _UZ_VOWELS_CYR or prev in "ъь"):
            out.append("ye")
        else:
            out.append(_UZ_C2L.get(ch, ch))
        prev = ch
    return "".join(out)


_UZ_L2C = [
    ("o'", "ў"), ("g'", "ғ"), ("sh", "ш"), ("ch", "ч"), ("ya", "я"), ("yu", "ю"),
    ("yo", "ё"), ("ye", "е"), ("ts", "ц"), ("a", "а"), ("b", "б"), ("d", "д"),
    ("e", "е"), ("f", "ф"), ("g", "г"), ("h", "ҳ"), ("i", "и"), ("j", "ж"), ("k", "к"),
    ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("q", "қ"), ("r", "р"),
    ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"), ("x", "х"), ("y", "й"), ("z", "з"),
    ("'", "ъ"),
]


def uz_lat_to_cyr(text: str) -> str:
    text = text.lower()
    # Начальная «e» в узбекской кириллице пишется как «э»
    text = re.sub(r"(?<![\w'])e", "э", text)
    return _greedy(text, _UZ_L2C)


def uz_latin_informal(word: str) -> set[str]:
    """Неформальные написания: без апострофов, o' -> u, g' -> g."""
    out = {word.replace("'", ""), word.replace("o'", "u").replace("g'", "g")}
    return {v for v in out if v and v != word}


# --- Казахский, киргизский, таджикский --------------------------------------

_KK_OFFICIAL = {
    "а": "a", "ә": "ä", "б": "b", "в": "v", "г": "g", "ғ": "ğ", "д": "d", "е": "e",
    "ё": "io", "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k", "қ": "q", "л": "l",
    "м": "m", "н": "n", "ң": "ñ", "о": "o", "ө": "ö", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ұ": "ū", "ү": "ü", "ф": "f", "х": "h", "һ": "h", "ц": "ts",
    "ч": "ç", "ш": "ş", "щ": "şş", "ъ": "", "ы": "y", "і": "ı", "ь": "", "э": "e",
    "ю": "iu", "я": "ia",
}
_CHAT_CYR = {
    "а": "a", "ә": "a", "б": "b", "в": "v", "г": "g", "ғ": "g", "д": "d", "е": "e",
    "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "қ": "k", "л": "l",
    "м": "m", "н": "n", "ң": "ng", "о": "o", "ө": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ұ": "u", "ү": "u", "ф": "f", "х": "h", "һ": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "і": "i", "ь": "", "э": "e",
    "ю": "yu", "я": "ya", "ҳ": "h", "ӣ": "i", "ӯ": "u", "ҷ": "j", "ў": "u",
}
_TG_LATIN = {**_CHAT_CYR, "ғ": "gh", "қ": "q", "ҳ": "h", "ҷ": "j", "х": "kh", "ъ": "'"}
_KY_LATIN = {**_CHAT_CYR, "ж": "j"}


def kk_to_latin_variants(word: str) -> set[str]:
    word = word.lower()
    return {"".join(_KK_OFFICIAL.get(c, c) for c in word), "".join(_CHAT_CYR.get(c, c) for c in word)}


def ky_to_latin_variants(word: str) -> set[str]:
    word = word.lower()
    return {"".join(_CHAT_CYR.get(c, c) for c in word), "".join(_KY_LATIN.get(c, c) for c in word)}


def tg_to_latin_variants(word: str) -> set[str]:
    word = word.lower()
    return {"".join(_TG_LATIN.get(c, c) for c in word), "".join(_CHAT_CYR.get(c, c) for c in word)}


# --- Туркменский и турецкий (латиница) --------------------------------------

_TK_CHAT = {"ş": "sh", "ç": "ch", "ž": "zh", "ň": "n", "ý": "y", "ä": "a", "ö": "o", "ü": "u"}
_TR_CHAT = {"ş": "sh", "ç": "ch", "ğ": "g", "ı": "i", "ö": "o", "ü": "u"}


def tk_latin_informal(word: str) -> set[str]:
    return {"".join(_TK_CHAT.get(c, c) for c in word.lower())}


def tr_latin_informal(word: str) -> set[str]:
    return {"".join(_TR_CHAT.get(c, c) for c in word.lower())}


# --- Общее ------------------------------------------------------------------

def _greedy(text: str, table: list[tuple[str, str]]) -> str:
    out = []
    i = 0
    while i < len(text):
        for src, dst in table:
            if text.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def is_cyrillic(word: str) -> bool:
    return any("Ѐ" <= ch <= "ԯ" for ch in word)


def variants_for(lang: str, term: str) -> set[str]:
    """Все дополнительные написания термина для данного языка."""
    if lang == "ru" and is_cyrillic(term):
        return ru_to_latin_variants(term)
    if lang == "uz":
        if is_cyrillic(term):
            lat = uz_cyr_to_lat(term)
            return {lat} | uz_latin_informal(lat)
        return {uz_lat_to_cyr(term)} | uz_latin_informal(term)
    if lang == "kk" and is_cyrillic(term):
        return kk_to_latin_variants(term)
    if lang == "ky" and is_cyrillic(term):
        return ky_to_latin_variants(term)
    if lang == "tg" and is_cyrillic(term):
        return tg_to_latin_variants(term)
    if lang == "tk":
        return tk_latin_informal(term) - {term}
    if lang == "tr":
        return tr_latin_informal(term) - {term}
    return set()
