"""Таксономия: категории, рамки, приоритеты, тяжесть, уверенность."""

THREAT_CATEGORIES = {
    "direct_threat": "Прямая угроза",
    "call_to_violence": "Призыв к насилию",
    "recruitment": "Вербовка (в т. ч. террористическая)",
    "incitement": "Подстрекательство к насилию по национальному/религиозному признаку",
    "harassment": "Травля",
    "dangerous_involvement": "Опасное вовлечение людей",
    "ambiguous": "Неоднозначный контекст",
}
NON_THREAT_CATEGORIES = {
    "quotation": "Цитирование",
    "journalism": "Журналистское / официальное освещение",
    "condemnation": "Осуждение насилия",
    "fiction": "Художественный, игровой или шуточный контекст",
    "idiom": "Переносное значение (идиома)",
    "protected_speech": "Критика власти, мирный протест, политические взгляды",
    "religious_expression": "Выражение религиозных убеждений",
    "no_threat": "Признаков угрозы не обнаружено",
}
CATEGORIES = {**THREAT_CATEGORIES, **NON_THREAT_CATEGORIES}

FRAMINGS = {
    "own_voice": "Высказывание от своего имени",
    "quotation": "Цитата чужих слов",
    "journalism": "Сообщение о событии (СМИ, официальные лица)",
    "condemnation": "Осуждение насилия",
    "fiction": "Игра, кино, книга, шутка",
    "idiom": "Идиоматическое выражение",
    "negated": "Отрицание («не собираюсь…»)",
    "mixed": "Смешанные признаки",
    "none": "Нет насильственной лексики",
}

# Приоритет проверки (п. 5 ТЗ)
PRIORITIES = {
    "insufficient_data": "Недостаточно данных",
    "moderate": "Умеренный",
    "high": "Высокий",
    "critical": "Критический",
    "very_critical": "Очень критический",
}
PRIORITY_ORDER = ["insufficient_data", "moderate", "high", "critical", "very_critical"]
MANDATORY_REVIEW = {"critical", "very_critical"}

# Тяжесть предполагаемого вреда — отдельно от приоритета
SEVERITIES = {
    "low": "Низкая / неопределённая",
    "moderate": "Умеренная (психологический вред, травля, рознь)",
    "significant": "Значительная (телесный вред, поджог, диверсия)",
    "severe": "Тяжёлая (угроза жизни человека)",
    "catastrophic": "Катастрофическая (массовые жертвы)",
}
SEVERITY_ORDER = ["low", "moderate", "significant", "severe", "catastrophic"]

REVIEW_STATUSES = {
    "new": "Новый — не проверен",
    "needs_review": "Требует обязательной проверки",
    "in_review": "На проверке",
    "confirmed": "Подтверждён аналитиком",
    "escalated": "Передан по установленной процедуре",
    "false_positive": "Ложное срабатывание",
    "not_threat": "Не угроза (проверено)",
    "closed": "Закрыт",
}


def confidence_label(value: float) -> str:
    if value >= 0.7:
        return "высокая"
    if value >= 0.45:
        return "средняя"
    return "низкая"


def max_priority(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if PRIORITY_ORDER.index(a) >= PRIORITY_ORDER.index(b) else b
