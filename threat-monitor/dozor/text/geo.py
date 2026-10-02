"""География, упомянутая в самом материале.

Система фиксирует только явные упоминания стран и городов в тексте и
поясняет, к чему относится упоминание. Местонахождение, происхождение или
гражданство автора НЕ определяются — ни по тексту, ни по метаданным.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

from .normalize import fold_basic, split_sentences

COUNTRY_NAMES = {
    "UZ": "Узбекистан", "KZ": "Казахстан", "KG": "Кыргызстан", "TJ": "Таджикистан",
    "TM": "Туркменистан", "AF": "Афганистан", "RU": "Россия", "CN": "Китай", "IR": "Иран",
    "TR": "Турция", "PK": "Пакистан", "SY": "Сирия", "IQ": "Ирак", "UA": "Украина",
    "US": "США", "DE": "Германия", "FR": "Франция", "GB": "Великобритания",
    "KR": "Республика Корея", "JP": "Япония", "SA": "Саудовская Аравия", "AZ": "Азербайджан",
}

# (код страны, отображаемое имя, шаблоны). Шаблоны — основы с «*» или «re:».
_COUNTRY_PATTERNS = {
    "UZ": ["узбекистан*", "o'zbekiston*", "uzbekiston*", "uzbekistan", "ouzbekistan", "usbekistan", "өзбекстан*", "özbegistan*", "özbekistan*", "ӯзбекистон*", "우즈베키스탄", "ウズベキスタン", "乌兹别克斯坦", "ازبکستان", "أوزبكستان"],
    "KZ": ["казахстан*", "qozog'iston*", "kazakhstan", "kasachstan", "қазақстан*", "qazaqstan*", "kazakistan", "gazagystan*", "қазоқистон*", "카자흐스탄", "カザフスタン", "哈萨克斯坦", "قزاقستان", "كازاخستان"],
    "KG": ["кыргызстан*", "киргизи*", "qirg'iziston*", "kyrgyzstan", "kirghizistan", "kirgisistan", "қырғызстан*", "kırgızistan", "gyrgyzystan*", "қирғизистон*", "키르기스스탄", "キルギス", "吉尔吉斯斯坦", "قرقیزستان", "قيرغيزستان"],
    "TJ": ["таджикистан*", "tojikiston*", "tajikistan", "tadjikistan", "tadschikistan", "тәжікстан*", "тажикстан*", "тоҷикистон*", "tacikistan", "täjigistan*", "타지키스탄", "タジキスタン", "塔吉克斯坦", "تاجیکستان", "طاجيكستان"],
    "TM": ["туркменистан*", "turkmaniston*", "turkmenistan", "түрікменстан*", "түркмөнстан*", "туркманистон*", "türkmenistan*", "투르크메니스탄", "トルクメニスタン", "土库曼斯坦", "ترکمنستان", "تركمانستان"],
    "AF": ["афганистан*", "afg'oniston*", "afghanistan", "afganistan", "ауғанстан*", "ооганстан*", "афғонистон*", "아프가니스탄", "アフガニスタン", "阿富汗", "افغانستان"],
    "RU": ["росси*", "rossiya*", "russia", "russie", "russland", "rusia", "ресей*", "орусия*", "русия*", "rusya", "러시아", "ロシア", "俄罗斯", "روسیه", "روسيا"],
    "CN": ["китай", "китае", "китая", "китаю", "xitoy*", "china", "chine", "қытай*", "кытай*", "중국", "中国", "الصين"],
    "IR": ["иран", "ирана", "иране", "eron*", "iran", "ایران", "إيران"],
    "TR": ["турци*", "turkiya*", "turkey", "turquie", "turkei", "turquia", "түркия*", "туркия*", "turkiye", "튀르키예", "トルコ", "土耳其", "ترکیه", "تركيا"],
    "PK": ["пакистан*", "pokiston*", "pakistan", "پاکستان", "باكستان"],
    "SY": ["сири*", "suriya*", "syria", "syrie", "syrien", "siria", "suriye", "시리아", "シリア", "叙利亚", "سوریه", "سوريا"],
    "IQ": ["ирак", "ирака", "ираке", "iroq*", "iraq", "irak", "عراق", "العراق"],
    "UA": ["re:\\bукраин(?:а|е|ы|у|ой)\\b", "ukraina*", "ukraine", "ukrayna", "우크라이나", "ウクライナ", "乌克兰", "اوکراین", "أوكرانيا"],
    "US": ["сша", "usa", "united states", "etats-unis", "vereinigte staaten", "estados unidos", "америк*", "amerika*", "미국", "アメリカ", "美国", "آمریکا", "أمريكا"],
    "DE": ["германи*", "germaniya*", "germany", "allemagne", "deutschland", "alemania", "germania", "독일", "ドイツ", "德国", "آلمان", "ألمانيا"],
    "FR": ["франци*", "fransiya*", "france", "frankreich", "francia", "프랑스", "フランス", "法国", "فرانسه", "فرنسا"],
    "GB": ["великобритани*", "англи*", "buyuk britaniya*", "united kingdom", "britain", "england", "영국", "イギリス", "英国", "انگلیس", "بريطانيا"],
    "KR": ["южн* коре*", "janubiy koreya*", "south korea", "corée du sud", "südkorea", "한국", "대한민국", "韓国", "韩国"],
    "JP": ["япони*", "yaponiya*", "japan", "japon", "giappone", "일본", "日本", "ژاپن", "اليابان"],
    "SA": ["саудовск* арави*", "saudiya arabistoni*", "saudi arabia", "arabie saoudite", "السعودية", "عربستان"],
    "AZ": ["азербайджан", "азербайджана", "азербайджане", "ozarbayjon*", "azerbaijan", "azerbaycan"],
}

# Города: (страна, шаблоны, чувствительно к регистру)
_CITY_PATTERNS: dict[str, tuple[str, list[str], bool]] = {
    "Ташкент": ("UZ", ["ташкент*", "toshkent*", "тошкент*", "tashkent", "tachkent", "taschkent", "타슈켄트", "タシケント", "塔什干", "تاشکند", "طشقند"], False),
    "Самарканд": ("UZ", ["самарканд*", "samarqand*", "самарқанд*", "samarkand", "samarcande"], False),
    "Бухара": ("UZ", ["бухар*", "buxoro*", "бухоро*", "bukhara", "boukhara", "buchara"], False),
    "Наманган": ("UZ", ["наманган*", "namangan*"], False),
    "Андижан": ("UZ", ["андижан*", "andijon*", "андижон*", "andijan"], False),
    "Фергана": ("UZ", ["фергана", "фергане", "ферганы", "farg'ona*", "фарғона*", "fergana", "ferghana"], False),
    "Нукус": ("UZ", ["нукус*", "nukus*"], False),
    "Алматы": ("KZ", ["алматы*", "алма-ата*", "almaty", "alma-ata"], False),
    "Астана": ("KZ", ["астана*", "астане", "astana*"], False),
    "Шымкент": ("KZ", ["шымкент*", "чимкент*", "shymkent", "chimkent"], False),
    "Караганда": ("KZ", ["караганд*", "қарағанды*", "karaganda", "qaraghandy"], False),
    "Бишкек": ("KG", ["бишкек*", "bishkek*", "bichkek", "bischkek"], False),
    "Ош": ("KG", ["re:\\bОш(?:е|а|то|та|ко)?\\b", "re:\\bОш шаар"], True),
    "Джалал-Абад": ("KG", ["джалал-абад*", "жалал-абад*", "jalal-abad*", "jalalabad"], False),
    "Душанбе": ("TJ", ["re:\\bДушанбе\\b", "re:\\bDushanbe\\w*"], True),
    "Худжанд": ("TJ", ["худжанд*", "хуҷанд*", "xo'jand*", "khujand"], False),
    "Ашхабад": ("TM", ["ашхабад*", "ашгабат*", "ashgabat*", "asgabat*", "achgabat"], False),
    "Туркменабат": ("TM", ["туркменабат*", "turkmenabat*"], False),
    "Кабул": ("AF", ["кабул*", "kabul*", "kaboul", "کابل"], False),
    "Москва": ("RU", ["москв*", "moskva*", "moscow", "moscou", "moskau", "мәскеу*", "모스크바", "モスクワ", "莫斯科", "مسکو", "موسكو"], False),
    "Стамбул": ("TR", ["стамбул*", "istanbul*"], False),
}

_ARABIC_OR_CJK = re.compile(r"[؀-ۿ぀-ヿ一-鿿가-힯]")

LOCATIVE_RE = re.compile(
    r"(?:\b(?:в|во|на|у|возле|около|in|at|near|dans|a|en|bei|im|nel|nella|дар|назди)\s+\S*$)"
    r"|(?:(?:da|de|ta|te|да|де|та|те|дә|тә)$)"
)

RELATIONS = {
    "event_place_claimed": "Место, названное в самом сообщении рядом с угрозой (возможное место действия)",
    "reported_event_place": "Место события по сообщению материала (журналистское/официальное сообщение)",
    "near_threat": "Упомянуто в одном предложении с угрожающей лексикой",
    "context": "Упоминание в тексте (контекст), не связанное с угрозой напрямую",
}
DISCLAIMER = (
    "География — только явное упоминание в тексте материала. "
    "Местонахождение, происхождение и убеждения автора системой не определяются."
)


@dataclass
class GeoMention:
    name: str
    kind: str  # country | city
    country_code: str
    country_name: str
    relation: str
    relation_label: str
    evidence: str

    def to_dict(self) -> dict:
        return asdict(self)


def _compile(pattern: str, case_sensitive: bool) -> re.Pattern:
    if pattern.startswith("re:"):
        return re.compile(pattern[3:], 0 if case_sensitive else re.IGNORECASE)
    stem = pattern.endswith("*")
    body = fold_basic(pattern.rstrip("*"))
    esc = r"\s+".join(re.escape(p) for p in body.split(" "))
    if _ARABIC_OR_CJK.search(body):
        return re.compile(esc)
    return re.compile(r"(?<![\w'])" + esc + (r"\w*" if stem else r"(?![\w'])"))


_COMPILED: list[tuple[str, str, str, re.Pattern, bool]] = []
for _code, _pats in _COUNTRY_PATTERNS.items():
    for _p in _pats:
        _COMPILED.append(("country", COUNTRY_NAMES[_code], _code, _compile(_p, False), False))
for _city, (_code, _pats, _cs) in _CITY_PATTERNS.items():
    for _p in _pats:
        _COMPILED.append(("city", _city, _code, _compile(_p, _cs), _cs))


def find_mentions(text: str, folded: str, threat_spans: list[tuple[int, int]], journalism: bool) -> list[GeoMention]:
    """Находит упоминания мест в тексте и определяет, к чему они относятся.

    ``threat_spans`` — позиции угрожающей лексики в ``folded``.
    """
    original = unicodedata.normalize("NFKC", text or "")
    sentences = _sentence_spans(folded)
    found: dict[tuple[str, str], GeoMention] = {}
    for kind, name, code, regex, case_sensitive in _COMPILED:
        haystack = original if case_sensitive else folded
        for m in regex.finditer(haystack):
            key = (kind, name)
            if key in found:
                continue
            start, end = m.start(), m.end()
            if case_sensitive:
                # Пересчёт позиции в нормализованном тексте (приблизительно)
                idx = folded.find(m.group(0).casefold())
                start, end = (idx, idx + len(m.group(0))) if idx >= 0 else (0, 0)
            sent = _sentence_of(sentences, start)
            near = any(sent[0] <= s < sent[1] for s, _ in threat_spans)
            locative = bool(LOCATIVE_RE.search(folded[max(0, start - 12):start])) or bool(
                LOCATIVE_RE.search(m.group(0).casefold()[-3:])
            )
            if near and journalism:
                relation = "reported_event_place"
            elif near and locative:
                relation = "event_place_claimed"
            elif near:
                relation = "near_threat"
            else:
                relation = "context"
            evidence = folded[max(0, sent[0]):min(len(folded), sent[1])][:220]
            found[key] = GeoMention(name, kind, code, COUNTRY_NAMES[code], relation, RELATIONS[relation], evidence)
    return list(found.values())


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    pos = 0
    for sent in split_sentences(text):
        idx = text.find(sent, pos)
        if idx < 0:
            continue
        spans.append((idx, idx + len(sent)))
        pos = idx + len(sent)
    return spans or [(0, len(text))]


def _sentence_of(spans: list[tuple[int, int]], pos: int) -> tuple[int, int]:
    for s, e in spans:
        if s <= pos <= e:
            return s, e
    return spans[-1]


def country_options() -> list[dict]:
    return [{"code": c, "name": n} for c, n in COUNTRY_NAMES.items()]
