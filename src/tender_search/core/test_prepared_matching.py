from unittest.mock import patch
from django.test import SimpleTestCase

from tender_search import matching


def reference(name, description, spec):
    """Previous implementation, kept independent for semantic comparison."""
    combined = f"{name or ''}\n{description or ''}"
    if any(matching.matches(combined, term, 'substring') for term in spec.get('exclude', [])):
        return []
    kind = spec.get('kind', 'contains')
    text = name if kind == 'field' else combined
    found = [term for term in spec['terms'] if matching.matches(text, term, kind)]
    return [] if spec.get('mode', 'any') == 'all' and len(found) != len(spec['terms']) else found


class PreparedMatchingTests(SimpleTestCase):
    def test_same_semantics_as_previous_matcher(self):
        texts = [(None, None), ('МЫШКА', 'комплект клавиатуры'),
                 ('Промышленная', 'Момышулы, компьютернаямышь'),
                 ('Телефон', 'Ремонт услуг'), ('Беспроводную мышь', ''),
                 ('a+b [x]', '123мышь _мышь'), ('Кабель\n медный', 'Қазақша пернетақта'),
                 ('е\u0308лка', 'Счётчик'), ('Телефон', 'Описание')]
        term_sets = [['мыш', 'клав'], ['телефон'], ['беспроводн мыш'],
                     ['a+b', '[x]'], ['кабель медный'], ['Қазақша'], ['ёлка'],
                     ['', '   ', 'мыш'], ['мыш', 'мыш'], []]
        for kind in ['contains', 'substring', 'phrase', 'word', 'field', 'unknown']:
            for mode in ['any', 'all']:
                for terms in term_sets:
                    for excluded in [[], ['ремонт'], ['мышл', ''], ['описание']]:
                        spec = dict(kind=kind, mode=mode, terms=terms, exclude=excluded)
                        matcher = matching.PreparedMatcher(spec)
                        for name, description in texts:
                            with self.subTest(kind=kind, mode=mode, terms=terms, name=name, excluded=excluded):
                                expected = reference(name, description, spec)
                                self.assertEqual(matching.match_lot(name, description, matcher), expected)
                                self.assertEqual(matching.match_lot(name, description, spec), expected)

    def test_each_lot_normalized_once_and_conditions_reused(self):
        matcher = matching.PreparedMatcher({'terms': ['мыш', 'клав', 'компьютер'], 'exclude': ['ремонт']})
        with patch.object(matching, 'normalize', wraps=matching.normalize) as normalize:
            self.assertEqual(matching.match_lot('Мышка', 'Клавиатура компьютер', matcher), ['мыш', 'клав', 'компьютер'])
            self.assertEqual(matching.match_lot('Мышка', 'Клавиатура компьютер', matcher), ['мыш', 'клав', 'компьютер'])
            self.assertEqual(normalize.call_count, 2)
