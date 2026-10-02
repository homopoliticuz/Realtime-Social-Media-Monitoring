"""Многоязычное расширение поискового запроса.

Пользователь вводит запрос на любом языке («убить», «kill», «o'ldirish»,
транслитом «ubit»). Каждое слово сопоставляется с понятием лексикона, и
поиск параллельно выполняется на всех выбранных языках: по умолчанию —
русский, английский, французский, немецкий, испанский, итальянский,
корейский, японский, китайский, персидский, арабский (п. 9) и языки
региона (узбекский в обеих графиках, казахский, киргизский, таджикский,
туркменский) — с транслитерацией.

Слова, которых нет в лексиконе, ищутся буквально и в транслитерации; если
подключён LLM-переводчик, он предлагает переводы, помеченные как машинные.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import translit
from .langid import LANGUAGES, PARALLEL_DEFAULT, REGIONAL
from .lexicon import Lexicon, get_lexicon
from .normalize import Normalized, fold_basic

TOPICS: dict[str, dict] = {
    "violence_threats": {
        "label": "Угрозы насилия",
        "concepts": ["kill", "explode", "shoot", "stab", "burn", "attack", "beat", "death_to", "destroy_group", "veiled_threat"],
        "categories": ["direct_threat", "call_to_violence"],
    },
    "terrorism_recruitment": {
        "label": "Терроризм и вербовка",
        "concepts": ["explode", "attack", "recruit_join", "recruit_travel", "ideology"],
        "categories": ["recruitment"],
    },
    "ethnic_religious_incitement": {
        "label": "Подстрекательство по национальному/религиозному признаку",
        "concepts": ["dehumanize", "expel", "destroy_group", "death_to"],
        "categories": ["incitement"],
    },
    "harassment": {
        "label": "Травля",
        "concepts": ["kys", "doxxing", "mass_call", "insult"],
        "categories": ["harassment"],
    },
    "dangerous_involvement": {
        "label": "Опасное вовлечение (в т. ч. несовершеннолетних)",
        "concepts": ["paid_task", "sabotage", "challenge", "school_attack_glorification"],
        "categories": ["dangerous_involvement"],
    },
}

_STOP = set("и или в на с по для от до а но the and or of to in on for de la le el и/или".split())


@dataclass
class TermExpansion:
    query_term: str
    method: str  # lexicon | topic | llm | literal
    concepts: list[str] = field(default_factory=list)
    concept_labels: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)  # для тем: категории оценки, тоже дающие совпадение
    by_lang: dict[str, list[str]] = field(default_factory=dict)
    literal_variants: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Expansion:
    original: str
    languages: list[str]
    mode: str  # all | any
    terms: list[TermExpansion]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "languages": self.languages,
            "language_labels": {l: LANGUAGES.get(l, l) for l in self.languages},
            "mode": self.mode,
            "terms": [t.to_dict() for t in self.terms],
            "warnings": self.warnings,
            "variant_count": self.variant_count,
        }

    @property
    def variant_count(self) -> int:
        return sum(sum(len(v) for v in t.by_lang.values()) + len(t.literal_variants) for t in self.terms)

    @property
    def concept_ids(self) -> set[str]:
        return {c for t in self.terms for c in t.concepts}

    # --------------------------------------------------- запросы к платформам
    def platform_queries(self, max_queries: int, languages: list[str] | None = None) -> list[tuple[str, str]]:
        """Список (язык, строка запроса) с чередованием языков.

        При ограниченном бюджете запросов сначала покрываются все языки по
        одному термину, затем — следующие термины.
        """
        langs = languages or self.languages
        per_lang: dict[str, list[str]] = {l: [] for l in langs}
        for t in self.terms:
            for lang in langs:
                for term in t.by_lang.get(lang, []):
                    if term not in per_lang[lang]:
                        per_lang[lang].append(term)
            for lit in t.literal_variants:
                lang = "und"
                per_lang.setdefault(lang, [])
                if lit not in per_lang[lang]:
                    per_lang[lang].append(lit)
        out: list[tuple[str, str]] = []
        depth = 0
        while len(out) < max_queries:
            added = False
            for lang, terms in per_lang.items():
                if depth < len(terms):
                    out.append((lang, terms[depth]))
                    added = True
                    if len(out) >= max_queries:
                        break
            if not added:
                break
            depth += 1
        return out

    def total_platform_terms(self) -> int:
        return len(self.platform_queries(10_000))

    # -------------------------------------------------- локальная проверка
    def matcher(self, lexicon: Lexicon | None = None) -> Callable[..., list[str]]:
        """Функция, возвращающая термины запроса, найденные в материале.

        ``hits`` — уже вычисленные совпадения лексикона (чтобы не искать дважды);
        ``category`` — категория оценки: тема совпадает и тогда, когда
        классификатор отнёс материал к её категории (например, «приду к школе
        с ружьём» — угроза без глагола насилия).
        """
        lex = lexicon or get_lexicon()
        literal_res = {
            t.query_term: [re.compile(r"(?<![\w'])" + re.escape(fold_basic(v)) + r"\w*") for v in t.literal_variants if v]
            for t in self.terms
        }

        def match(norm: Normalized, hits: list | None = None, category: str | None = None) -> list[str]:
            if hits is None:
                hits = lex.find_concepts(norm)
            present = {h.concept for h in hits if not h.negated}
            matched = []
            for t in self.terms:
                ok = bool(set(t.concepts) & present) or (category is not None and category in t.categories)
                if not ok:
                    for rx in literal_res.get(t.query_term, []):
                        if rx.search(norm.basic) or rx.search(norm.deobf):
                            ok = True
                            break
                if ok:
                    matched.append(t.query_term)
            return matched

        return match

    def is_match(self, matched_terms: list[str]) -> bool:
        if not self.terms:
            return True
        if self.mode == "any":
            return bool(matched_terms)
        return len(set(matched_terms)) == len(self.terms)


def _split_query(query: str) -> list[str]:
    parts = re.findall(r'"([^"]+)"|«([^»]+)»|(\S+)', query or "")
    terms = []
    for a, b, c in parts:
        term = (a or b or c).strip(" ,.;:!?")
        if term and term.lower() not in _STOP and term.upper() != "OR":
            terms.append(term)
    return terms


def _literal_variants(term: str) -> list[str]:
    folded = fold_basic(term)
    out = [folded]
    if translit.is_cyrillic(folded):
        out += sorted(translit.ru_to_latin_variants(folded))
        out.append(translit.uz_cyr_to_lat(folded))
    else:
        out.append(translit.latin_to_ru(folded))
        out.append(translit.uz_lat_to_cyr(folded))
    seen = []
    for v in out:
        v = fold_basic(v)
        if v and v not in seen:
            seen.append(v)
    return seen


def expand_query(
    query: str,
    languages: list[str] | None = None,
    topics: list[str] | None = None,
    mode: str = "all",
    translator: Callable[[str, list[str]], dict[str, list[str]]] | None = None,
    lexicon: Lexicon | None = None,
) -> Expansion:
    lex = lexicon or get_lexicon()
    langs = list(dict.fromkeys(languages or (PARALLEL_DEFAULT + REGIONAL)))
    terms: list[TermExpansion] = []
    warnings: list[str] = []

    for topic in topics or []:
        spec = TOPICS.get(topic)
        if not spec:
            warnings.append(f"Неизвестная тема: {topic}")
            continue
        te = TermExpansion(query_term=f"тема: {spec['label']}", method="topic", concepts=list(spec["concepts"]),
                           categories=list(spec.get("categories", [])))
        _fill_langs(te, lex, langs)
        terms.append(te)
    if topics and len(terms) > 1:
        # Несколько тем объединяются по «ИЛИ»
        mode = "any" if not query.strip() else mode

    for raw in _split_query(query):
        folded = fold_basic(raw)
        concepts = sorted(lex.concepts_for_text(folded))
        if not concepts and not translit.is_cyrillic(folded):
            # Транслит русского: «ubit» -> «убит»
            concepts = sorted(lex.concepts_for_text(fold_basic(translit.latin_to_ru(folded))))
        if concepts:
            te = TermExpansion(query_term=raw, method="lexicon", concepts=concepts)
            _fill_langs(te, lex, langs)
            te.literal_variants = []
            terms.append(te)
            continue
        te = TermExpansion(query_term=raw, method="literal", literal_variants=_literal_variants(raw))
        if translator is not None:
            try:
                translated = translator(raw, langs)
            except Exception as exc:  # noqa: BLE001 — сбой переводчика не должен ломать поиск
                translated = {}
                warnings.append(f"Машинный перевод для «{raw}» недоступен: {exc}")
            if translated:
                te.method = "llm"
                te.by_lang = {l: [w for w in v if w][:4] for l, v in translated.items() if l in langs and v}
                te.literal_variants += [fold_basic(w) for v in te.by_lang.values() for w in v]
                te.note = "Переводы предложены языковой моделью и требуют проверки аналитиком."
        if te.method == "literal":
            te.note = (
                "Слова нет в многоязычном лексиконе: ищется буквально и в транслитерации. "
                "Для переводов на другие языки дополните лексикон или подключите переводчик."
            )
            warnings.append(f"«{raw}»: нет словарного перевода — поиск только по написанию и транслитерации.")
        terms.append(te)

    return Expansion(original=query, languages=langs, mode=mode if mode in ("all", "any") else "all", terms=terms, warnings=warnings)


def _fill_langs(te: TermExpansion, lex: Lexicon, langs: list[str]) -> None:
    te.concept_labels = [lex.concepts[c]["label"] for c in te.concepts if c in lex.concepts]
    for lang in langs:
        words: list[str] = []
        for cid in te.concepts:
            for w in lex.search_terms(cid, lang, limit=4):
                if w not in words:
                    words.append(w)
        if words:
            te.by_lang[lang] = words
        # Транслитерационные написания (узбекская кириллица/латиница и т. п.)
        if lang in ("uz", "ru", "kk", "ky", "tg") and words:
            extra = []
            for w in words[:3]:
                for v in sorted(translit.variants_for(lang, w)):
                    if v not in words and v not in extra:
                        extra.append(v)
            if extra:
                te.by_lang[lang] = words + extra[:4]


def preview(query: str, languages: list[str] | None = None) -> dict:
    return expand_query(query, languages).to_dict()
