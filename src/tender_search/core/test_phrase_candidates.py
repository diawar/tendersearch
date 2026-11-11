from django.test import SimpleTestCase
from tender_search.matching import PreparedMatcher

class PhraseCandidatePerformanceTests(SimpleTestCase):
    def test_inflections_adjacency_and_word_boundaries_remain_exact(self):
        matcher=PreparedMatcher({'terms':['беспроводная мышь'],'kind':'phrase'})
        for text in ['Купить беспроводную мышь', 'беспроводной мышью', 'БЕСПРОВОДНЫЕ МЫШИ']:
            self.assertTrue(matcher('',text),text)
        for text in ['беспроводной комплект клавиатура и мышь','пробеспроводная мышь','беспроводная внутримышечная инъекция']:
            self.assertFalse(matcher('',text),text)

    def test_suppletive_forms_and_overlapping_windows_are_preserved(self):
        matcher=PreparedMatcher({'terms':['человек'],'kind':'phrase'})
        self.assertTrue(matcher('', 'люди'))
        matcher=PreparedMatcher({'terms':['беспроводная мышь'],'kind':'phrase'})
        self.assertTrue(matcher('', 'беспроводная беспроводную мышь'))

    def test_long_irrelevant_text_does_not_require_word_by_word_analysis(self):
        from unittest.mock import patch
        import tender_search.morphology as morphology
        matcher=PreparedMatcher({'terms':['беспроводная мышь'],'kind':'phrase'})
        original=morphology.known_lemmas
        with patch.object(morphology,'known_lemmas',wraps=original) as analyze:
            self.assertFalse(matcher('', 'общие условия договора поставки товара '*1000))
        self.assertEqual(analyze.call_count,0)
