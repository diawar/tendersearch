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
    ("Мышь", "мыш", "contains", True),
    ("Проводная МЫШКА", "мыш", "contains", True),
    ("USB-мышь, (мышки)", "мыш", "contains", True),
    ("промышленный", "мыш", "contains", False),
    ("внутримышечный", "мыш", "contains", False),
    ("Момышулы Момышұлы", "мыш", "contains", False),
    ("компьютернаямышь", "мыш", "contains", False),
    ("123мышь _мышь", "мыш", "contains", False),
    ("клавиатуры", "клавиатур", "contains", True),
    ("Беспроводную мышь", "беспроводн мыш", "phrase", True),
    ("Новая БЕСПРОВОДНАЯ МЫШКА USB", "беспроводн мыш", "phrase", True),
    ("Беспроводной комплект клавиатура и мышь", "беспроводн мыш", "phrase", False),
    ("Мышь беспроводная", "беспроводн мыш", "phrase", False),
    ("Сверхбеспроводная мышь", "беспроводн мыш", "phrase", False),
    ("Беспроводной промышленный", "беспроводн мыш", "phrase", False),
    ("Беспроводная мышка", "беспроводная мышь", "phrase", False),
    ("Радиус действия беспроводной мыши", "беспроводная мышь", "phrase", True),
    ("Подключение беспроводных мышей", "беспроводная мышь", "phrase", True),
    ("Для новых людей", "новый человек", "phrase", True),
    ("Мыши беспроводные", "беспроводная мышь", "phrase", False),
    ("Сверхбеспроводной мыши", "беспроводная мышь", "phrase", False),
    ("Беспроводной комплект и мышь", "беспроводная мышь", "phrase", False),
    ("Промышленные мышечные ткани", "беспроводная мышь", "phrase", False),
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


def test_prefix_applies_in_both_fields_and_and_or_modes():
    spec = {'terms': ['мыш', 'клавиатур'], 'kind': 'contains', 'mode': 'any'}
    assert match_lot('Промышленный компьютер', 'Адрес: Момышулы', spec) == []
    assert match_lot('Клавиатура', 'Адрес: Момышулы', spec) == ['клавиатур']
    spec['mode'] = 'all'
    assert match_lot('Клавиатура', 'Адрес: Момышулы', spec) == []
    assert match_lot('Клавиатура', 'В комплекте мышка', spec) == ['мыш', 'клавиатур']


def test_exclusions_keep_substring_semantics():
    assert match_lot('Мышь', 'Промышленная', {'terms': ['мыш'], 'exclude': ['мышл']}) == []


def test_highlight_does_not_mark_inside_words():
    assert str(highlight('Мышка промышленная Момышулы', ['мыш'])) == '<mark>Мыш</mark>ка промышленная Момышулы'


def test_highlight_phrase_prefixes_only_when_adjacent():
    assert str(highlight('Беспроводную мышь', ['беспроводн мыш'])) == '<mark>Беспроводную мышь</mark>'
    text = 'Беспроводной комплект клавиатура и мышь'
    assert str(highlight(text, ['беспроводн мыш'])) == text


def test_highlight_inflected_phrase_preserves_source_and_escapes_html():
    assert str(highlight('Для беспроводной мыши', ['беспроводная мышь'])) == 'Для <mark>беспроводной мыши</mark>'
    assert str(highlight('<img> беспроводной мыши', ['беспроводная мышь'])) == '&lt;img&gt; <mark>беспроводной мыши</mark>'
