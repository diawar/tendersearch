from datetime import timedelta
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import MedicalIndex, ProcurementIndex
from .platform_status import health, platform_statuses


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                           'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PlatformStatusTests(TestCase):
    def test_loading_success_and_stale_are_distinct(self):
        now = timezone.now()
        args = dict(completed_at=now, heartbeat_at=now, building=False, now=now)
        self.assertEqual(health(**args)['state'], 'ready')
        self.assertEqual(health(**dict(args, building=True))['state'], 'loading')
        self.assertEqual(health(**dict(args, error=True, building=True))['state'], 'error')
        self.assertEqual(health(**dict(args, paused=True))['state'], 'paused')
        self.assertEqual(health(**dict(args, building=True, heartbeat_at=now-timedelta(minutes=6)))['state'], 'stale')
        self.assertEqual(health(**dict(args, heartbeat_at=now-timedelta(minutes=21)))['state'], 'stale')
        self.assertEqual(health(**dict(args, completed_at=None, heartbeat_at=None))['state'], 'waiting')

    def test_medical_health_is_independent_and_platforms_are_unique(self):
        now = timezone.now()
        MedicalIndex.objects.create(source='med_ecc', completed_at=now, heartbeat_at=now, cursor={'page': 1})
        MedicalIndex.objects.create(source='fms_ecc', completed_at=now, heartbeat_at=now)
        values = dict(completed_at=now, heartbeat_at=now, ready=True, building=False)
        rows = platform_statuses(values)
        self.assertEqual(len(rows), 7)
        self.assertEqual(len({r['key'] for r in rows}), 7)
        states = {r['key']: r['state'] for r in rows}
        self.assertEqual(states['med_ecc'], 'loading')
        self.assertEqual(states['fms_ecc'], 'ready')
        self.assertEqual(states['egz'], 'ready')
        MedicalIndex.objects.filter(source='fms_ecc').update(error='PRIVATE ERROR')
        rows = platform_statuses(values)
        self.assertEqual(next(r for r in rows if r['key']=='fms_ecc')['state'], 'error')
        self.assertNotIn('PRIVATE ERROR', str(rows))

    def test_status_block_and_endpoint_are_admin_only(self):
        now = timezone.now()
        user = get_user_model().objects.create_user('platform-viewer')
        ProcurementIndex.objects.create(completed_at=now, heartbeat_at=now)
        MedicalIndex.objects.create(source='med_ecc', completed_at=now, heartbeat_at=now, cursor={'page': 1})
        MedicalIndex.objects.create(source='fms_ecc', completed_at=now, heartbeat_at=now, error='PRIVATE ERROR')
        self.client.force_login(user)
        response = self.client.get(reverse('index_status'))
        self.assertEqual(response.status_code, 403)
        with patch('tender_search.core.index_service.index_coverage', side_effect=AssertionError('Admin only')):
            page = self.client.get(reverse('search'))
        self.assertNotContains(page, 'База активных закупок')
        self.assertNotContains(page, 'index-coverage')
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.get(reverse('index_status')).status_code, 403)
        user.is_superuser = True
        user.save(update_fields=['is_superuser'])
        response = self.client.get(reverse('index_status'))
        self.assertContains(response, 'База активных закупок')
        self.assertContains(response, 'PRIVATE ERROR')
        self.assertContains(response, 'Состояние загрузки')
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(len(soup.select('.platform-status')), 7)
        self.assertEqual(len(soup.select('[data-platform="fms_ecc"]')), 1)
        self.assertTrue(soup.select_one('[data-platform="med_ecc"] .platform-spinner'))
        self.assertTrue(soup.select_one('[data-platform="egz"] .platform-check'))
        self.assertFalse(soup.select_one('[data-platform="fms_ecc"] .platform-check'))

    def test_elapsed_counter_uses_matching_cycle_timestamps(self):
        from .index_service import index_coverage
        now = timezone.now()
        state = ProcurementIndex.objects.create(started_at=now-timedelta(seconds=125),
            completed_at=now-timedelta(hours=1), pending=[{'resource': 'lots'}])
        with patch('tender_search.core.index_service.timezone.now', return_value=now):
            self.assertEqual(index_coverage()['elapsed_label'], '00:02:05')
        state.pending = []
        state.save()
        self.assertEqual(index_coverage()['elapsed_label'], '')
        state.completed_at = now
        state.save()
        self.assertEqual(index_coverage()['elapsed_label'], '00:02:05')
        user = get_user_model().objects.create_superuser('admin')
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse('index_status')), 'Последний обход занял:')
        self.assertContains(self.client.get(reverse('index_status')), '00:02:05')
