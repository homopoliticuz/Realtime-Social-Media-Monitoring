from dozor.analysis import dedup


def test_exact_duplicate_hash_ignores_case_punctuation_and_urls():
    a = "Завтра в 8:30 приду к школе! https://t.me/x/1"
    b = "завтра в 8 30 приду к школе"
    assert dedup.content_hash(a) == dedup.content_hash(b)


def test_near_duplicate_detection():
    a = "Пора выгнать всех приезжих из нашего района, это не люди, а тараканы. Кто с нами — в субботу на площади."
    b = "Пора выгнать всех приезжих из нашего района, они не люди, а тараканы. Все, кто с нами, — в субботу на площадь!"
    ok, j = dedup.near_duplicate(a, b)
    assert ok and j >= 0.6
    ok2, _ = dedup.near_duplicate(a, "Совершенно другой текст про погоду и урожай яблок в этом году")
    assert not ok2


def test_short_texts_are_not_marked_similar():
    ok, _ = dedup.near_duplicate("ну всё", "ну всё")
    assert not ok


def test_evidence_hash_is_of_original_text():
    assert dedup.evidence_hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
