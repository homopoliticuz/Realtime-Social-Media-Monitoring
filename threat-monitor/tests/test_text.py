import pytest

from dozor.text import geo, translit
from dozor.text.langid import detect
from dozor.text.normalize import fold_basic, normalize
from dozor.text.pii import QueryPolicyError, check_query, mask_pii, person_name_notice


@pytest.mark.parametrize(
    "raw, expected, technique",
    [
        ("y6ью тебя", "убью тебя", "homoglyph"),  # латинская y и цифра 6 вместо «б»
        ("у.б.и.т.ь", "убить", "separators"),
        ("у б и т ь", "убить", "separators"),
        ("убиииить", "убить", "stretching"),
        ("k1ll them", "kill them", "leet"),
        ("взоrвy", "взорву", "homoglyph"),
    ],
)
def test_deobfuscation(raw, expected, technique):
    n = normalize(raw)
    assert n.deobf == expected
    assert technique in n.techniques


def test_trailing_punctuation_and_handles_are_not_leet():
    n = normalize("Qoralaymiz! @demo_bot")
    assert n.deobf == "qoralaymiz! @demo_bot"


def test_fold_basic_unifies_scripts():
    assert fold_basic("Ёлка") == "елка"
    assert fold_basic("Oʻldiraman") == "o'ldiraman"  # узбекский апостроф
    assert fold_basic("Töten") == "toten"
    assert fold_basic("سأقتل") == fold_basic("ساقتل")  # хамза над алифом
    assert fold_basic("می‌کشم") == "میکشم"  # ZWNJ в персидском
    assert fold_basic("می کشم") == "میکشم"
    assert fold_basic("zero​width") == "zerowidth"


def test_translit_uzbek_both_ways():
    assert translit.uz_cyr_to_lat("ўлдираман") == "o'ldiraman"
    assert translit.uz_lat_to_cyr("o'ldiraman") == "ўлдираман"
    assert translit.uz_lat_to_cyr("ertaga") == "эртага"
    assert translit.uz_cyr_to_lat("ер") == "yer"


def test_translit_russian_variants():
    variants = translit.ru_to_latin_variants("убью")
    assert {"ubyu", "ub'ju"} & variants
    assert translit.latin_to_ru("vzorvu") == "взорву"


@pytest.mark.parametrize(
    "text, lang",
    [
        ("Завтра будет хорошая погода, мы идём гулять", "ru"),
        ("Men seni ertaga topaman, bu juda muhim", "uz"),
        ("Бу хоинларни ўлдириш керак", "uz"),
        ("Ертең емтиханды жарып жіберемін", "kk"),
        ("Бишкекте шектелген адам кармалды, деп билдирди", "ky"),
        ("Ман туро мекушам, Алӣ", "tg"),
        ("Oraza baýramyňyz gutly bolsun", "tk"),
        ("The police said the suspect was arrested", "en"),
        ("Je condamne fermement cet attentat", "fr"),
        ("Die Polizei hat einen Mann festgenommen", "de"),
        ("Me voy a matar estudiando para el examen", "es"),
        ("Il giornale riporta la frase dell'aggressore", "it"),
        ("경찰은 남성을 체포했다고 밝혔다", "ko"),
        ("明日、駅で全員殺す", "ja"),
        ("我要杀了他", "zh"),
        ("مرگ بر همه‌شان، باید همه را کشت", "fa"),
        ("أعلنت الشرطة اعتقال شخص", "ar"),
    ],
)
def test_language_detection(text, lang):
    assert detect(text).lang == lang


def test_pii_masking_and_query_guard():
    masked, counts = mask_pii("Пишите на test@example.com или +998 90 123-45-67, встреча 12.10.2026")
    assert "[e-mail скрыт]" in masked and "[телефон скрыт]" in masked
    assert "12.10.2026" in masked
    assert counts == {"email": 1, "phone": 1}
    for q in ("ivan@example.com", "+7 701 123 45 67", "AA1234567"):
        with pytest.raises(QueryPolicyError):
            check_query(q)
    check_query("убить школа")  # обычный запрос допустим
    assert person_name_notice("Иван Тестов") is not None
    assert person_name_notice("убить") is None


def test_geo_mentions_relation_and_no_author_location():
    text = "Завтра взорву рынок в Ташкенте"
    from dozor.analysis.features import extract

    f = extract(text)
    spans = [(h.start, h.end) for h in f.hits]
    mentions = geo.find_mentions(text, f.norm.deobf, spans, journalism=False)
    assert mentions and mentions[0].name == "Ташкент"
    assert mentions[0].relation == "event_place_claimed"
    assert "автора" in geo.DISCLAIMER
    # Упоминание вне контекста угрозы
    m2 = geo.find_mentions("Красивый закат в Бишкеке", fold_basic("Красивый закат в Бишкеке"), [], False)
    assert m2[0].relation == "context"


def test_tajik_monday_is_not_dushanbe_city():
    text = "душанбе вохӯрем"  # «в понедельник встретимся» — строчными
    assert not geo.find_mentions(text, fold_basic(text), [], False)
