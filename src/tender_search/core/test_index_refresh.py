from datetime import timedelta
from unittest.mock import Mock

from django.test import TestCase
from django.utils import timezone
from tender_search.client import Page, SourceError
from .index_service import collect_index_page, claim_index, index_coverage, registry_fingerprint
from .detail_index import refresh_details, claim_detail, DETAIL_MAX_AGE
from .models import AnnouncementLotCache, AnnouncementRefresh, IndexedLot, ProcurementIndex
from .test_announcement_index import announcement, detail
from .test_index import row


class IndexRefreshTests(TestCase):
    def setUp(self):
        self.ann, self.lot = announcement(), row()
        self.api = Mock()
        self.api.announcement.return_value = self.ann
        self.api.announcement_lots.return_value = [detail()]

    def sweep(self, resource='announcements', rows=None):
        now = timezone.now()
        ProcurementIndex.objects.update_or_create(pk=1, defaults={
            'cycle': now.isoformat(), 'started_at': now, 'lease': '', 'lease_until': None,
            'pending': [{'resource': resource, 'lo': now.isoformat(), 'hi': None, 'offset': 0}]})
        rows = [self.ann] if rows is None else rows
        self.api.search.return_value = Page(len(rows), rows, None)
        self.assertTrue(collect_index_page(self.api))

    def test_registry_never_requests_cards_and_new_lot_is_immediately_available(self):
        self.sweep('lots', [self.lot])
        self.api.announcement.assert_not_called()
        self.api.announcement_lots.assert_not_called()
        lot = IndexedLot.objects.get()
        self.assertTrue(lot.current)
        self.assertTrue(lot.row['_details_pending'])
        self.assertEqual(lot.row['name_ru'], self.lot['name_ru'])
        self.assertFalse(index_coverage()['ready'])
        self.assertTrue(refresh_details(self.api))
        self.assertTrue(index_coverage()['ready'])

    def test_unchanged_registry_does_not_extend_ttl_or_reset_queue(self):
        self.sweep()
        self.assertTrue(refresh_details(self.api))
        task = AnnouncementRefresh.objects.get()
        for _ in range(3):
            self.sweep()
            self.assertFalse(refresh_details(self.api))
        actual = AnnouncementRefresh.objects.get()
        self.assertEqual(actual.due_at, task.due_at)
        self.assertEqual(actual.revision, task.revision)
        self.assertEqual(AnnouncementLotCache.objects.get().checked_at, task.checked_at)
        self.api.announcement_lots.assert_called_once()
        self.assertTrue(IndexedLot.objects.get().current)

    def test_existing_cache_bootstraps_as_periodic_not_urgent_work(self):
        from .index_service import save_index_row
        from .announcement_index import announcement_lot_row
        save_index_row(announcement_lot_row(self.ann, detail()), detail(), 'old')
        checked = timezone.now()
        AnnouncementLotCache.objects.create(source_id=100, rows=[detail()],
            checked_at=checked, registry_fingerprint=registry_fingerprint(self.ann))
        self.sweep()
        task = AnnouncementRefresh.objects.get()
        self.assertFalse(task.urgent)
        self.assertEqual(task.due_at, checked+DETAIL_MAX_AGE)
        self.assertFalse(IndexedLot.objects.get().row['_details_pending'])
        self.assertFalse(refresh_details(self.api))
        self.api.announcement_lots.assert_not_called()

    def test_hidden_description_changes_are_rechecked_without_registry_changes(self):
        self.sweep()
        self.assertTrue(refresh_details(self.api))
        AnnouncementRefresh.objects.update(due_at=timezone.now()-timedelta(seconds=1))
        self.api.announcement_lots.return_value = [dict(detail(), description='Новые требования')]
        self.assertTrue(refresh_details(self.api))
        self.assertIn('Новые требования', IndexedLot.objects.get().row['description_ru'])

    def test_membership_status_deleted_and_empty_card(self):
        self.sweep()
        self.assertTrue(refresh_details(self.api))
        self.ann['status_id'] = 6
        self.sweep()
        self.api.announcement_lots.return_value = [detail(2), detail(3, 16), dict(detail(4), is_externally_deleted=True)]
        self.assertTrue(refresh_details(self.api))
        self.assertEqual(list(IndexedLot.objects.filter(current=True).values_list('source_id', flat=True)), [2])
        self.assertTrue(IndexedLot.objects.get(pk=1).details_skipped)
        AnnouncementRefresh.objects.update(due_at=timezone.now())
        self.api.announcement_lots.return_value = []
        self.assertTrue(refresh_details(self.api))
        self.assertFalse(IndexedLot.objects.filter(current=True).exists())

    def test_registry_updates_amount_despite_card_failure_and_does_not_reset_backoff(self):
        self.sweep('lots', [self.lot])
        self.assertTrue(refresh_details(self.api))
        self.lot['total_price'] = 6000
        self.sweep('lots', [self.lot])
        self.api.announcement_lots.side_effect = SourceError('temporary')
        self.assertFalse(refresh_details(self.api))
        task = AnnouncementRefresh.objects.get()
        self.assertEqual(IndexedLot.objects.get().row['total_price'], 6000)
        self.assertEqual(ProcurementIndex.objects.get().pending, [])
        self.sweep('lots', [self.lot])
        self.assertEqual(AnnouncementRefresh.objects.get().due_at, task.due_at)
        self.assertFalse(refresh_details(self.api))
        self.api.announcement_lots.side_effect = None
        self.api.announcement_lots.return_value = [dict(detail(), total_price=6000)]
        AnnouncementRefresh.objects.update(due_at=timezone.now())
        self.assertTrue(refresh_details(self.api))
        self.assertFalse(AnnouncementRefresh.objects.get().error)

    def test_new_lot_coalesces_with_existing_announcement_even_with_recent_cache(self):
        self.sweep('lots', [self.lot])
        self.assertTrue(refresh_details(self.api))
        self.sweep('lots', [self.lot, dict(self.lot, id=2)])
        self.assertEqual(AnnouncementRefresh.objects.count(), 1)
        self.api.announcement_lots.return_value = [detail(), detail(2)]
        self.assertTrue(refresh_details(self.api))
        self.assertEqual(self.api.announcement_lots.call_count, 2)
        self.assertEqual(IndexedLot.objects.filter(current=True).count(), 2)

    def test_raw_fingerprint_survives_enrichment_and_both_registry_cycles(self):
        for _ in range(2):
            self.sweep('lots', [self.lot])
            self.sweep()
            refresh_details(self.api)
        self.api.announcement_lots.assert_called_once()
        self.assertEqual(IndexedLot.objects.get().registry_fingerprint, registry_fingerprint(self.lot))

    def test_changed_while_fetching_is_not_overwritten_or_acknowledged(self):
        self.sweep('lots', [self.lot])
        def respond(identifier):
            self.lot['total_price'] = 9000
            self.sweep('lots', [self.lot])
            return [dict(detail(), total_price=5000)]
        self.api.announcement_lots.side_effect = respond
        self.assertFalse(refresh_details(self.api))
        self.assertEqual(IndexedLot.objects.get().row['total_price'], 9000)
        self.assertTrue(AnnouncementRefresh.objects.get().urgent)
        self.assertIsNone(AnnouncementRefresh.objects.get().checked_at)
        self.api.announcement_lots.side_effect = None
        self.api.announcement_lots.return_value = [dict(detail(), total_price=9000)]
        self.assertTrue(refresh_details(self.api))
        self.assertFalse(AnnouncementRefresh.objects.get().urgent)

    def test_removed_announcement_during_request_is_not_reactivated(self):
        self.sweep()
        def respond(identifier):
            self.sweep(rows=[])
            return [detail()]
        self.api.announcement_lots.side_effect = respond
        self.assertFalse(refresh_details(self.api))
        self.assertFalse(AnnouncementRefresh.objects.get().active)
        self.assertFalse(IndexedLot.objects.filter(current=True).exists())

    def test_worker_crash_lease_expiry_and_pause(self):
        self.sweep()
        first = claim_detail()
        self.assertIsNotNone(first)
        self.assertIsNone(claim_detail())
        AnnouncementRefresh.objects.update(lease_until=timezone.now()-timedelta(seconds=1))
        ProcurementIndex.objects.update(paused=True)
        self.assertIsNone(claim_detail())
        ProcurementIndex.objects.update(paused=False)
        second = claim_detail()
        self.assertIsNotNone(second)
        self.assertNotEqual(first.lease, second.lease)

    def test_expired_worker_cannot_overwrite_new_lease(self):
        self.sweep()
        def respond(identifier):
            AnnouncementRefresh.objects.update(lease_until=timezone.now()-timedelta(seconds=1))
            self.new_lease = claim_detail().lease
            return [detail()]
        self.api.announcement_lots.side_effect = respond
        self.assertFalse(refresh_details(self.api))
        self.assertEqual(AnnouncementRefresh.objects.get().lease, self.new_lease)
        self.assertFalse(IndexedLot.objects.exists())

    def test_urgent_changes_are_selected_before_periodic_checks(self):
        self.sweep()
        self.assertTrue(refresh_details(self.api))
        AnnouncementRefresh.objects.update(due_at=timezone.now()-timedelta(days=1))
        AnnouncementRefresh.objects.create(source_id=200, deadline=timezone.now()+timedelta(days=1))
        self.assertEqual(claim_detail().source_id, 200)

    def test_malformed_card_preserves_previous_rows_and_retries(self):
        self.sweep()
        self.assertTrue(refresh_details(self.api))
        AnnouncementRefresh.objects.update(due_at=timezone.now())
        self.api.announcement_lots.return_value = [dict(detail(2), status=None)]
        self.assertFalse(refresh_details(self.api))
        self.assertTrue(IndexedLot.objects.get(pk=1).current)
        self.assertTrue(AnnouncementRefresh.objects.get().error)

    def test_page_mutations_and_queue_are_atomic_on_bad_continuation(self):
        now = timezone.now()
        ProcurementIndex.objects.create(started_at=now, cycle='x', pending=[
            {'resource': 'lots', 'lo': now.isoformat(), 'hi': None, 'offset': 9900}])
        self.api.search.return_value = Page(2, [self.lot], 'next')
        self.assertFalse(collect_index_page(self.api))
        self.assertFalse(IndexedLot.objects.exists())
        self.assertFalse(AnnouncementRefresh.objects.exists())
        self.assertEqual(ProcurementIndex.objects.get().pending[0]['offset'], 9900)

    def test_empty_continuation_does_not_complete_registry(self):
        self.api.search.return_value = Page(2, [], 'next')
        self.assertFalse(collect_index_page(self.api))
        self.assertIsNone(ProcurementIndex.objects.get().completed_at)

    def test_pipeline_upgrade_restarts_registry_without_removing_existing_data(self):
        now = timezone.now()
        ProcurementIndex.objects.create(pipeline_version=0, completed_at=now, started_at=now,
            pending=[{'resource': 'announcements', 'lo': now.isoformat(), 'hi': None, 'offset': 400}])
        state = claim_index()
        self.assertEqual(state.pipeline_version, 2)
        self.assertEqual(state.pending[0]['offset'], 0)
        self.assertEqual([w['resource'] for w in state.pending], ['lots', 'announcements'])
        self.assertEqual(state.completed_at, now)

    def test_new_registry_metadata_updates_dates_without_borrowing_announcement_status(self):
        self.sweep()
        self.assertTrue(refresh_details(self.api))
        self.ann.update(status_id=6, purchase_method_id=33,
            offer_end_date=(timezone.now()+timedelta(days=2)).isoformat())
        self.sweep()
        lot = IndexedLot.objects.get()
        self.assertEqual(lot.status_id, 9)
        self.assertEqual(lot.method_id, 33)
        self.assertEqual(lot.row['offer_end_date'], self.ann['offer_end_date'])
        self.assertTrue(lot.row['_details_pending'])

    def test_expired_tasks_are_not_fetched_or_blocking_coverage(self):
        self.sweep()
        self.assertTrue(refresh_details(self.api))
        AnnouncementRefresh.objects.update(deadline=timezone.now()-timedelta(seconds=1), urgent=True, due_at=timezone.now())
        self.assertFalse(refresh_details(self.api))
        self.assertTrue(index_coverage()['ready'])
