from dozor.text.expansion import expand_query
from dozor.text.langid import PARALLEL_DEFAULT, REGIONAL
from dozor.text.normalize import normalize

REQUIRED = ["ru", "en", "fr", "de", "es", "it", "ko", "ja", "zh", "fa", "ar"]


def test_russian_query_expands_to_all_required_languages():
    exp = expand_query("убить")
    assert exp.terms[0].method == "lexicon"
    assert "kill" in exp.terms[0].concepts
    for lang in REQUIRED + REGIONAL:
        assert exp.terms[0].by_lang.get(lang), f"нет вариантов для {lang}"
    assert "kill" in exp.terms[0].by_lang["en"]
    assert any("o'ldir" in w for w in exp.terms[0].by_lang["uz"])
    assert any("ўлдир" in w for w in exp.terms[0].by_lang["uz"])  # узбекская кириллица


def test_default_languages_cover_point_9():
    assert PARALLEL_DEFAULT == REQUIRED


def test_query_in_other_languages_and_translit_map_to_same_concept():
    for q in ("kill", "o'ldirish", "ubit", "öldürmek", "殺す", "قتل"):
        exp = expand_query(q)
        assert "kill" in exp.terms[0].concepts, q


def test_inflected_form_maps_to_concept():
    assert "explode" in expand_query("взорву").terms[0].concepts


def test_unknown_word_falls_back_to_literal_with_warning():
    exp = expand_query("Самарканд")
    assert exp.terms[0].method == "literal"
    assert exp.warnings
    assert "samarkand" in exp.terms[0].literal_variants


def test_translator_used_for_unknown_terms():
    def fake(term, langs):
        return {"en": ["test-translation"], "fr": ["traduction"]}

    exp = expand_query("неологизм", languages=["en", "fr"], translator=fake)
    assert exp.terms[0].method == "llm"
    assert exp.terms[0].by_lang["en"] == ["test-translation"]
    assert "проверки" in exp.terms[0].note


def test_matcher_finds_cross_language_material():
    exp = expand_query("убить")
    match = exp.matcher()
    assert match(normalize("I will kill you tomorrow")) == ["убить"]
    assert match(normalize("Men seni o'ldiraman")) == ["убить"]
    assert match(normalize("Hello world")) == []


def test_platform_queries_interleave_languages():
    exp = expand_query("убить", languages=["ru", "en", "fr"])
    q = exp.platform_queries(3)
    assert [lang for lang, _ in q] == ["ru", "en", "fr"]


def test_topics():
    exp = expand_query("", topics=["harassment", "terrorism_recruitment"])
    assert exp.mode == "any"
    assert {"kys", "recruit_join"} <= exp.concept_ids
