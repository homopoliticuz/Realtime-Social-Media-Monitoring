"""Компиляция лексикона в регулярные выражения и поиск совпадений."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import translit
from .lexicon_data import CONCEPTS, NEGATION_EXCLUDE, NEGATION_SUFFIXES, SUFFIX_FORMS
from .markers_data import MARKERS, NEGATORS
from .normalize import Normalized, fold_basic

# Языки без пробелов между словами или с присоединёнными частицами:
# сопоставление по подстроке.
SUBSTRING_LANGS = {"zh", "ja", "ko", "ar", "fa"}
# Для маркеров арабской графики используем границы слов (иначе короткие
# слова вроде «تو» находятся внутри любых других).
MARKER_SUBSTRING_LANGS = {"zh", "ja", "ko"}

FORM_LABELS = {"b": "нейтральная форма", "f": "намерение 1-го лица / будущее время", "i": "повелительное наклонение / призыв"}


@dataclass(frozen=True)
class Hit:
    concept: str
    group: str
    lang: str
    term: str  # исходный термин лексикона
    surface: str  # найденная строка в нормализованном тексте
    start: int
    end: int
    form: str  # b | f | i
    obfuscated: bool = False
    negated: bool = False

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class MarkerHit:
    marker: str
    lang: str
    surface: str
    start: int
    end: int


def _term_to_regex(term: str, lang: str, substring: bool) -> tuple[str, bool]:
    """Возвращает (шаблон, is_verb_stem)."""
    if term.startswith("re:"):
        return term[3:], False
    verb_stem = term.endswith("+")
    stem = term.endswith("*") or verb_stem
    body = fold_basic(term[:-1] if stem else term)
    parts = [re.escape(p) for p in body.split(" ") if p]
    sep = r"\s*" if substring else r"\s+"
    pattern = sep.join(parts)
    if stem:
        pattern += r"\w*"
    if substring:
        return pattern, verb_stem
    return r"(?<![\w'])" + pattern + (r"(?![\w'])" if not stem else ""), verb_stem


def _expand_variants(lang: str, term: str) -> list[str]:
    if term.startswith("re:"):
        return [term]
    suffix = ""
    core = term
    if term.endswith(("*", "+")):
        suffix, core = term[-1], term[:-1]
    out = [term]
    for variant in sorted(translit.variants_for(lang, core)):
        out.append(variant + suffix)
    return out


@dataclass
class _Compiled:
    concept: str
    group: str
    lang: str
    form: str
    regex: re.Pattern
    terms: list[str]  # исходный термин по номеру группы
    variants: list[str]  # конкретное написание (транслит и т. п.) по номеру группы
    verb_stem: list[bool]


def _load_extra() -> dict:
    path = os.environ.get("DOZOR_LEXICON_EXTRA")
    if not path or not Path(path).is_file():
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _merged_concepts() -> dict:
    concepts = {k: {**v, "terms": {lang: {f: list(ts) for f, ts in forms.items()} for lang, forms in v["terms"].items()}} for k, v in CONCEPTS.items()}
    for cid, spec in _load_extra().items():
        target = concepts.setdefault(cid, {"label": spec.get("label", cid), "group": spec.get("group", "violence"), "severity": spec.get("severity", "significant"), "terms": {}})
        for lang, forms in spec.get("terms", {}).items():
            for form, terms in forms.items():
                target["terms"].setdefault(lang, {}).setdefault(form, []).extend(terms)
    return concepts


class Lexicon:
    def __init__(self) -> None:
        self.concepts = _merged_concepts()
        self._compiled: list[_Compiled] = []
        self._markers: dict[str, list[tuple[str, re.Pattern]]] = {}
        self._surface_index: dict[str, set[str]] = {}
        self._compile()

    # ------------------------------------------------------------ компиляция
    def _compile(self) -> None:
        for cid, spec in self.concepts.items():
            for lang, forms in spec["terms"].items():
                substring = lang in SUBSTRING_LANGS
                for form, terms in forms.items():
                    patterns, canon, variants, stems = [], [], [], []
                    for term in terms:
                        for variant in _expand_variants(lang, term):
                            pat, is_verb = _term_to_regex(variant, lang, substring)
                            patterns.append(f"({pat})")
                            canon.append(term)
                            variants.append(variant)
                            stems.append(is_verb)
                            if not variant.startswith("re:"):
                                key = fold_basic(variant.rstrip("*+"))
                                self._surface_index.setdefault(key, set()).add(cid)
                    if patterns:
                        self._compiled.append(
                            _Compiled(cid, spec["group"], lang, form, re.compile("|".join(patterns)), canon, variants, stems)
                        )
        for marker, by_lang in MARKERS.items():
            compiled = []
            for lang, terms in by_lang.items():
                substring = lang in MARKER_SUBSTRING_LANGS
                pats = []
                for term in terms:
                    for variant in _expand_variants(lang, term):
                        pats.append(_term_to_regex(variant, lang, substring)[0])
                if pats:
                    compiled.append((lang, re.compile("|".join(f"(?:{p})" for p in pats))))
            self._markers[marker] = compiled

    # --------------------------------------------------------------- поиск
    def find_concepts(self, norm: Normalized, detected_lang: str = "und") -> list[Hit]:
        hits = self._scan(norm.deobf, detected_lang)
        if norm.deobf != norm.basic:
            plain = {(h.concept, h.surface) for h in self._scan(norm.basic, detected_lang)}
            hits = [
                Hit(h.concept, h.group, h.lang, h.term, h.surface, h.start, h.end, h.form, (h.concept, h.surface) not in plain, h.negated)
                for h in hits
            ]
        return hits

    def _scan(self, text: str, detected_lang: str) -> list[Hit]:
        raw: list[Hit] = []
        for comp in self._compiled:
            for m in comp.regex.finditer(text):
                idx = (m.lastindex or 1) - 1
                term = comp.terms[idx]
                surface = m.group(0)
                if not _plausible(comp.lang, surface, detected_lang):
                    continue
                form = comp.form
                negated = False
                if comp.verb_stem[idx]:
                    stem = fold_basic(comp.variants[idx][:-1])
                    form, negated = _stem_form(comp.lang, stem, surface, form)
                if not negated:
                    negated = _negated_context(text, m.start(), m.end(), comp.lang)
                raw.append(Hit(comp.concept, comp.group, comp.lang, term, surface, m.start(), m.end(), form, False, negated))
        return _resolve_overlaps(raw)

    def find_markers(self, text: str, marker: str, detected_lang: str = "und") -> list[MarkerHit]:
        out = []
        for lang, regex in self._markers.get(marker, []):
            for m in regex.finditer(text):
                if not m.group(0).strip():
                    continue
                if not _plausible(lang, m.group(0), detected_lang):
                    continue
                out.append(MarkerHit(marker, lang, m.group(0), m.start(), m.end()))
        out.sort(key=lambda h: h.start)
        return out

    # ------------------------------------------------- для расширения запроса
    def concepts_for_text(self, query_norm: str) -> set[str]:
        """Понятия, к которым относится слово/фраза запроса."""
        found = set(self._surface_index.get(query_norm, set()))
        if found:
            return found
        # Совпадение со словоформой: запускаем полный поиск по самой фразе
        from .normalize import normalize as _normalize

        for hit in self.find_concepts(_normalize(query_norm)):
            if hit.start == 0 and hit.end >= len(query_norm) - 1:
                found.add(hit.concept)
        return found

    def display_terms(self, concept: str, lang: str, limit: int = 8) -> list[str]:
        forms = self.concepts.get(concept, {}).get("terms", {}).get(lang, {})
        seen: list[str] = []
        for form in ("f", "i", "b"):
            for term in forms.get(form, []):
                if term.startswith("re:"):
                    continue
                clean = term.rstrip("*+")
                if clean not in seen:
                    seen.append(clean)
        return seen[:limit]

    def search_terms(self, concept: str, lang: str, limit: int = 6) -> list[str]:
        """Термины, пригодные для отправки в API платформы (без масок)."""
        forms = self.concepts.get(concept, {}).get("terms", {}).get(lang, {})
        out: list[str] = []
        for form in ("b", "f", "i"):
            for term in forms.get(form, []):
                if term.startswith("re:"):
                    continue
                clean = term.rstrip("*+")
                if clean and clean not in out:
                    out.append(clean)
        return out[:limit]

    def coverage(self) -> dict[str, dict[str, int]]:
        cov: dict[str, dict[str, int]] = {}
        for cid, spec in self.concepts.items():
            for lang, forms in spec["terms"].items():
                cov.setdefault(lang, {})[cid] = sum(len(v) for v in forms.values())
        return cov


# Языки, между которыми в регионе часто переключаются внутри одного текста
_COMPATIBLE = {
    "ru": {"uz", "kk", "ky", "tg", "en"},
    "uz": {"ru", "kk", "ky", "tg", "tk", "en"},
    "kk": {"ru", "uz", "ky", "en"},
    "ky": {"ru", "uz", "kk", "en"},
    "tg": {"ru", "uz", "fa", "en"},
    "tk": {"ru", "uz", "tr"},
    "tr": {"tk", "uz"},
}


def _plausible(lang: str, surface: str, detected: str) -> bool:
    """Отсекает короткие совпадения из «чужого» языка (англ. gun в тур. gün)."""
    if detected in ("und", lang) or lang in SUBSTRING_LANGS:
        return True
    if lang in _COMPATIBLE.get(detected, {"en"}):
        return True
    return sum(1 for c in surface if c.isalpha()) >= 5


def _stem_form(lang: str, stem: str, surface: str, default: str) -> tuple[str, bool]:
    if not surface.startswith(stem):
        return default, False
    suffix = surface[len(stem):]
    if lang == "uz" and translit.is_cyrillic(suffix):
        suffix = translit.uz_cyr_to_lat(suffix)
    elif lang in ("kk", "ky", "tg") and suffix and not translit.is_cyrillic(suffix):
        return default, False
    negs = NEGATION_SUFFIXES.get(lang, ())
    excl = NEGATION_EXCLUDE.get(lang, ())
    if suffix and suffix.startswith(negs) and not suffix.startswith(excl):
        return default, True
    for affix, form in SUFFIX_FORMS.get(lang, []):
        if (affix == "" and suffix == "") or (affix and suffix.startswith(affix)):
            return form, False
    return default, False


_TOKEN_RE = re.compile(r"[\w']+")


def _negated_context(text: str, start: int, end: int, lang: str) -> bool:
    if lang == "zh":
        return start > 0 and text[start - 1] in "不没别勿"
    if lang == "fa":
        return start > 0 and text[start - 1] == "ن"
    if lang == "ja":
        return bool(re.match(r"\S{0,3}(?:ない|ません|ず)", text[end:end + 8]))
    negators = NEGATORS.get(lang)
    if not negators:
        return False
    window = _TOKEN_RE.findall(text[max(0, start - 40):start])[-3:]
    return any(tok in negators for tok in window)


def _resolve_overlaps(hits: list[Hit]) -> list[Hit]:
    """Убирает совпадения, целиком покрытые более длинным совпадением.

    Для одинаковых интервалов одного понятия оставляет более специфичную
    форму (намерение/призыв важнее нейтральной).
    """
    rank = {"f": 0, "i": 1, "b": 2}
    hits = sorted(hits, key=lambda h: (h.start, -h.length, rank.get(h.form, 3)))
    kept: list[Hit] = []
    for h in hits:
        covered = False
        for k in kept:
            if k.start <= h.start and h.end <= k.end and (k.length > h.length or k.concept == h.concept):
                covered = True
                break
        if not covered:
            kept.append(h)
    return kept


@lru_cache(maxsize=1)
def get_lexicon() -> Lexicon:
    return Lexicon()
