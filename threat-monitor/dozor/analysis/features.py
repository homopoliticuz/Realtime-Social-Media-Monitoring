"""Извлечение наблюдаемых признаков из текста конкретного материала.

Все признаки — факты о тексте (какие слова и конструкции в нём есть),
а не выводы о человеке. Выводы делает ``classifier`` и явно помечает их
как аналитическую оценку.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..text.langid import LangGuess, detect
from ..text.lexicon import Hit, Lexicon, MarkerHit, get_lexicon
from ..text.normalize import Normalized, normalize

MARKER_TYPES = (
    "quote_verbs", "journalism", "condemnation", "fiction", "humor", "protest", "religious",
    "intent", "obligation", "conditional", "second_person", "third_person", "mass_target",
    "titles", "role_labels", "institution", "time", "place", "preparation", "groups", "idioms",
)

_QUOTE_PAIRS = [("«", "»"), ('"', '"'), ("“", "”"), ("„", "“"), ("「", "」"), ("『", "』")]
_HANDLE_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{3,32})")
_LINK_RE = re.compile(r"(?:https?://|t\.me/|vk\.com/|wa\.me/)\S+", re.IGNORECASE)
_NAME_PAIR_RE = re.compile(
    r"(?<![\w])([A-ZА-ЯЁЎҚҒҲӘӨҮҰІҢҶӢӮ][a-zа-яёўқғҳәөүұіңҷӣӯ'ʻ’-]+)\s+([A-ZА-ЯЁЎҚҒҲӘӨҮҰІҢҶӢӮ][a-zа-яёўқғҳәөүұіңҷӣӯ'ʻ’-]+)"
)
_VOCATIVE_RE = re.compile(
    r"(?:^|[,!?.:]\s*)([A-ZА-ЯЁЎҚҒҲӘӨҮҰІҢҶӢӮ][a-zа-яёўқғҳәөүұіңҷӣӯ'ʻ’-]{2,})(?=\s*[,!?])"
)
_EMOJI_HUMOR = set("😂🤣😆😹😜🤪😅")
# Обращения, которые не являются именами
_NOT_NAMES = {
    "братья", "брат", "друзья", "люди", "ребята", "пацаны", "народ", "все", "мужики", "сестры",
    "brothers", "brother", "friends", "guys", "people", "everyone", "folks",
    "birodarlar", "do'stlar", "aka", "bratan", "бауырлар", "достар", "агайындар", "бородарон", "дӯстон",
    "lol", "ok", "окей", "завтра", "сегодня", "ертең", "ertaga", "фардо", "tomorrow", "today",
    "requiem", "amen", "аминь", "omin", "иншаллах", "inshallah", "alhamdulillah",
}
# Первые слова предложения, которые не образуют имя в паре
_NOT_NAME_FIRST = {
    "мвд", "в", "на", "по", "завтра", "сегодня", "все", "это", "уже", "мы", "я", "он", "она",
    "the", "this", "we", "i", "police", "breaking", "требуем", "пора", "нужны", "братья",
}


@dataclass
class TargetInfo:
    named: list[str] = field(default_factory=list)
    handles: list[str] = field(default_factory=list)
    second_person: bool = False
    third_person: bool = False
    institutions: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    mass: bool = False

    @property
    def person(self) -> bool:
        return bool(self.named or self.handles or self.second_person or self.titles)

    @property
    def specific(self) -> bool:
        return bool(self.named or self.handles or self.second_person or self.institutions or self.titles)

    @property
    def any(self) -> bool:
        return self.specific or self.third_person or bool(self.groups) or bool(self.labels) or self.mass

    def describe(self) -> list[str]:
        out = []
        if self.named:
            out.append("названное лицо: " + ", ".join(self.named[:3]))
        if self.handles:
            out.append("упомянутый аккаунт: " + ", ".join("@" + h for h in self.handles[:3]))
        if self.second_person:
            out.append("обращение во 2-м лице («ты/вы»)")
        if self.titles:
            out.append("должность/роль: " + ", ".join(self.titles[:3]))
        if self.institutions:
            out.append("учреждение/объект: " + ", ".join(self.institutions[:3]))
        if self.groups:
            out.append("группа людей: " + ", ".join(self.groups[:3]))
        if self.labels:
            out.append("люди, обозначенные ярлыком: " + ", ".join(self.labels[:3]))
        if self.mass:
            out.append("неопределённо широкий круг («всех»)")
        if self.third_person and not out:
            out.append("лицо, обозначенное местоимением")
        return out


@dataclass
class Features:
    text: str
    norm: Normalized
    lang: LangGuess
    hits: list[Hit]
    markers: dict[str, list[MarkerHit]]
    target: TargetInfo
    quoted_spans: list[tuple[int, int]]
    contact_handles: list[str]
    links: list[str]
    humor_emoji: bool

    # ----------------------------------------------------------- выборки
    def by_group(self, group: str, include_negated: bool = False) -> list[Hit]:
        return [h for h in self.hits if h.group == group and (include_negated or not h.negated)]

    def by_concept(self, *concepts: str) -> list[Hit]:
        return [h for h in self.hits if h.concept in concepts and not h.negated]

    def has(self, marker: str) -> bool:
        return bool(self.markers.get(marker))

    def marker_terms(self, marker: str, limit: int = 4) -> list[str]:
        out: list[str] = []
        for m in self.markers.get(marker, []):
            if m.surface not in out:
                out.append(m.surface)
        return out[:limit]

    def in_quotes(self, hit: Hit) -> bool:
        return any(s <= hit.start and hit.end <= e for s, e in self.quoted_spans)

    def idiom_covered(self, hit: Hit) -> bool:
        return any(m.start <= hit.end and hit.start <= m.end for m in self.markers.get("idioms", []))

    def near(self, hit: Hit, marker: str, before: int = 60, after: int = 60) -> list[MarkerHit]:
        return [m for m in self.markers.get(marker, []) if hit.start - before <= m.start <= hit.end + after]

    @property
    def text_len(self) -> int:
        return len(self.norm.basic)

    @property
    def obfuscated(self) -> bool:
        return any(h.obfuscated for h in self.hits)


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for open_q, close_q in _QUOTE_PAIRS:
        pos = 0
        while True:
            s = text.find(open_q, pos)
            if s < 0:
                break
            e = text.find(close_q, s + 1)
            if e < 0:
                break
            spans.append((s, e + 1))
            pos = e + 1
    return spans


def _names(original: str) -> list[str]:
    names: list[str] = []
    for m in _NAME_PAIR_RE.finditer(original):
        first, second = m.group(1), m.group(2)
        if first.lower() in _NOT_NAME_FIRST or first.isupper() or second.isupper():
            continue
        names.append(f"{first} {second}")
    for m in _VOCATIVE_RE.finditer(original):
        word = m.group(1)
        if word.lower() in _NOT_NAMES or word.isupper():
            continue
        if not any(word in n for n in names):
            names.append(word)
    return names


def _unique(values) -> list[str]:
    return list(dict.fromkeys(values))


def extract(text: str, lexicon: Lexicon | None = None, lang: LangGuess | None = None) -> Features:
    lex = lexicon or get_lexicon()
    norm = normalize(text)
    guess = lang or detect(text)
    hits = lex.find_concepts(norm, guess.lang)
    markers = {m: lex.find_markers(norm.deobf, m, guess.lang) for m in MARKER_TYPES}

    handles = _HANDLE_RE.findall(text or "")
    contact_handles = [h for h in handles if h.lower().endswith("bot") or h.lower().endswith("_bot")]
    target_handles = [h for h in handles if h not in contact_handles]
    target = TargetInfo(
        named=_names(text or ""),
        handles=target_handles,
        second_person=bool(markers["second_person"]),
        third_person=bool(markers["third_person"]),
        institutions=_unique(m.surface for m in markers["institution"]),
        titles=_unique(m.surface for m in markers["titles"]),
        groups=_unique(m.surface for m in markers["groups"]),
        labels=_unique(m.surface for m in markers["role_labels"]),
        mass=bool(markers["mass_target"]),
    )
    return Features(
        text=text or "",
        norm=norm,
        lang=guess,
        hits=hits,
        markers=markers,
        target=target,
        quoted_spans=_quoted_spans(norm.deobf),
        contact_handles=contact_handles,
        links=_LINK_RE.findall(text or ""),
        humor_emoji=any(ch in _EMOJI_HUMOR for ch in (text or "")),
    )
