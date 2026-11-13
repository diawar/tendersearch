from urllib.parse import parse_qs, urlsplit

from django.core.paginator import Paginator
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase

from .pagination import page_numbers
from .templatetags.search_tags import numbered_pagination


class NumberedPaginationTests(SimpleTestCase):
    def test_page_ranges(self):
        self.assertEqual(page_numbers(1, 20), [1, 2, 3, 4, 5, None, 20])
        self.assertEqual(page_numbers(10, 20), [1, None, 8, 9, 10, 11, 12, None, 20])
        self.assertEqual(page_numbers(20, 20), [1, None, 16, 17, 18, 19, 20])
        self.assertEqual(page_numbers(2, 5), [1, 2, 3, 4, 5])

    def test_workspace_filters_and_repeated_parameters_survive(self):
        request = RequestFactory().get('/workspace/', {
            'profile': '12', 'stage': 'preparing', 'sort': '-amount',
            'lot_number': '885', 'status': ['6', '7'], 'page': '2'})
        page = Paginator(range(200), 10).page(2)
        result = numbered_pagination({'request': request, 'per_page': 10}, page)
        query = parse_qs(urlsplit(result['next_url']).query)
        self.assertEqual(query['page'], ['3'])
        self.assertEqual(query['profile'], ['12'])
        self.assertEqual(query['stage'], ['preparing'])
        self.assertEqual(query['sort'], ['-amount'])
        self.assertEqual(query['lot_number'], ['885'])
        self.assertEqual(query['status'], ['6', '7'])
        self.assertEqual(query['per_page'], ['10'])

    def test_render_current_and_search_anchor(self):
        request = RequestFactory().get('/search/example/', {'sort': 'amount'})
        page = Paginator(range(200), 10).page(1)
        html = Template("{% include 'core/_pagination.html' %}").render(
            Context({'request': request, 'page': page, 'per_page': 10, 'job': object()}))
        self.assertIn('aria-current="page">1</span>', html)
        self.assertIn('page=2#search-results', html)
        self.assertIn('Страница 5', html)
        self.assertIn('…', html)

    def test_one_page_has_no_links(self):
        self.assertEqual(numbered_pagination({}, Paginator(range(2), 10).page(1)), {})
