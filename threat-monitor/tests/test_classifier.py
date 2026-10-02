import pytest

from dozor.analysis.classifier import assess, merge_llm, priority_from_flags
from dozor.analysis.taxonomy import MANDATORY_REVIEW, THREAT_CATEGORIES
from dozor.data.demo import DEMO_MATERIALS


@pytest.mark.parametrize("m", DEMO_MATERIALS, ids=[m["key"] for m in DEMO_MATERIALS])
def test_demo_expectations(m):
    a = assess(m["text"])
    assert a.category == m["expected"]["category"], a.explanation
    if "priority" in m["expected"]:
        assert a.priority == m["expected"]["priority"]


def test_every_decision_has_explanation_and_limitation():
    for m in DEMO_MATERIALS:
        a = assess(m["text"])
        kinds = {e["kind"] for e in a.explanation}
        assert "limitation" in kinds
        assert "assessment" in kinds
        if a.is_threat:
            assert "rule" in kinds


def test_critical_requires_human_review():
    for m in DEMO_MATERIALS:
        a = assess(m["text"])
        if a.priority in MANDATORY_REVIEW:
            assert a.requires_human_review
            assert a.review_reason


def test_severity_and_confidence_are_separate_from_priority():
    a = assess("Иван Тестов, ты конченый. Все пишем ему в личку, слейте адрес, пусть знает.")
    assert a.priority == "high"
    assert a.severity == "moderate"
    assert 0 < a.confidence < 1


@pytest.mark.parametrize(
    "text, category",
    [
        ("Я никогда не стану убивать людей", "no_threat"),
        ("Мы осуждаем теракт и скорбим о погибших", "condemnation"),
        ("Полиция задержала мужчину, который угрожал взорвать вокзал, сообщает МВД", "journalism"),
        ("Он написал: «я всех убью», — сообщили в полиции", "journalism"),
        ("Убить время в очереди — моё хобби", "idiom"),
        ("Требуем отставки министра, коррупция душит страну! Мирный митинг в субботу", "protected_speech"),
        ("Ramazon muborak! Namoz va duo qabul bo'lsin", "religious_expression"),
        ("The army of Jihad in history: a lecture on medieval caliphate politics", "no_threat"),
    ],
)
def test_non_threat_contexts(text, category):
    a = assess(text)
    assert a.category == category, a.explanation
    assert not a.is_threat
    assert a.priority is None


def test_protest_death_slogan_is_ambiguous_not_call():
    a = assess("Мирный протест против коррупции! смерть им всем, кто ворует")
    assert a.category in ("ambiguous", "call_to_violence")
    if a.category == "call_to_violence":
        pytest.fail("лозунг на протесте без конкретной цели не должен становиться призывом")


def test_quoted_threat_in_trailer_is_quotation():
    a = assess('"I will blow up the bridge," the villain says in the new trailer.')
    assert a.category == "quotation"


def test_obfuscated_threat_detected_and_flagged():
    a = assess("Я тебя y6ью завтра у подъезда")
    assert a.category == "direct_threat"
    assert a.obfuscation
    assert a.priority in ("critical", "very_critical")


def test_negation_in_uzbek_morphology():
    assert assess("Men uni hech qachon o'ldirmayman").category == "no_threat"


def test_armed_arrival_without_violence_verb():
    a = assess("Завтра приду в школу с пистолетом")
    assert a.category == "direct_threat"
    assert a.priority == "critical"


def test_incitement_has_rabat_factors():
    a = assess("Пора выгнать всех приезжих из нашего района, это не люди, а тараканы.")
    assert a.category == "incitement"
    assert a.rabat and "speaker" in a.rabat
    assert "не оценивается" in a.rabat["speaker"].lower()


def test_no_author_attribution_in_output():
    a = assess("Brothers, join the ranks of the mujahideen. DM for travel details.")
    text = " ".join(e["text"] for e in a.explanation)
    assert "не устанавливает" in text
    assert "автор является" not in text


def test_llm_disagreement_lowers_confidence_and_forces_review():
    a = assess("ну всё, ему конец")
    before = a.confidence
    merged = merge_llm(a, {
        "engine": "llm:test", "category": "no_threat", "framing": "own_voice", "target_present": False,
        "time_present": False, "place_present": False, "preparation_present": False, "severity": "low",
        "confidence": 0.8, "rationale": "Спортивный контекст", "evidence_quotes": [], "ungrounded_quotes": 2,
    })
    assert merged.requires_human_review
    assert merged.confidence <= before
    assert any("отброшены" in e["text"] for e in merged.explanation)


def test_priority_from_flags():
    assert priority_from_flags("direct_threat", True, True, True, True) == "very_critical"
    assert priority_from_flags("direct_threat", True, False, False, False) == "high"
    assert priority_from_flags("journalism", True, True, True, True) is None
    assert set(THREAT_CATEGORIES) >= {"direct_threat", "harassment"}
