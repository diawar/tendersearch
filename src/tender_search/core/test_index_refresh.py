from datetime import timedelta
from unittest.mock import Mock

from django.test import TestCase
from django.utils import timezone
from tender_search.client import Page, SourceError
from .index_service import collect_index_page, DETAIL_MAX_AGE
from .models import AnnouncementLotCache, IndexedLot, ProcurementIndex
from .test_announcement_index import announcement, detail
from .test_index import row


class IndexRefreshTests(TestCase):
    def restart(self, resource):
        now = timezone.now()
        ProcurementIndex.objects.update_or_create(pk=1, defaults={
            'cycle': now.isoformat(), 'started_at': now, 'lease': '', 'lease_until': None,
            'pending': [{'resource': resource, 'lo': now.isoformat(), 'hi': None, 'offset': 0}]})

    def test_unchanged_announcements_reuse_cache_across_cycles_without_extending_ttl(self):
        ann = announcement()
        api = Mock()
        api.search.return_value = Page(1, [ann], None)
        api.announcement_lots.return_value = [detail()]
        self.restart('announcements')
        self.assertTrue(collect_index_page(api))
        checked = AnnouncementLotCache.objects.get().checked_at
        for _ in range(3):
            self.restart('announcements')
            self.assertTrue(collect_index_page(api))
        self.assertEqual(api.announcement_lots.call_count, 1)
        self.assertEqual(AnnouncementLotCache.objects.get().checked_at, checked)
        self.assertTrue(IndexedLot.objects.get().current)
        self.assertEqual(api.search.call_args.kwargs['limit'], 100)

    def test_announcement_change_and_expired_cache_refresh_membership_and_hidden_description(self):
        ann = announcement()
        api = Mock()
        api.search.return_value = Page(1, [ann], None)
        api.announcement_lots.return_value = [detail()]
        self.restart('announcements')
        self.assertTrue(collect_index_page(api))
        ann = dict(ann, status_id=6)
        api.search.return_value = Page(1, [ann], None)
        api.announcement_lots.return_value = [detail(2)]
        self.restart('announcements')
        self.assertTrue(collect_index_page(api))
        self.assertFalse(IndexedLot.objects.get(pk=1).current)
        self.assertTrue(IndexedLot.objects.get(pk=2).current)
        AnnouncementLotCache.objects.update(checked_at=timezone.now()-DETAIL_MAX_AGE-timedelta(seconds=1))
        api.announcement_lots.return_value = [dict(detail(2), description='Изменённая спецификация')]
        self.restart('announcements')
        self.assertTrue(collect_index_page(api))
        self.assertEqual(api.announcement_lots.call_count, 3)
        self.assertIn('Изменённая спецификация', IndexedLot.objects.get(pk=2).row['description_ru'])

    def test_lot_registry_change_refreshes_details_but_identical_rows_do_not(self):
        lot = row()
        api = Mock()
        api.search.return_value = Page(1, [lot], None)
        api.announcement_lots.return_value = [detail()]
        for _ in range(2):
            self.restart('lots')
            self.assertTrue(collect_index_page(api))
        self.assertEqual(api.announcement_lots.call_count, 1)
        api.search.return_value = Page(1, [dict(lot, total_price=6000)], None)
        api.announcement_lots.side_effect = SourceError('temporary')
        self.restart('lots')
        self.assertFalse(collect_index_page(api))
        self.assertEqual(ProcurementIndex.objects.get().pending[0]['offset'], 0)
        api.announcement_lots.side_effect = None
        api.announcement_lots.return_value = [dict(detail(), description='Новые условия')]
        self.assertTrue(collect_index_page(api))
        self.assertEqual(IndexedLot.objects.get().row['total_price'], 6000)
        self.assertIn('Новые условия', IndexedLot.objects.get().row['description_ru'])

    def test_new_lot_in_existing_announcement_is_requested_even_with_recent_cache(self):
        lot = row()
        api = Mock()
        api.search.return_value = Page(1, [lot], None)
        api.announcement_lots.return_value = [detail()]
        self.restart('lots')
        self.assertTrue(collect_index_page(api))
        api.search.return_value = Page(2, [lot, dict(lot, id=2)], None)
        api.announcement_lots.return_value = [detail(), detail(2)]
        self.restart('lots')
        self.assertTrue(collect_index_page(api))
        self.assertEqual(api.announcement_lots.call_count, 2)
        self.assertEqual(IndexedLot.objects.filter(current=True).count(), 2)

    def test_two_registry_cycles_keep_raw_fingerprint_after_enrichment(self):
        lot, ann = row(), announcement()
        api = Mock()
        api.announcement_lots.return_value = [detail()]
        for _ in range(2):
            self.restart('lots')
            state = ProcurementIndex.objects.get()
            state.pending.append(dict(state.pending[0], resource='announcements'))
            state.save()
            api.search.side_effect = [Page(1, [lot], None), Page(1, [ann], None)]
            self.assertTrue(collect_index_page(api))
            self.assertTrue(collect_index_page(api))
            self.assertIsNotNone(ProcurementIndex.objects.get().completed_at)
        self.assertEqual(api.announcement_lots.call_count, 1)
        self.assertTrue(IndexedLot.objects.get().registry_fingerprint)
