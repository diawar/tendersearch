from copy import deepcopy
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from .index_service import execute_local_search
from .models import IndexedLot, MedicalIndexedLot, ReferenceEntry, SearchCandidateCache, WebSearch
from .query_search import search_sources
from .test_index import row, put


class SearchProjectionTests(TestCase):
    def test_projection_tracks_saves_and_invalidates_direct_json_updates(self):
        record = put(row(name_ru='МЫШКА', customers=[{'bin': '123', 'id': 5}]))
        self.assertIn('мышка', record.search_text)
        self.assertEqual(record.search_meta['bins'], ['123'])
        self.assertEqual(record.search_meta['amount'], '5000')
        changed = dict(record.row, name_ru='Пернетақта')
        IndexedLot.objects.filter(pk=record.pk).update(row=changed)
        record.refresh_from_db()
        self.assertIsNone(record.search_text)
        self.assertIsNone(record.search_meta)
        record.save(update_fields=['row'])
        record.refresh_from_db()
        self.assertIn('пернетақта', record.search_text)


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL SQL prefilters and invalidation triggers')
class PostgreSQLSearchTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('sql-owner')
        self.now = timezone.now()
        self.spec = {'terms': ['flash'], 'exclude': [], 'kind': 'contains', 'mode': 'any',
                     'filters': {}, 'status_selection': ['open:9']}
        cases = [
            {}, {'description_ru': 'Промышленный'},
            {'total_price': '5000.00000000000001'}, {'total_price': None},
            {'_has_advance': True}, {'_has_advance': 1},
            {'customers': [{'id': 1, 'iin_bin': '123456789012'}]},
            {'customers': [{'id': 2, 'bin': '000000000000'}, {'id': 3, 'bin': '123456789012'}]},
            {'delivery_addresses': [{'id': 777, 'code': '191010100'}]},
            {'delivery_addresses': [{'id': '888', 'code': '390000000'}]},
            {'purchase_subject_id': 2}, {'purchase_subject_id': None},
            {'purchase_subject_id': 1}, {'purchase_subject_id': True},
            {'plan_items': [{'purchase_subject': {'id': 3}}]},
            {'name_ru': 'е\u0308лка', 'description_ru': 'МЫШКА ҚАЗАҚША'},
            {'offer_start_date': (self.now+timedelta(hours=1)).isoformat()},
            {'name_ru': 'Беспроводная мышка'},
        ]
        for identifier, changes in enumerate(cases, 1):
            put(row(identifier, announcement_id=100+identifier, **changes))
        data = row(1, announcement_id=500, _source='med_ecc')
        MedicalIndexedLot.objects.create(source='med_ecc', source_id=1, row=data,
            deadline=data['offer_end_date'], status_id=9, method_id=12, seen_cycle='test')
        ReferenceEntry.objects.create(kind='katos', source_id=10, name='Region', raw={'code': '190000000'})

    def search(self, spec, enabled=True, cache=False):
        job = WebSearch.objects.create(owner=self.owner, specification=spec)
        with override_settings(SEARCH_DB_PREFILTERS=enabled, SEARCH_CANDIDATE_CACHE=cache):
            execute_local_search(job.pk)
        job.refresh_from_db()
        identities = {(hit.lot.announcement.source, hit.lot.source_id, tuple(hit.terms))
                      for hit in job.hits.select_related('lot__announcement')}
        return job, identities

    def test_prefilters_preserve_exact_results_for_filter_combinations(self):
        conditions = [{}, {'only_advance': True}, {'subject': '1'}, {'subject': '3'},
                      {'total_price__gte': '5000.000000000000005'}, {'total_price__lte': '5000'},
                      {'delivery_address_id__in': '10'}, {'delivery_address_id__in': '888'},
                      {'lot_number': '1-ЗЦП'}, {'announcement_number': '100'},
                      {'only_medical': True}, {'only_advance': True, 'subject': '2'}]
        for filters in conditions:
            for terms in [['flash'], ['мыш'], ['ЁЛКА'], ['flash', 'мыш'], ['%_', 'flash']]:
                spec = dict(self.spec, filters=filters, terms=terms)
                with self.subTest(filters=filters, terms=terms):
                    baseline, expected = self.search(spec, enabled=False)
                    optimized, actual = self.search(spec)
                    self.assertEqual(optimized.error, baseline.error)
                    self.assertEqual(actual, expected)
        spec = dict(self.spec, customer_bin='123456789012')
        self.assertEqual(self.search(spec)[1], self.search(spec, enabled=False)[1])

    def test_every_matching_mode_preserves_phrase_prefixes_and_order(self):
        for kind in ['contains', 'substring', 'phrase', 'word', 'field']:
            for mode in ['any', 'all']:
                for terms in [['беспроводн мыш'], ['беспроводн мыш', 'flash'], ['Беспроводная мышка']]:
                    spec = dict(self.spec, kind=kind, mode=mode, terms=terms)
                    with self.subTest(kind=kind, mode=mode, terms=terms):
                        self.assertEqual(self.search(spec)[1], self.search(spec, enabled=False)[1])

    def test_unprojected_rows_are_never_lost(self):
        IndexedLot.objects.update(search_text=None, search_meta=None, search_fingerprint=None)
        self.assertEqual(self.search(self.spec)[1], self.search(self.spec, enabled=False)[1])

    def test_inflections_and_suppletive_forms_survive_sql_and_cache(self):
        put(row(801,announcement_id=1801,name_ru='Для беспроводной мыши',description_ru=''))
        put(row(802,announcement_id=1802,name_ru='Для новых людей',description_ru=''))
        put(row(803,announcement_id=1803,name_ru='Беспроводной комплект клавиатура и мышь',description_ru=''))
        for term,identifier in [('беспроводная мышь',801),('новый человек',802)]:
            spec=dict(self.spec,kind='phrase',terms=[term])
            baseline,expected=self.search(spec,enabled=False)
            optimized,actual=self.search(spec,cache=True)
            self.assertFalse(optimized.error)
            self.assertEqual(actual,expected)
            self.assertTrue(any(item[1]==identifier for item in actual))
            self.assertFalse(any(item[1]==803 for item in actual))
            self.assertEqual(self.search(spec,cache=True)[1],expected)

    def test_cache_hits_and_index_writes_invalidate_it(self):
        first, expected = self.search(self.spec, cache=True)
        second, actual = self.search(self.spec, cache=True)
        self.assertFalse(first.source_counts['__query__']['cache_hit'])
        self.assertTrue(second.source_counts['__query__']['cache_hit'])
        self.assertEqual(actual, expected)
        data = dict(IndexedLot.objects.get(pk=2).row, description_ru='flash newly indexed')
        IndexedLot.objects.filter(pk=2).update(row=data)
        third, actual = self.search(self.spec, cache=True)
        self.assertFalse(third.source_counts['__query__']['cache_hit'])
        self.assertIn(('zakup', 2, ('flash',)), actual)
        IndexedLot.objects.filter(pk=2).delete()
        fourth, actual = self.search(self.spec, cache=True)
        self.assertFalse(fourth.source_counts['__query__']['cache_hit'])
        self.assertNotIn(('zakup', 2, ('flash',)), actual)

    def test_cache_rechecks_deadlines_and_start_times(self):
        from unittest.mock import patch
        record = IndexedLot.objects.get(pk=17)
        record.row['offer_start_date'] = (self.now+timedelta(seconds=30)).isoformat()
        record.save(update_fields=['row'])
        with override_settings(SEARCH_CANDIDATE_CACHE=True):
            sources, _, _ = search_sources(self.spec, self.now, self.owner.pk)
            self.assertTrue(any(rows.filter(source_id=17).exists() for rows in sources))
            with patch('tender_search.core.index_service.timezone.now', return_value=self.now+timedelta(seconds=60)):
                job, actual = self.search(self.spec, cache=True)
            self.assertTrue(job.source_counts['__query__']['cache_hit'])
            self.assertIn(('zakup', 17, ('flash',)), actual)
            with patch('tender_search.core.index_service.timezone.now', return_value=self.now+timedelta(days=6)):
                job, actual = self.search(self.spec, cache=True)
            self.assertFalse(actual)

    def test_cache_is_scoped_to_owner_and_conditions_and_expires(self):
        self.search(self.spec, cache=True)
        other = get_user_model().objects.create_user('sql-other')
        with override_settings(SEARCH_CANDIDATE_CACHE=True):
            _, _, info = search_sources(self.spec, timezone.now(), other.pk)
        self.assertFalse(info['cache_hit'])
        SearchCandidateCache.objects.update(expires_at=self.now-timedelta(seconds=1))
        job, _ = self.search(self.spec, cache=True)
        self.assertFalse(job.source_counts['__query__']['cache_hit'])

    def test_invalid_amount_remains_visible_as_search_error(self):
        put(row(100, total_price='invalid', name_ru='No keyword', description_ru=''))
        spec = dict(self.spec, filters={'total_price__gte': '1'})
        reference, _ = self.search(spec, enabled=False)
        optimized, _ = self.search(spec)
        self.assertTrue(reference.error)
        self.assertEqual(optimized.error, reference.error)
