from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from .models import ProcurementIndex, AnnouncementRefresh
from .index_service import index_coverage
from .medical_index import coverage as medical_coverage
from .test_index import row, put


class SearchReadinessTests(TestCase):
    def test_lightweight_readiness_keeps_live_safety_checks_without_counts(self):
        now = timezone.now()
        ProcurementIndex.objects.create(completed_at=now, heartbeat_at=now)
        lot = put(row())
        with CaptureQueriesContext(connection) as captured:
            light = index_coverage(diagnostics=False)
            medical_coverage(diagnostics=False)
        self.assertTrue(light['ready'])
        self.assertFalse(any('COUNT(' in query['sql'].upper() for query in captured))
        self.assertIsNone(light['loaded'])
        self.assertEqual(index_coverage()['loaded'], 1)
        lot.row['_details_pending'] = True
        lot.save(update_fields=['row'])
        self.assertFalse(index_coverage(diagnostics=False)['ready'])
        lot.row['_details_pending'] = False
        lot.save(update_fields=['row'])
        AnnouncementRefresh.objects.create(source_id=100, deadline=lot.deadline, active=True, urgent=True)
        self.assertFalse(index_coverage(diagnostics=False)['ready'])
        AnnouncementRefresh.objects.update(urgent=False)
        self.assertTrue(index_coverage(diagnostics=False)['ready'])
