from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import WebSearch, SearchHit
from .search_service import store_lot
from .test_index import row


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ResultSortingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('sorting-owner')
        self.client.force_login(self.owner)
        self.now = timezone.now().replace(microsecond=0)
        self.job = WebSearch.objects.create(owner=self.owner, status='done', specification={'terms': ['flash']})
        self.url = f'/search/{self.job.pk}/'
        for identifier, amount, start, end in [(1, 100, 3, 1), (2, 9, 1, 3), (3, 30, 2, 2),
                                              (4, None, None, None), (5, 9, '', None)]:
            self.add(identifier, amount, start, end)

    def add(self, identifier, amount, start, end):
        data = row(identifier, announcement_id=100+identifier, total_price=amount,
                   announcement_publish_date=self.now.isoformat(),
                   offer_start_date=(self.now+timedelta(days=start)).isoformat() if isinstance(start, int) else start,
                   offer_end_date=(self.now+timedelta(days=end)).isoformat() if end else None)
        SearchHit.objects.create(search=self.job, lot=store_lot(data), terms=['flash'])

    def fetch(self, sort='', **params):
        return self.client.get(self.url, {'sort': sort, **params})

    def ids(self, response):
        self.assertEqual(response.status_code, 200)
        return [hit.lot.source_id for hit in response.context['page']]

    def test_amount_is_numeric_and_unknown_values_stay_last_in_both_directions(self):
        self.assertEqual(self.ids(self.fetch('amount')), [5, 2, 3, 1, 4])
        self.assertEqual(self.ids(self.fetch('-amount')), [1, 3, 5, 2, 4])

    def test_pending_search_polls_back_off_and_completed_search_stops(self):
        for age,delay in [(0,1000),(10,2000),(40,5000)]:
            WebSearch.objects.filter(pk=self.job.pk).update(status='running',created_at=timezone.now()-timedelta(seconds=age))
            response=self.client.get(self.url,HTTP_HX_REQUEST='true')
            root=BeautifulSoup(response.content,'html.parser').select_one('#search-results')
            self.assertTrue(root.has_attr('data-background-poll'))
            self.assertEqual(root['hx-trigger'],f'load delay:{delay}ms, app:resume')
            self.assertFalse(root.has_attr('hx-sync'),'Polling must not suppress POST forms inside the results')
        WebSearch.objects.filter(pk=self.job.pk).update(status='done')
        root=BeautifulSoup(self.client.get(self.url,HTTP_HX_REQUEST='true').content,'html.parser').select_one('#search-results')
        self.assertFalse(root.has_attr('data-background-poll'))
        self.assertFalse(root.has_attr('hx-trigger'))

    def test_application_start_and_end_are_independent_and_unknown_dates_last(self):
        self.assertEqual(self.ids(self.fetch('start')), [2, 3, 1, 5, 4])
        self.assertEqual(self.ids(self.fetch('-start')), [1, 3, 2, 5, 4])
        self.assertEqual(self.ids(self.fetch('end')), [1, 3, 2, 5, 4])
        self.assertEqual(self.ids(self.fetch('-end')), [2, 3, 1, 5, 4])

    def test_default_invalid_and_reset_use_original_publication_order(self):
        for sort in ['', '-published', 'unknown', '--amount', 'lot__amount']:
            self.assertEqual(self.ids(self.fetch(sort)), [5, 4, 3, 2, 1])
            self.assertEqual(self.fetch(sort).context['result_sort'], '')

    def test_sort_changes_direction_keeps_filter_and_resets_page(self):
        response = self.fetch('amount', remaining='2', page='9')
        soup = BeautifulSoup(response.content, 'html.parser')
        amount = soup.select_one('th a.sort-heading')
        query = parse_qs(urlsplit(amount['href']).query)
        self.assertEqual(query, {'sort': ['-amount'], 'per_page':['20']})
        self.assertEqual(amount.find_parent('th')['aria-sort'], 'ascending')
        self.assertEqual(amount['hx-target'], '#search-results')
        self.assertEqual(len(soup.select('.date-sort-links a')), 2)
        self.assertContains(response, 'По сумме ↑')

    def test_sort_applies_to_whole_result_and_survives_pagination_cards_and_filters(self):
        for identifier in range(6, 32):
            self.add(identifier, 200+identifier, 1, 1)
        response = self.fetch('amount', remaining='2')
        self.assertEqual(response.context['page'].paginator.count, 31)
        self.assertEqual(self.ids(response)[:2], [5, 2])
        soup = BeautifulSoup(response.content, 'html.parser')
        next_link = soup.select_one('.pagination a')
        self.assertEqual(parse_qs(urlsplit(next_link['href']).query),
                         {'page': ['2'], 'sort': ['amount'], 'per_page':['20']})
        self.assertEqual(self.ids(self.fetch('amount', remaining='2', page='2')), list(range(22,32))+[4])
        for link in soup.select('.view-switch a, .deadline-filters a'):
            self.assertEqual(parse_qs(urlsplit(link['href']).query)['sort'], ['amount'])
        self.assertContains(self.fetch('amount', view='cards'), 'results-grid')

    def test_htmx_changes_only_results_without_starting_another_search(self):
        with patch('tender_search.core.views.tasks.web_search.delay') as enqueue:
            response = self.client.get(self.url, {'sort': '-amount'}, HTTP_HX_REQUEST='true')
        self.assertEqual(self.ids(response), [1, 3, 5, 2, 4])
        self.assertNotContains(response, '<html')
        enqueue.assert_not_called()
        self.assertEqual(WebSearch.objects.count(), 1)
        self.assertEqual(SearchHit.objects.count(), 5)

    def test_pending_poll_preserves_sort_and_other_users_cannot_read_results(self):
        self.job.status = 'running'
        self.job.save()
        response = self.fetch('-end', remaining='1')
        soup = BeautifulSoup(response.content, 'html.parser')
        query = parse_qs(urlsplit(soup.select_one('#search-results')['hx-get']).query)
        self.assertEqual(query['sort'], ['-end'])
        self.assertNotIn('remaining', query)
        self.assertEqual(self.ids(response), [])
        self.client.force_login(get_user_model().objects.create_user('another-owner'))
        self.assertEqual(self.fetch('amount').status_code, 404)

    def test_waiting_illustrations_are_only_present_while_search_is_pending(self):
        self.job.status = 'running'
        self.job.save()
        soup = BeautifulSoup(self.fetch().content, 'html.parser')
        self.assertEqual(len(soup.select('.search-progress-art [data-progress-frame]')), 3)
        self.assertIn('empty-results', soup.find('h2', string='Идёт поиск').parent['class'])
        self.job.status = 'done'
        self.job.save()
        self.assertNotContains(self.fetch(), 'data-progress-frame')
