from datetime import timedelta
from unittest.mock import Mock

from django.test import TestCase
from django.utils import timezone
from tender_search.client import Page, SourceError, NotFound
from .announcement_index import announcement_lot_row
from .index_service import collect_index_page, claim_index
from .models import ProcurementIndex, IndexedLot, AnnouncementLotCache
from .search_service import extended_description, lot_identity


def announcement():
    now = timezone.now()
    return {'id': 100, 'external_id': 10, 'announcement_number': '10-1',
            'name': 'Компьютерные товары', 'status_id': 9, 'system_id': 4,
            'purchase_method_id': 12, 'purchase_method_name': 'ЗЦП',
            'offer_start_date': (now-timedelta(days=1)).isoformat(),
            'offer_end_date': (now+timedelta(days=4)).isoformat(), 'publish_date': now.isoformat()}


def detail(identifier=1, status=9):
    return {'id': identifier, 'lot_number': '88-ЗЦП1', 'name': 'Коврик',
            'status': {'id': status, 'name': 'Опубликован'}, 'system': {'id': 1}, 'total_price': 5000,
            'plan_items': [{'description_ru': 'для мышки', 'prepayment': 30,
                'purchase_subject': {'id': 1},
                'organization': {'id': 30, 'iin_bin': '123456789012', 'name_ru': 'Заказчик'},
                'deliveries': [{'kato': {'id': 50, 'code': '351010000'}}]}]}


class AnnouncementIndexTests(TestCase):
    def start_announcements(self):
        now = timezone.now()
        return ProcurementIndex.objects.create(cycle='cycle', started_at=now,
            pending=[{'resource': 'announcements', 'lo': now.isoformat(), 'hi': None, 'offset': 0}])

    def test_missing_lot_is_collected_only_after_both_registries_and_replay_is_idempotent(self):
        api = Mock()
        ann = announcement()
        api.search.side_effect = [Page(0, [], None), Page(1, [ann], None)]
        api.announcement_lots.return_value = [detail(), detail(2, 6)]
        self.assertTrue(collect_index_page(api))
        self.assertIsNone(ProcurementIndex.objects.get().completed_at)
        self.assertTrue(collect_index_page(api))
        self.assertIsNotNone(ProcurementIndex.objects.get().completed_at)
        a, b = list(IndexedLot.objects.order_by('source_id'))
        self.assertIn('для мышки', a.row['description_ru'])
        self.assertEqual(a.row['system_id'], 1)  # Never replace the lot's platform with announcement EPZ.
        self.assertEqual(a.row['purchase_subject_id'], [1])
        self.assertEqual(a.row['delivery_addresses'][0]['code'], '351010000')
        self.assertEqual(lot_identity(a.row), lot_identity(b.row))
        self.assertIs(a.row['_has_advance'], True)
        state = ProcurementIndex.objects.get()
        state.pending = [{'resource': 'announcements', 'lo': state.started_at.isoformat(), 'hi': None, 'offset': 0}]
        state.save()
        api.search.side_effect = None
        api.search.return_value = Page(1, [ann], None)
        self.assertTrue(collect_index_page(api))
        self.assertEqual(IndexedLot.objects.count(), 2)
        api.announcement_lots.assert_called_once_with(100)

    def test_failure_keeps_cursor_and_old_rows_until_both_registries_finish(self):
        self.start_announcements()
        api = Mock()
        api.search.return_value = Page(1, [announcement()], None)
        api.announcement_lots.side_effect = SourceError('Temporary failure')
        self.assertFalse(collect_index_page(api))
        state = ProcurementIndex.objects.get()
        self.assertEqual(state.pending[0]['offset'], 0)
        self.assertIsNone(state.completed_at)
        api.announcement_lots.side_effect = None
        api.announcement_lots.return_value = [detail()]
        self.assertTrue(collect_index_page(api))
        state.refresh_from_db()
        self.assertEqual(state.error, '')
        self.assertIsNotNone(state.completed_at)

    def test_old_cursor_upgrade_appends_announcement_phase_once(self):
        now = timezone.now()
        ProcurementIndex.objects.create(started_at=now, pending=[{'lo': now.isoformat(), 'hi': None, 'offset': 345}])
        state = claim_index()
        self.assertEqual(state.pending[0]['offset'], 345)
        self.assertEqual([w['resource'] for w in state.pending], ['lots', 'announcements'])
        ProcurementIndex.objects.update(lease='', lease_until=None)
        self.assertEqual(len(claim_index().pending), 2)

    def test_split_preserves_resource_and_does_not_complete(self):
        self.start_announcements()
        api = Mock()
        api.search.return_value = Page(16000, [], 'next')
        self.assertTrue(collect_index_page(api))
        state = ProcurementIndex.objects.get()
        self.assertEqual([w['resource'] for w in state.pending], ['announcements', 'announcements'])
        self.assertEqual(state.pending[0]['hi'], state.pending[1]['lo'])
        self.assertIsNone(state.completed_at)

    def test_filters_must_be_confirmed_and_cards_inactive_deleted_or_expired_excluded(self):
        self.start_announcements()
        api = Mock()
        api.search.return_value = Page(1, [dict(announcement(), status_id=16)], None)
        self.assertFalse(collect_index_page(api))
        api.announcement_lots.assert_not_called()
        api.search.return_value = Page(1, [announcement()], None)
        api.announcement_lots.return_value = [detail(1, 16), dict(detail(2), is_externally_deleted=True), detail(3)]
        self.assertTrue(collect_index_page(api))
        self.assertEqual(list(IndexedLot.objects.filter(current=True).values_list('source_id', flat=True)), [3])

    def test_previous_cycle_cache_is_refreshed_and_404_does_not_block(self):
        state = self.start_announcements()
        AnnouncementLotCache.objects.create(source_id=100, rows=[detail()], checked_at=state.started_at-timedelta(seconds=1))
        api = Mock()
        api.search.return_value = Page(1, [announcement()], None)
        api.announcement_lots.side_effect = NotFound('HTTP 404')
        self.assertTrue(collect_index_page(api))
        self.assertFalse(IndexedLot.objects.exists())
        api.announcement_lots.assert_called_once_with(100)

    def test_title_does_not_leak_into_matching_description(self):
        ann = dict(announcement(), name='Клавиатура и мышь')
        lot = dict(detail(), name='Стул', plan_items=[])
        normalized = announcement_lot_row(ann, lot)
        self.assertNotIn('мышь', extended_description(normalized, lot))

    def test_reconciliation_keeps_union_and_removes_lost_cards(self):
        from .test_index import row, put
        old = put(row(99))
        api = Mock()
        api.search.side_effect = [Page(1, [row(50, announcement_id=200)], None),
                                Page(1, [announcement()], None)]
        api.announcement_lots.side_effect = [[row(50)], [detail()]]
        self.assertTrue(collect_index_page(api))
        old.refresh_from_db()
        self.assertTrue(old.current)  # The first registry cannot reconcile the whole index.
        self.assertTrue(collect_index_page(api))
        self.assertEqual(set(IndexedLot.objects.filter(current=True).values_list('source_id', flat=True)), {1, 50})

    def test_permanently_skipped_ids_stay_skipped_in_announcement_traversal(self):
        from .test_index import row, put
        self.start_announcements()
        skipped = put(row(1))
        skipped.details_skipped = True
        skipped.current = False
        skipped.save()
        api = Mock()
        ann = announcement()
        api.search.return_value = Page(1, [ann], None)
        api.announcement_lots.return_value = [detail()]
        self.assertTrue(collect_index_page(api))
        skipped.refresh_from_db()
        self.assertFalse(skipped.current)
