from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from .models import SearchHit, WebSearch
from .search_service import store_lot
from .test_index import row


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DeadlineFilterTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.owner = get_user_model().objects.create_user('deadline-owner')
        self.client.force_login(self.owner)
        self.job = WebSearch.objects.create(owner=self.owner, status='done', specification={'terms': ['flash']})
        self.url = f'/search/{self.job.pk}/'
        deadlines = [self.now-timedelta(seconds=1), self.now, None,
                     self.now+timedelta(seconds=1), self.now+timedelta(days=1),
                     self.now+timedelta(days=1, seconds=1), self.now+timedelta(days=2),
                     self.now+timedelta(days=2, seconds=1)]
        for identifier, end in enumerate(deadlines, 1):
            self.add_hit(identifier, end)

    def add_hit(self, identifier, end):
        data = row(identifier, announcement_id=100+identifier,
                   offer_end_date=end.isoformat() if end else None)
        SearchHit.objects.create(search=self.job, lot=store_lot(data), terms=['flash'])

    def fetch(self, remaining='', **headers):
        with patch('tender_search.core.views.timezone.now', return_value=self.now):
            return self.client.get(self.url, {'remaining': remaining}, **headers)

    def ids(self, response):
        return {hit.lot.source_id for hit in response.context['page']}

    def test_rolling_windows_include_upper_boundary_and_exclude_expired(self):
        self.assertEqual(self.ids(self.fetch('1')), {4, 5})
        self.assertEqual(self.ids(self.fetch('2')), {4, 5, 6, 7})
        self.assertEqual(self.ids(self.fetch()), set(range(1, 9)))

    def test_filters_do_not_restart_search_or_change_saved_results(self):
        with patch('tender_search.core.views.tasks.web_search.delay') as task:
            response = self.fetch('1', HTTP_HX_REQUEST='true')
        task.assert_not_called()
        self.assertTemplateUsed(response, 'core/_results.html')
        self.assertContains(response, 'До 24 часов')
        self.assertEqual(self.job.hits.count(), 8)
        self.job.refresh_from_db()
        self.assertEqual(self.job.specification, {'terms': ['flash']})
        for value in ['invalid', '0', '-1', '3']:
            self.assertEqual(self.ids(self.fetch(value)), set(range(1, 9)))

    def test_partial_filter_does_not_load_location_dictionaries(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=['is_staff'])
        with CaptureQueriesContext(connection) as queries:
            response = self.fetch('1', HTTP_HX_REQUEST='true')
        self.assertEqual(self.ids(response), {4, 5})
        self.assertFalse(any('core_referenceentry' in query['sql'].lower() for query in queries))
        self.assertFalse(any('core_indexedlot' in query['sql'].lower() or 'core_medicalindexedlot' in query['sql'].lower()
                             for query in queries))
        self.assertNotContains(response, 'Проверено записей')

    def test_view_switch_and_pagination_keep_filter(self):
        for identifier in range(20, 46):
            self.add_hit(identifier, self.now+timedelta(hours=4))
        response = self.fetch('1')
        self.assertEqual(response.context['page'].paginator.count, 28)
        self.assertContains(response, '?page=2&amp;remaining=1#search-results')
        self.assertContains(response, '?view=cards&amp;remaining=1#search-results')
        with patch('tender_search.core.views.timezone.now', return_value=self.now):
            response = self.client.get(self.url, {'remaining': '1', 'page': '2', 'view': 'cards'})
        self.assertEqual(len(response.context['page']), 3)
        self.assertContains(response, 'results-grid')
        self.assertEqual(response.context['remaining_days'], '1')

    def test_time_is_recomputed_and_polling_retains_filter(self):
        self.job.status = 'running'
        self.job.save()
        response = self.fetch('2')
        self.assertContains(response, 'page=1&amp;remaining=2')
        self.assertContains(response, 'Идёт поиск')
        self.job.status = 'done'
        self.job.save()
        self.now += timedelta(days=1)
        self.assertEqual(self.ids(self.fetch('1')), {6, 7})
