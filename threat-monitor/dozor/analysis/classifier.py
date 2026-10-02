"""Объяснимый классификатор содержания конкретного материала.

Решение строится из наблюдаемых признаков текста (``features``) по
прозрачным правилам. Каждое решение сопровождается объяснением, в котором
факты (что именно найдено в тексте) отделены от аналитической оценки.

Методологические опоры:

* Рабатский план действий ООН (2012): шестифакторный тест порога
  подстрекательства — контекст, говорящий, намерение, содержание и форма,
  масштаб распространения, вероятность и неотвратимость вреда;
* концепция «опасной речи» (Dangerous Speech Project): расчеловечивание,
  призывы к изгнанию и насилию в адрес группы;
* модели оценки угроз (TRAP-18, Meloy) — только как перечень наблюдаемых
  в тексте признаков («утечка» намерения, «последний довод», идентификация
  с нападавшими, признаки подготовки). Система НЕ оценивает людей.

Автоматическая оценка не устанавливает причастность человека к
экстремистской или террористической деятельности.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..text import geo
from ..text.langid import LANGUAGES, LangGuess
from ..text.lexicon import FORM_LABELS, Hit, get_lexicon
from ..text.normalize import TECHNIQUE_LABELS
from .features import Features, extract
from .taxonomy import (
    CATEGORIES,
    FRAMINGS,
    MANDATORY_REVIEW,
    PRIORITIES,
    SEVERITIES,
    SEVERITY_ORDER,
    THREAT_CATEGORIES,
    confidence_label,
)

ENGINE = "rules-v1"

LIMITATION_TEXT = (
    "Оценка автоматическая и вероятностная. Она описывает содержание конкретного материала, "
    "не устанавливает личность автора и его причастность к экстремистской или террористической "
    "деятельности и не заменяет проверку человеком."
)

_CROWDED = ("школ", "maktab", "мектеп", "мактаб", "school", "ecole", "schule", "escuela", "scuola", "학교", "学校",
            "мечет", "masjid", "мешіт", "мечит", "mosque", "церк", "church", "синагог", "synagogue", "рынок", "базар",
            "bozor", "market", "тц", "торгов", "mall", "метро", "metro", "вокзал", "station", "駅", "车站", "стадион",
            "stadium", "концерт", "concert", "university", "университет")


@dataclass
class Explanation:
    kind: str  # fact | assessment | rule | limitation
    text: str


@dataclass
class Assessment:
    engine: str
    is_threat: bool
    category: str
    category_label: str
    framing: str
    framing_label: str
    priority: str | None
    priority_label: str
    severity: str
    severity_label: str
    confidence: float
    confidence_label: str
    requires_human_review: bool
    review_reason: str
    scores: dict[str, float]
    specificity: dict
    evidence: list[dict]
    explanation: list[dict]
    geo: list[dict]
    language: dict
    obfuscation: list[str]
    rabat: dict | None = None
    engines: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class _Explainer:
    def __init__(self) -> None:
        self.items: list[Explanation] = []

    def fact(self, text: str) -> None:
        self.items.append(Explanation("fact", text))

    def assess(self, text: str) -> None:
        self.items.append(Explanation("assessment", text))

    def rule(self, text: str) -> None:
        self.items.append(Explanation("rule", text))

    def limit(self, text: str) -> None:
        self.items.append(Explanation("limitation", text))

    def dump(self) -> list[dict]:
        return [asdict(i) for i in self.items]


def _describe_hit(h: Hit) -> str:
    lex = get_lexicon()
    label = lex.concepts.get(h.concept, {}).get("label", h.concept)
    parts = [f"«{h.surface}» — {label}", FORM_LABELS.get(h.form, h.form), f"язык термина: {LANGUAGES.get(h.lang, h.lang)}"]
    if h.obfuscated:
        parts.append("найдено после снятия обфускации")
    if h.negated:
        parts.append("с отрицанием")
    return "; ".join(parts)


def _evidence(h: Hit, f: Features) -> dict:
    lex = get_lexicon()
    return {
        "concept": h.concept,
        "label": lex.concepts.get(h.concept, {}).get("label", h.concept),
        "group": h.group,
        "surface": h.surface,
        "form": h.form,
        "form_label": FORM_LABELS.get(h.form, h.form),
        "lang": h.lang,
        "obfuscated": h.obfuscated,
        "negated": h.negated,
        "quoted": f.in_quotes(h),
        "idiom": f.idiom_covered(h),
    }


def assess(text: str, lang: LangGuess | None = None, propagation_count: int = 0) -> Assessment:
    return assess_features(extract(text, lang=lang), propagation_count=propagation_count)


def assess_features(f: Features, propagation_count: int = 0) -> Assessment:  # noqa: C901 — правила намеренно в одном месте
    ex = _Explainer()
    t = f.target

    # ---------------------------------------------------------------- признаки
    violence_all = f.by_group("violence", include_negated=True)
    negated_v = [h for h in violence_all if h.negated]
    live_v = [h for h in violence_all if not h.negated]
    idiom_v = [h for h in live_v if f.idiom_covered(h)]
    v = [h for h in live_v if not f.idiom_covered(h)]
    quoted_v = [h for h in v if f.in_quotes(h)]
    own_v = [h for h in v if not f.in_quotes(h)]
    veiled = [h for h in own_v if h.concept == "veiled_threat"]
    hard_v = [h for h in own_v if h.concept != "veiled_threat"]

    # «Приду к школе с ружьём»: заявленное прибытие + оружие без глагола насилия
    weapons = f.by_group("weapon")
    arrival = [h for h in f.by_concept("arrival") if not f.in_quotes(h)]
    armed_arrival = bool(arrival and weapons)
    intent_near = any(f.near(h, "intent", before=50, after=10) for h in hard_v)
    fut = any(h.form == "f" for h in hard_v) or intent_near or armed_arrival
    imp = any(h.form == "i" for h in hard_v)
    oblig = any(f.near(h, "obligation", before=50, after=30) for h in hard_v)
    death_to = any(h.concept == "death_to" for h in own_v)
    conditional = any(f.near(h, "conditional", before=60, after=60) for h in own_v)
    time_m = f.marker_terms("time")
    place_m = f.marker_terms("place")
    prep_m = f.marker_terms("preparation")
    time_ok = bool(time_m)
    place_ok = bool(place_m) or bool(t.institutions)
    prep_ok = bool(prep_m)
    leakage = f.by_concept("leakage", "last_resort")
    glorification = f.by_concept("school_attack_glorification")

    rj = f.by_concept("recruit_join")
    rt = f.by_concept("recruit_travel")
    ideology = f.by_concept("ideology")
    contact = bool(f.by_concept("contact") or f.contact_handles)
    kys = f.by_concept("kys")
    doxx = f.by_concept("doxxing")
    mass_call = f.by_concept("mass_call")
    insult = f.by_concept("insult")
    dehum = f.by_concept("dehumanize")
    expel = f.by_concept("expel")
    paid = f.by_concept("paid_task")
    sabotage = f.by_concept("sabotage")
    minors = f.by_concept("minors")
    challenge = f.by_concept("challenge")
    burn_v = [h for h in v if h.concept == "burn"]

    journalism = f.marker_terms("journalism", 10)
    condemn = f.has("condemnation")
    fiction = f.has("fiction")
    humor = f.has("humor") or f.humor_emoji
    quote_verbs = f.has("quote_verbs")
    protest = f.has("protest")
    religious = f.has("religious")

    # ------------------------------------------------------------- факты
    for h in hard_v + veiled:
        ex.fact("Насильственная лексика: " + _describe_hit(h))
    for h in quoted_v:
        ex.fact("Насильственная лексика внутри кавычек: " + _describe_hit(h))
    for h in idiom_v:
        ex.fact(f"«{h.surface}» входит в устойчивое выражение (переносное значение).")
    for h in negated_v:
        ex.fact("Насильственная лексика с отрицанием: " + _describe_hit(h))
    for group_hits, title in (
        (rj + rt, "Вербовочные формулировки"),
        (ideology, "Идеологическая лексика (сама по себе не является признаком угрозы)"),
        (kys + doxx + mass_call + insult, "Признаки травли"),
        (dehum, "Расчеловечивающие обозначения"),
        (expel, "Призывы к изгнанию"),
        (paid + sabotage + challenge + minors, "Признаки опасного вовлечения"),
        (weapons, "Упоминание оружия"),
        (leakage + glorification, "Тревожные признаки в тексте (по перечню TRAP-18)"),
    ):
        if group_hits:
            ex.fact(f"{title}: " + ", ".join(f"«{h.surface}»" for h in group_hits[:5]))
    if intent_near:
        ex.fact("Конструкция намерения рядом с насильственным глаголом: " + ", ".join(f.marker_terms("intent")))
    if armed_arrival:
        ex.fact("Заявлено намерение прийти/принести вместе с упоминанием оружия: " + ", ".join(f"«{h.surface}»" for h in arrival[:3]))
    if oblig:
        ex.fact("Конструкция долженствования/призыва: " + ", ".join(f.marker_terms("obligation")))
    if contact:
        ex.fact("Указан канал связи: " + ", ".join([h.surface for h in f.by_concept("contact")] + ["@" + c for c in f.contact_handles])[:200])
    target_desc = t.describe()
    if target_desc:
        ex.fact("Обозначенная цель: " + "; ".join(target_desc))
    if time_ok:
        ex.fact("Указание времени: " + ", ".join(time_m))
    if place_ok:
        ex.fact("Указание места: " + ", ".join(list(dict.fromkeys(place_m + t.institutions))[:5]))
    if prep_ok:
        ex.fact("Признаки подготовки, заявленные в тексте: " + ", ".join(prep_m))
    if f.obfuscated:
        ex.fact("Обнаружены приёмы обхода фильтров: " + ", ".join(TECHNIQUE_LABELS[x] for x in sorted(f.norm.techniques)))
    if journalism:
        ex.fact("Маркеры новостного/официального сообщения: " + ", ".join(journalism[:5]))
    if condemn:
        ex.fact("Маркеры осуждения насилия: " + ", ".join(f.marker_terms("condemnation")))
    if fiction or humor:
        ex.fact("Маркеры игры/кино/шутки: " + ", ".join(f.marker_terms("fiction") + f.marker_terms("humor") + (["эмодзи смеха"] if f.humor_emoji else [])))
    if protest:
        ex.fact("Лексика протеста/политической критики: " + ", ".join(f.marker_terms("protest")))
    if religious:
        ex.fact("Религиозная лексика: " + ", ".join(f.marker_terms("religious")))

    # ------------------------------------------------------------- оценки
    scores: dict[str, float] = {}
    if (hard_v or armed_arrival) and fut:
        scores["direct_threat"] = (
            0.58 + 0.12 * t.specific + 0.06 * (t.any and not t.specific)
            + 0.07 * time_ok + 0.07 * place_ok + 0.10 * prep_ok + 0.05 * bool(leakage or glorification)
        )
    elif veiled:
        scores["direct_threat"] = 0.36 + 0.10 * t.specific + 0.06 * time_ok + 0.06 * place_ok + 0.05 * bool(weapons) + 0.05 * prep_ok
    if (hard_v and (imp or oblig)) or death_to:
        s = 0.52 + 0.10 * t.any + 0.05 * t.mass + 0.06 * time_ok + 0.06 * place_ok + 0.05 * prep_ok
        if death_to and not (imp or oblig) and not t.groups and not t.specific:
            s -= 0.05
        scores["call_to_violence"] = s
    if rj or rt:
        s = 0.25 + 0.15 * bool(rj) + 0.15 * bool(rt) + 0.20 * bool(ideology) + 0.15 * contact + 0.10 * bool(hard_v or weapons)
        if not (ideology or hard_v or weapons or rt):
            s = min(s, 0.32)
        scores["recruitment"] = s
    if t.groups and (hard_v or dehum or expel or death_to):
        scores["incitement"] = (
            0.40 + 0.15 * bool(hard_v or death_to) + 0.15 * bool(dehum) + 0.12 * bool(expel)
            + 0.10 * bool(imp or oblig or expel or death_to) + 0.05 * (time_ok or place_ok)
        )
    elif dehum and (hard_v or expel):
        scores["incitement"] = 0.36 + 0.1 * bool(expel) + 0.1 * bool(hard_v)
    if kys or doxx or mass_call or (insult and t.person):
        scores["harassment"] = (
            0.30 + 0.25 * bool(kys) + 0.20 * bool(doxx) + 0.15 * bool(mass_call) + 0.05 * bool(insult) + 0.12 * t.person
        )
    inv = 0.0
    if paid and (sabotage or burn_v or hard_v):
        inv = 0.62
    elif challenge and minors:
        inv = 0.58
    elif sabotage and (minors or contact or paid):
        inv = 0.52
    elif paid and minors:
        inv = 0.42
    elif challenge or glorification:
        inv = 0.34
    if inv:
        inv += 0.08 * contact + 0.08 * bool(minors and inv >= 0.5)
        scores["dangerous_involvement"] = inv
    if hard_v and not any(k in scores for k in ("direct_threat", "call_to_violence", "incitement", "recruitment")):
        scores["violence_mention"] = 0.22 + 0.08 * t.specific + 0.05 * (time_ok and place_ok) + 0.05 * bool(weapons) + 0.05 * prep_ok
    scores = {k: round(min(val, 0.97), 3) for k, val in scores.items()}

    # ------------------------------------------------------------- рамка
    strong_own = (bool(hard_v) or armed_arrival) and (fut or imp or oblig or death_to)
    real_world_specific = prep_ok or bool(t.institutions) or (time_ok and place_ok)
    any_violence = bool(v or idiom_v or negated_v or armed_arrival)
    framing = "own_voice" if (own_v or armed_arrival or rj or kys or doxx or mass_call or paid or expel or dehum) else "none"
    if any_violence:
        if idiom_v and not v:
            framing = "idiom"
        elif not strong_own and not veiled:
            if condemn:
                framing = "condemnation"
            elif len(journalism) >= 2 or (journalism and (quote_verbs or quoted_v)):
                framing = "journalism"
            elif quoted_v and not own_v:
                framing = "quotation"
            elif fiction or humor:
                framing = "fiction"
            elif negated_v and not own_v:
                framing = "negated"
        else:
            if (fiction or humor) and not real_world_specific:
                framing = "fiction"
            elif condemn or len(journalism) >= 2 or fiction or humor or conditional:
                framing = "mixed"
    elif framing == "own_voice" and len(journalism) >= 2 and not (kys or doxx or mass_call):
        framing = "journalism"

    suppressed_by = None
    if framing in ("journalism", "quotation", "condemnation", "fiction", "idiom"):
        suppressed_by = framing
        for k in ("direct_threat", "call_to_violence", "incitement", "recruitment", "violence_mention"):
            scores.pop(k, None)
        if framing in ("journalism", "quotation"):
            scores.pop("harassment", None)
            scores.pop("dangerous_involvement", None)
    elif framing == "negated":
        for k in ("direct_threat", "call_to_violence", "violence_mention"):
            scores.pop(k, None)

    # ------------------------------------------------------------- категория
    ranked = sorted(((k, val) for k, val in scores.items() if k != "violence_mention"), key=lambda kv: kv[1], reverse=True)
    top_cat, top = ranked[0] if ranked else (None, 0.0)
    mention = scores.get("violence_mention", 0.0)
    protest_slogan = protest and death_to and not (hard_v and any(h.concept != "death_to" for h in hard_v)) and not t.specific

    if top >= 0.45 and top_cat:
        category = top_cat
    elif top >= 0.30 or mention >= 0.30:
        category = "ambiguous"
    elif suppressed_by:
        category = suppressed_by
    elif condemn:
        category = "condemnation"
    elif len(journalism) >= 2:
        category = "journalism"
    elif framing == "negated":
        category = "no_threat"
    elif protest:
        category = "protected_speech"
    elif religious:
        category = "religious_expression"
    else:
        category = "no_threat"
    if category == "call_to_violence" and protest_slogan:
        category = "ambiguous"
        ex.assess(
            "Лозунг «смерть …» в контексте протеста: такие лозунги нередко риторичны; "
            "оценка контекста и реальной направленности требуется от аналитика."
        )
    is_threat = category in THREAT_CATEGORIES

    # ------------------------------------------------------------- тяжесть
    concepts_own = {h.concept for h in own_v} | ({"shoot"} if armed_arrival else set())
    sev = "low"

    def bump(level: str) -> None:
        nonlocal sev
        if SEVERITY_ORDER.index(level) > SEVERITY_ORDER.index(sev):
            sev = level

    crowded = any(any(c in inst for c in _CROWDED) for inst in t.institutions)
    if is_threat:
        if concepts_own & {"explode", "attack", "destroy_group"}:
            bump("catastrophic")
        if concepts_own & {"kill", "shoot", "stab", "death_to"}:
            bump("catastrophic" if (t.mass or crowded) else "severe")
        if concepts_own & {"burn", "beat", "veiled_threat"}:
            bump("significant")
        if category == "recruitment":
            bump("severe")
        if category == "harassment":
            bump("severe" if kys else "moderate")
        if category == "incitement":
            bump("moderate")
        if category == "dangerous_involvement":
            bump("severe" if glorification else "significant")
        if glorification and category == "direct_threat":
            bump("catastrophic")

    # ------------------------------------------------------------- приоритет
    priority: str | None = None
    rule_text = ""
    n_spec = int(time_ok) + int(place_ok) + int(prep_ok)
    if is_threat:
        if category == "ambiguous":
            if f.text_len < 30 or not t.any or top < 0.36 and mention < 0.36:
                priority = "insufficient_data"
                rule_text = "Неоднозначный контекст и слабые признаки: данных для оценки недостаточно."
            else:
                priority = "moderate"
                rule_text = "Неоднозначный контекст при обозначенной цели — умеренный приоритет."
        elif category == "harassment":
            priority = "moderate"
            rule_text = "Травля без конкретной цели или опасных действий — умеренный приоритет."
            if t.person and (kys or doxx or mass_call):
                priority = "high"
                rule_text = "Травля конкретного человека с призывом к самоповреждению, раскрытию данных или массовой атаке — высокий."
            if t.person and (time_ok or place_ok) and (doxx or hard_v):
                priority = "critical"
                rule_text = "Травля конкретного человека с указанием места/времени — критический."
        elif category == "dangerous_involvement":
            priority = "high"
            rule_text = "Признаки вовлечения людей в опасные действия — высокий."
            if minors or (paid and (sabotage or burn_v)):
                priority = "critical"
                rule_text = "Вовлечение несовершеннолетних или оплачиваемые поджоги/диверсии — критический."
            if n_spec >= 2 and prep_ok:
                priority = "very_critical"
                rule_text = "Вовлечение с конкретным временем/местом и признаками подготовки — очень критический."
        elif category == "recruitment":
            priority = "high"
            rule_text = "Вербовочный призыв — высокий."
            if contact or rt:
                priority = "critical"
                rule_text = "Вербовочный призыв с каналом связи или логистикой переезда — критический."
            if n_spec >= 2 and prep_ok:
                priority = "very_critical"
                rule_text = "Вербовка с указанием времени/места и признаками подготовки — очень критический."
        else:  # direct_threat, call_to_violence, incitement
            priority = "moderate"
            rule_text = "Угрожающее высказывание без обозначенной цели — умеренный."
            if t.any:
                priority = "high"
                rule_text = "Есть обозначенная цель, но нет времени, места и признаков подготовки — высокий."
            if t.any and n_spec >= 1:
                priority = "critical"
                rule_text = "Цель + время, место или признаки подготовки — критический."
            if (t.any and n_spec >= 2 and (prep_ok or leakage or glorification)) or n_spec == 3:
                priority = "very_critical"
                rule_text = "Цель, время/место и признаки подготовки — очень критический."
            if priority in ("moderate", "high") and sev == "catastrophic" and fut and (time_ok or place_ok):
                priority = "critical"
                rule_text = "Намерение причинить массовый вред с указанием времени/места — критический."
            if category == "direct_threat" and veiled and not hard_v and priority in ("critical", "very_critical") and not prep_ok:
                priority = "high"
                rule_text = "Завуалированная угроза: без признаков подготовки приоритет не выше высокого."

    # ------------------------------------------------------------- уверенность
    signal_types = sum(
        bool(x)
        for x in (fut or imp or oblig or death_to, t.any, time_ok, place_ok, prep_ok, weapons, contact, ideology, t.groups, dehum or expel, kys or doxx or mass_call)
    )
    if is_threat:
        conf = max(top, mention) + min(0.12, 0.03 * signal_types)
        if framing == "mixed":
            conf -= 0.15
        if category == "ambiguous":
            conf = min(conf, 0.5)
    else:
        markers = len(journalism) + int(condemn) * 2 + int(fiction or humor) + int(quote_verbs) + int(bool(idiom_v)) * 2 + int(protest) + int(religious)
        conf = 0.5 + min(0.3, 0.07 * markers) if category != "no_threat" else (0.75 if not any_violence else 0.6)
        if top > 0:
            conf -= 0.1
    if f.text_len < 25:
        conf -= 0.1
    if f.lang.confidence < 0.5:
        conf -= 0.05
    if f.obfuscated and is_threat:
        conf -= 0.03
    cross_lang = [h for h in v if f.lang.lang not in ("und", h.lang)]
    if cross_lang and is_threat:
        conf -= 0.05
    conf = round(max(0.05, min(0.95, conf)), 2)

    # ------------------------------------------------------------- проверка человеком
    review_reason = ""
    requires_review = False
    if priority in MANDATORY_REVIEW:
        requires_review = True
        review_reason = "Критическая оценка: обязательная проверка человеком до любых действий."
    elif is_threat and framing == "mixed" and SEVERITY_ORDER.index(sev) >= SEVERITY_ORDER.index("severe"):
        requires_review = True
        review_reason = "Противоречивые признаки при тяжёлом предполагаемом вреде."

    # ------------------------------------------------------------- выводы
    if is_threat:
        ex.assess(f"Система относит материал к категории «{CATEGORIES[category]}» (рамка: {FRAMINGS[framing].lower()}).")
        if top_cat and category != top_cat and top >= 0.3:
            ex.assess(f"Наиболее выраженный тип признаков — «{CATEGORIES.get(top_cat, top_cat)}», но их недостаточно для уверенного вывода.")
    else:
        reason = {
            "journalism": "материал сообщает о событии (новость, официальное сообщение), а не угрожает",
            "quotation": "насильственные слова приведены как цитата чужой речи",
            "condemnation": "материал осуждает насилие",
            "fiction": "насильственная лексика относится к игре, кино, книге или шутке",
            "idiom": "насильственная лексика употреблена в переносном значении",
            "protected_speech": "критика власти, мирный протест и политические взгляды сами по себе не являются угрозой",
            "religious_expression": "религиозные убеждения и практика сами по себе не являются угрозой",
            "no_threat": "признаков угрозы, призыва, вербовки или травли не найдено",
        }[category]
        ex.assess(f"Не угроза: {reason}.")
        if framing == "negated":
            ex.assess("Насильственная лексика употреблена с отрицанием.")
    if framing == "mixed":
        ex.assess("Есть противоречивые признаки (осуждение, новостной или игровой контекст, условие) — уверенность снижена.")
    if conditional and is_threat:
        ex.assess("Высказывание содержит условие («если…»): угроза может быть условной.")
    if protest and is_threat:
        ex.assess("Контекст протеста или политической критики сам по себе не повышает оценку.")
    if religious and is_threat:
        ex.assess("Религиозная лексика сама по себе не повышает оценку.")
    if rule_text:
        ex.rule("Приоритет: " + rule_text)
    if is_threat:
        ex.rule(f"Тяжесть предполагаемого вреда: {SEVERITIES[sev].lower()} — оценивается отдельно от приоритета.")
        ex.rule(
            f"Уверенность {conf:.2f} ({confidence_label(conf)}) — надёжность автоматической классификации, "
            "а не вероятность совершения насилия."
        )
    if requires_review:
        ex.rule(review_reason)
    ex.limit(LIMITATION_TEXT)

    rabat = None
    if category == "incitement" or (t.groups and is_threat):
        rabat = {
            "context": FRAMINGS[framing] + ("; контекст протеста" if protest else ""),
            "speaker": "Не оценивается автоматически: система не устанавливает личность и статус автора.",
            "intent": "Есть признаки призыва (повелительное наклонение, долженствование или изгнание)."
            if (imp or oblig or expel or death_to) else "Явных грамматических признаков призыва нет.",
            "content_form": ", ".join(
                x for x in (
                    "расчеловечивание" if dehum else "",
                    "призыв к изгнанию" if expel else "",
                    "насильственная лексика" if (hard_v or death_to) else "",
                ) if x
            ) or "—",
            "extent": f"Подтверждённых распространений в базе: {propagation_count}.",
            "likelihood_imminence": ", ".join(
                x for x in ("время указано" if time_ok else "", "место указано" if place_ok else "", "признаки подготовки" if prep_ok else "") if x
            ) or "Время, место и признаки подготовки не указаны.",
        }

    threat_spans = [(h.start, h.end) for h in v + rj + rt + kys + doxx + paid + sabotage + expel]
    geo_mentions = [g.to_dict() for g in geo.find_mentions(f.text, f.norm.deobf, threat_spans, framing == "journalism")]
    evidence = [_evidence(h, f) for h in f.hits]

    return Assessment(
        engine=ENGINE,
        is_threat=is_threat,
        category=category,
        category_label=CATEGORIES[category],
        framing=framing,
        framing_label=FRAMINGS[framing],
        priority=priority,
        priority_label=PRIORITIES[priority] if priority else "— (не угроза)",
        severity=sev,
        severity_label=SEVERITIES[sev],
        confidence=conf,
        confidence_label=confidence_label(conf),
        requires_human_review=requires_review,
        review_reason=review_reason,
        scores=scores,
        specificity={
            "target": {"present": t.any, "specific": t.specific, "details": target_desc},
            "time": time_m,
            "place": list(dict.fromkeys(place_m + t.institutions))[:6],
            "preparation": prep_m,
            "warning_behaviors": [h.surface for h in leakage + glorification],
            "intent": bool(fut),
            "call": bool(imp or oblig or death_to),
        },
        evidence=evidence,
        explanation=ex.dump(),
        geo=geo_mentions,
        language={"code": f.lang.lang, "label": f.lang.label, "confidence": f.lang.confidence, "script": f.lang.script, "method": f.lang.method},
        obfuscation=[TECHNIQUE_LABELS[x] for x in sorted(f.norm.techniques)] if f.obfuscated else [],
        rabat=rabat,
        engines={ENGINE: {"category": category, "priority": priority, "confidence": conf}},
    )


def priority_from_flags(category: str, target: bool, time_ok: bool, place_ok: bool, prep_ok: bool) -> str | None:
    """Упрощённая шкала приоритета по признакам конкретности (для внешних движков)."""
    if category not in THREAT_CATEGORIES:
        return None
    if category == "ambiguous":
        return "moderate" if target else "insufficient_data"
    n = int(time_ok) + int(place_ok) + int(prep_ok)
    if not target:
        return "moderate"
    if n >= 2 and prep_ok:
        return "very_critical"
    if n >= 1:
        return "critical"
    return "high"


def merge_llm(a: Assessment, llm: dict) -> Assessment:
    """Объединяет правило-ориентированную оценку с оценкой LLM.

    Расхождение не скрывается: оно снижает уверенность и требует проверки
    человеком. Приоритет выбирается по более осторожной (высокой) оценке.
    """
    from .taxonomy import max_priority

    engine = llm.get("engine", "llm")
    llm_cat = llm.get("category", "no_threat")
    if llm_cat not in CATEGORIES:
        llm_cat = "ambiguous"
    llm_priority = priority_from_flags(
        llm_cat,
        bool(llm.get("target_present")),
        bool(llm.get("time_present")),
        bool(llm.get("place_present")),
        bool(llm.get("preparation_present")),
    )
    a.engines[engine] = {
        "category": llm_cat,
        "priority": llm_priority,
        "confidence": round(float(llm.get("confidence", 0.5)), 2),
        "framing": llm.get("framing"),
        "rationale": llm.get("rationale", ""),
        "evidence_quotes": llm.get("evidence_quotes", []),
        "ungrounded_quotes": llm.get("ungrounded_quotes", 0),
    }
    a.explanation.insert(
        len([e for e in a.explanation if e["kind"] == "fact"]),
        {"kind": "assessment", "text": f"Второй классификатор ({engine}): «{CATEGORIES[llm_cat]}». {llm.get('rationale', '')}".strip()},
    )
    if llm.get("ungrounded_quotes"):
        a.explanation.append({
            "kind": "limitation",
            "text": f"LLM привела {llm['ungrounded_quotes']} цитат(ы), которых нет в тексте; они отброшены.",
        })
    llm_threat = llm_cat in THREAT_CATEGORIES
    agree = llm_cat == a.category
    if agree:
        a.confidence = round(min(0.95, 0.5 * a.confidence + 0.5 * max(a.confidence, float(llm.get("confidence", 0.5))) + 0.05), 2)
    else:
        a.confidence = round(max(0.05, a.confidence - 0.2), 2)
        a.requires_human_review = True
        a.review_reason = "Классификаторы расходятся в оценке — требуется проверка человеком."
        a.explanation.append({"kind": "rule", "text": a.review_reason})
        if llm_threat and not a.is_threat:
            a.is_threat = True
            a.category = llm_cat
            a.category_label = CATEGORIES[llm_cat]
            sev = llm.get("severity", "low")
            a.severity = sev if sev in SEVERITIES else "low"
            a.severity_label = SEVERITIES[a.severity]
    if a.is_threat:
        a.priority = max_priority(a.priority, llm_priority if llm_threat else None)
        if a.priority is None:
            a.priority = "insufficient_data"
        a.priority_label = PRIORITIES[a.priority]
        if a.priority in MANDATORY_REVIEW:
            a.requires_human_review = True
            if not a.review_reason:
                a.review_reason = "Критическая оценка: обязательная проверка человеком до любых действий."
    a.confidence_label = confidence_label(a.confidence)
    a.engine = f"{ENGINE}+{engine}"
    return a
