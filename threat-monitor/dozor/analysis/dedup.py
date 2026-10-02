"""Устранение дублей и поиск похожих текстов.

* Точный дубль — одинаковый текст после нормализации (SHA-256).
* Похожий текст — SimHash по символьным 4-граммам (расстояние Хэмминга) с
  подтверждением коэффициентом Жаккара.

Сходство текста — техническая характеристика. Оно НЕ является
доказательством связи, сотрудничества, общей идеологии или принадлежности
авторов к организации и не превращается в связь на карте распространения.
"""

from __future__ import annotations

import hashlib
import re

from ..text.normalize import fold_basic

_URL = re.compile(r"https?://\S+")


def canonical(text: str) -> str:
    text = _URL.sub(" ", text or "")
    text = fold_basic(text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(canonical(text).encode("utf-8")).hexdigest()


def evidence_hash(text: str) -> str:
    """Хэш исходного текста как он получен (цепочка хранения доказательств)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _shingles(text: str, k: int = 4) -> set[str]:
    t = canonical(text)
    if len(t) <= k:
        return {t} if t else set()
    return {t[i:i + k] for i in range(len(t) - k + 1)}


def simhash(text: str, bits: int = 64) -> int:
    vec = [0] * bits
    for sh in _shingles(text):
        h = int.from_bytes(hashlib.blake2b(sh.encode("utf-8"), digest_size=8).digest(), "big")
        for i in range(bits):
            vec[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(bits):
        if vec[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def jaccard(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def near_duplicate(a_text: str, b_text: str, a_hash: int | None = None, b_hash: int | None = None,
                   max_distance: int = 12, min_jaccard: float = 0.6) -> tuple[bool, float]:
    """(похожи ли тексты, коэффициент Жаккара)."""
    if len(canonical(a_text)) < 25 or len(canonical(b_text)) < 25:
        return False, 0.0
    ha = a_hash if a_hash is not None else simhash(a_text)
    hb = b_hash if b_hash is not None else simhash(b_text)
    if hamming(ha, hb) > max_distance:
        return False, 0.0
    j = jaccard(a_text, b_text)
    return j >= min_jaccard, round(j, 3)
