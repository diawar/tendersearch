from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Lot, WebSearch
from .search_service import store_lot, store_search_hits
from .test_index import row, put
from .index_service import execute_local_search, local_matches


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SearchStorageTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('storage-owner')
        self.job = WebSearch.objects.create(owner=self.owner, specification={'terms': ['flash']})

    def test_unchanged_snapshots_need_two_queries_and_no_row_writes(self):
        rows = [row(i, announcement_id=100+i) for i in range(1, 51)]
        for value in rows:
            store_lot(value)
        with patch('tender_search.core.search_service.store_lot', side_effect=AssertionError('Redundant write')):
            with self.assertNumQueries(2):  # One SELECT and one batched INSERT.
                store_search_hits(self.job, [(value, ['flash']) for value in rows])
        self.assertEqual(self.job.hits.count(), 50)

    def test_changed_customer_and_snapshot_use_existing_storage_rules(self):
        value = row(customers=[{'id': 11, 'bin': '123456789012', 'name_ru': 'Customer'}])
        lot = store_lot(value)
        lot.customer.name = 'Other snapshot'
        lot.customer.save()
        store_search_hits(self.job, [(value, ['flash'])])
        lot.refresh_from_db()
        self.assertEqual(lot.customer.name, 'Customer')
        changed = dict(value, description_ru='New flash description', _extended_description=True)
        store_search_hits(self.job, [(changed, ['flash'])])
        lot.refresh_from_db()
        self.assertEqual(lot.description, 'New flash description')
        self.assertEqual(self.job.hits.count(), 1)

    def test_verified_card_fields_keep_precedence(self):
        value = row()
        lot = store_lot(value)
        lot.checked_at = timezone.now()
        lot.name = 'Verified name'
        lot.description = 'Verified description'
        lot.save()
        store_search_hits(self.job, [(value, ['flash'])])
        lot.refresh_from_db()
        self.assertEqual(lot.name, 'Verified name')
        self.assertEqual(lot.description, 'Verified description')

    def test_shared_customer_preserves_snapshot_order_within_batch(self):
        first = row(1, customers=[{'id': 11, 'bin': '123456789012', 'name_ru': 'First'}])
        second = row(2, customers=[{'id': 11, 'bin': '123456789012', 'name_ru': 'Second'}])
        store_lot(first)
        lot = store_lot(second)
        # Initially the cached customer matches second. Saving first must
        # invalidate that assumption before deciding to reuse second.
        store_search_hits(self.job, [(first, ['flash']), (second, ['flash'])])
        lot.refresh_from_db()
        self.assertEqual(lot.customer.name, 'Second')

    def test_same_ids_across_sources_stay_separate_and_medical_card_updates(self):
        value = row()
        med = dict(value, _source='med_ecc', _announcement={
            'name_ru': 'Medical card', 'status': {'name': 'Open'},
            'offer_end_date': value['offer_end_date']})
        for data in [value, med]:
            store_lot(data)
        store_search_hits(self.job, [(value, ['flash']), (med, ['flash'])])
        self.assertEqual(self.job.hits.count(), 2)
        updated = deepcopy(med)
        updated['_announcement']['name_ru'] = 'Updated medical card'
        store_search_hits(self.job, [(updated, ['flash'])])
        self.assertEqual(Lot.objects.get(announcement__source='med_ecc').announcement.name, 'Updated medical card')

    def test_failure_keeps_preceding_successful_matches(self):
        with self.assertRaises(Exception):
            store_search_hits(self.job, [(row(), ['flash']), (row(2, total_price='invalid'), ['flash'])])
        self.assertEqual(list(self.job.hits.values_list('lot__source_id', flat=True)), [1])

    def test_cancel_preserves_buffered_hits_and_status(self):
        for identifier in range(1, 102):
            put(row(identifier))
        calls = 0

        def match(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                WebSearch.objects.filter(pk=self.job.pk).update(status='cancelled')
            return local_matches(*args)

        with patch('tender_search.core.index_service.local_matches', side_effect=match):
            execute_local_search(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'cancelled')
        self.assertEqual(self.job.hits.count(), 100)

    def test_results_appear_together_after_completion(self):
        self.client.force_login(self.owner)
        store_search_hits(self.job, [(row(name_ru='Unique result title'), ['flash'])])
        self.job.status = 'running'
        self.job.save()
        response = self.client.get(f'/search/{self.job.pk}/')
        self.assertContains(response, 'Идёт поиск')
        self.assertContains(response, 'empty-search.svg')
        self.assertNotContains(response, 'Unique result title')
        self.job.status = 'done'
        self.job.save()
        response = self.client.get(f'/search/{self.job.pk}/', HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Unique result title')
        self.assertNotContains(response, 'empty-search.svg')

    def test_initial_and_completed_empty_states(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/search/'), 'Результаты поиска пусты')
        self.job.status = 'done'
        self.job.save()
        response = self.client.get(f'/search/{self.job.pk}/')
        self.assertContains(response, 'Результаты поиска пусты')
        self.assertContains(response, 'empty-search.svg')
        self.assertNotContains(response, 'Идёт поиск')
