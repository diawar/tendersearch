from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .coverage_snapshot import display_snapshot, refresh_snapshot
from .models import ProcurementCoverageSnapshot, ProcurementIndex, IndexedLot


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CoverageSnapshotTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('coverage-admin')
        self.client.force_login(self.user)

    def test_cold_start_and_http_poll_never_compute_counts(self):
        with patch('tender_search.core.index_service.index_coverage', side_effect=AssertionError('No HTTP scans')):
            with self.assertNumQueries(1):
                self.assertTrue(display_snapshot()['coverage_pending'])
            for path in [reverse('index_status'), reverse('search')]:
                self.assertContains(self.client.get(path), 'Показатели базы рассчитываются в фоне.')
        self.assertFalse(ProcurementCoverageSnapshot.objects.exists())

    def test_background_refresh_and_one_query_reads_preserve_consistent_counts(self):
        now = timezone.now()
        ProcurementIndex.objects.create(completed_at=now, heartbeat_at=now)
        IndexedLot.objects.create(source_id=1, row={}, deadline=now+timedelta(days=1),status_id=9)
        refresh_snapshot()
        IndexedLot.objects.create(source_id=2, row={}, deadline=now+timedelta(days=1),status_id=9)
        with patch('tender_search.core.index_service.index_coverage', side_effect=AssertionError('No HTTP scans')):
            with self.assertNumQueries(1):
                self.assertEqual(display_snapshot()['index_coverage']['loaded'],1)
            page=self.client.get(reverse('index_status'))
            self.assertContains(page,'Загружено 1 лотов')
            self.assertContains(page,'Показатели на')
        refresh_snapshot()
        self.assertEqual(display_snapshot()['index_coverage']['loaded'],2)

    def test_stale_snapshot_never_displays_green_health_and_failure_keeps_last_result(self):
        now=timezone.now()
        ProcurementIndex.objects.create(completed_at=now,heartbeat_at=now)
        refresh_snapshot()
        previous=ProcurementCoverageSnapshot.objects.get(pk=1).payload
        with patch('tender_search.core.medical_index.coverage',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):refresh_snapshot()
        self.assertEqual(ProcurementCoverageSnapshot.objects.get(pk=1).payload,previous)
        ProcurementCoverageSnapshot.objects.filter(pk=1).update(measured_at=now-timedelta(minutes=4))
        data=display_snapshot()
        self.assertTrue(data['coverage_stale'])
        self.assertFalse(any(item['state'] in ['ready','loading'] for item in data['platform_statuses']))
        self.assertContains(self.client.get(reverse('index_status')),'обновление показателей задерживается')

    def test_non_admin_cannot_read_snapshot(self):
        user=get_user_model().objects.create_user('coverage-user')
        self.client.force_login(user)
        with patch('tender_search.core.coverage_snapshot.display_snapshot',side_effect=AssertionError('Admin only')):
            self.assertEqual(self.client.get(reverse('index_status')).status_code,403)
            self.assertNotContains(self.client.get(reverse('search')),'База активных закупок')
