from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Document, DocumentVersion, WebSearch, IndexedLot, MedicalIndexedLot
from .test_index import row, put
from .search_service import store_lot
from .index_service import execute_local_search
from .technical_documents import matching_lots
from tender_search.matching import PreparedMatcher


class DocumentCandidateTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('document-search-owner')
        for identifier, name in [(1, 'Клавиатура'), (2, 'Мышь'), (3, 'Стол'), (4, 'Ремонт')]:
            data = row(identifier, name_ru=name, description_ru='', announcement_id=100+identifier)
            put(data)
            lot = store_lot(data)
            doc = Document.objects.create(announcement=lot.announcement, lot=lot,
                source_id=str(identifier), name='tech.txt', kind='technical', url='https://example.test/tech')
            DocumentVersion.objects.create(document=doc, sha256=str(identifier)*64,
                text='Мышь беспроводная Straße И\u0306од' if identifier not in (2, 3) else 'Нет совпадения')
        # A common announcement document must also make its lots candidates.
        self.common = Document.objects.create(announcement=store_lot(row(3, announcement_id=103)).announcement,
            source_id='common', name='common.txt', kind='technical', url='https://example.test/common')
        DocumentVersion.objects.create(document=self.common, sha256='c'*64, text='Беспроводная мышка')

    def search(self, terms, *, enabled, kind='contains', mode='any', filters=None):
        spec = {'terms': terms, 'exclude': ['ремонт'], 'kind': kind, 'mode': mode,
            'filters': {'in_documents': True, **(filters or {})}, 'status_selection': ['open:9']}
        job = WebSearch.objects.create(owner=self.owner, specification=spec)
        with override_settings(SEARCH_DB_PREFILTERS=enabled, SEARCH_CANDIDATE_CACHE=False):
            execute_local_search(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.error, '')
        return {(hit.lot.announcement.source, hit.lot.source_id, tuple(hit.terms)) for hit in job.hits.select_related('lot__announcement')}

    def test_optimized_and_full_scan_match_for_words_phrases_unicode_and_filters(self):
        for terms, kind, mode in [(['мыш'], 'contains', 'any'), (['беспроводн мыш'], 'phrase', 'any'),
            (['strasse'], 'contains', 'any'), (['йод'], 'contains', 'any'),
            (['мыш', 'клавиатур'], 'contains', 'any'), (['мыш', 'беспроводн'], 'contains', 'all')]:
            for filters in [{}, {'total_price__lte': 5000}, {'remaining_hours': 24}]:
                with self.subTest(terms=terms, kind=kind, mode=mode, filters=filters):
                    self.assertEqual(self.search(terms, enabled=True, kind=kind, mode=mode, filters=filters),
                        self.search(terms, enabled=False, kind=kind, mode=mode, filters=filters))
        self.assertEqual({item[1] for item in self.search(['мыш'], enabled=True)}, {1, 2, 3})

    def test_latest_empty_version_hides_older_match_and_null_projection_is_safe(self):
        version = DocumentVersion.objects.filter(document__source_id='1').get()
        DocumentVersion.objects.filter(pk=version.pk).update(text='Пернетақта')
        version.refresh_from_db()
        self.assertIsNone(version.search_text)
        self.assertIn(1, {item[1] for item in self.search(['пернетақ'], enabled=True)})
        DocumentVersion.objects.create(document=version.document, sha256='z'*64, text='')
        self.assertNotIn(1, {item[1] for item in self.search(['пернетақ'], enabled=True)})

    def test_skipped_expired_and_medical_candidates_keep_source_scope(self):
        IndexedLot.objects.filter(pk=1).update(details_skipped=True)
        IndexedLot.objects.filter(pk=2).update(deadline=timezone.now()-timedelta(seconds=1))
        data = row(1, announcement_id=1001, name_ru='Тест', description_ru='', _source='med_ecc')
        MedicalIndexedLot.objects.create(source='med_ecc', source_id=1, row=data,
            deadline=data['offer_end_date'], status_id=9, method_id=12)
        lot = store_lot(data)
        doc = Document.objects.create(announcement=lot.announcement, lot=lot,
            source_id='medical', name='technical.txt', kind='technical', url='https://example.test/medical')
        DocumentVersion.objects.create(document=doc, sha256='m'*64, text='Мышь')
        self.assertEqual(self.search(['мыш'], enabled=True), self.search(['мыш'], enabled=False))
        result = self.search(['мыш'], enabled=True, filters={'only_medical': True})
        self.assertEqual({item[:2] for item in result}, {('med_ecc', 1)})

    def test_document_phase_can_be_cancelled_before_matching(self):
        matcher = PreparedMatcher({'terms': ['мыш']})
        self.assertIsNone(matching_lots(matcher, is_active=lambda: False))

    @override_settings(SEARCH_CACHE_MAX_CANDIDATES=1)
    def test_broad_query_falls_back_without_truncating_results(self):
        self.assertEqual(self.search(['мыш'], enabled=True), self.search(['мыш'], enabled=False))

    def test_projection_backfill_preserves_concurrent_extraction(self):
        from io import StringIO
        from unittest.mock import patch
        from django.core.management import call_command
        from tender_search.matching import normalize
        version = DocumentVersion.objects.get(document__source_id='1')
        DocumentVersion.objects.filter(pk=version.pk).update(search_text=None)
        def newer_text(text):
            DocumentVersion.objects.filter(pk=version.pk).update(text='Новые требования')
            return normalize(text)
        with patch('tender_search.core.management.commands.backfill_document_search.normalize', side_effect=newer_text):
            call_command('backfill_document_search', stdout=StringIO())
        version.refresh_from_db()
        self.assertEqual(version.text, 'Новые требования')
        self.assertIsNone(version.search_text)
        call_command('backfill_document_search', stdout=StringIO())
        version.refresh_from_db()
        self.assertEqual(version.search_text, 'новые требования')
