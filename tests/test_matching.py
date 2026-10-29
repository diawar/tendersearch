import pytest

from tender_search.matching import match_lot, matches
from tender_search.core.templatetags.search_tags import highlight


@pytest.mark.parametrize("text,term,kind,expected", [
    ("Телефон", "телефон", "word", True),
    ("Аппарат телефонный", "телефон", "word", False),
    ("Аппарат телефонный", "телефон", "contains", True),
    ("Кабель   медный", "кабель медный", "phrase", True),
    ("Кабель силовой медный", "кабель медный", "phrase", False),
    ("Закупка телефона", "телефон", "word", False),
    ("  ТЕЛЕФОН  ", "телефон", "field", True),
    ("Телефон сотовой связи", "телефон", "field", False),
    ("a+b", "a+b", "word", True),
    ("aaaab", "a+b", "contains", False),
    ("Счётчик", "счетчик", "contains", False),
    ("ВВГ–нг", "ВВГ-нг", "contains", False),
])
def test_matching_semantics(text, term, kind, expected):
    assert matches(text, term, kind) is expected


def test_and_can_match_name_and_description_of_same_lot():
    spec = {"terms": ["кабель", "медный"], "mode": "all", "kind": "word"}
    assert match_lot("Кабель", "Медный, 10 метров", spec) == spec["terms"]
    assert match_lot("Кабель", "Алюминиевый", spec) == []


def test_or_and_exclusions():
    spec = {"terms": ["телефон", "смартфон"], "exclude": ["ремонт"], "mode": "any", "kind": "word"}
    assert match_lot("Смартфон", "", spec) == ["смартфон"]
    assert match_lot("Телефон", "Ремонт и монтаж", spec) == []


def test_whole_field_applies_only_to_name():
    assert match_lot("Телефон", "не часть названия", {"terms": ["телефон"], "kind": "field"}) == ["телефон"]


def test_highlight_escapes_source_html_and_terms():
    rendered = str(highlight('<script>alert(1)</script> Телефон', ["Телефон"]))
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered and "<mark>Телефон</mark>" in rendered


@pytest.mark.parametrize("name,description", [
 ("Услуги по информационной поддержке сайтов", ""),
 ("Разработка сайта", "Оказание услуг по поддержке"),
 ("Ремонт сайта", ""),
 ("Обслуживание сайта", "После ремонта"),
])
def test_exclusions_match_word_parts(name, description):
    assert match_lot(name, description, {"terms":["сайт"],"exclude":["услуг","ремонт"]}) == []

def test_exclusion_does_not_hide_unrelated_lot():
    assert match_lot("Разработка сайта", "Создание страниц", {"terms":["сайт"],"exclude":["услуг","ремонт"]}) == ["сайт"]
