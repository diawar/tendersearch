from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from bs4 import BeautifulSoup
from .models import Lot
from .search_service import store_lot
from .test_index import row


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
                            'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class LotRefreshConsistencyTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('refresh-consistency')
        self.client.force_login(self.owner)
        self.lot=store_lot(row(1, name_ru='Old item',description_ru='Old description',total_price=100))
        Lot.objects.filter(pk=self.lot.pk).update(refresh_state='done',checked_at=timezone.now()-timedelta(minutes=10))
        self.url=f'/lots/{self.lot.pk}/'
        self.detail={'id':self.lot.source_id,'is_combined':False,'system':{'id':1},
                     'plan_items':[{'count':4,'unit_price':250,'measure':{'name_ru':'Штука'}}]}

    def complete_worker(self, *args):
        Lot.objects.filter(pk=self.lot.pk).update(name='Updated item',description='Updated description',
            amount=1000, raw={'_pricing_detail':self.detail},checked_at=timezone.now(),refresh_state='done')

    def assert_current(self, html):
        soup=BeautifulSoup(html,'html.parser')
        self.assertIn('Updated description',soup.select_one('#lot-description').get_text())
        self.assertIn('Updated item',soup.select_one('#lot-header').get_text())
        self.assertNotIn('Old description',html)
        self.assertIn('1000,00', ''.join(soup.select_one('#lot-header .price').get_text().split()))

    def test_fast_worker_completion_during_open_reloads_body_and_price_projection(self):
        with patch('tender_search.core.views.queue_refresh',side_effect=self.complete_worker):
            response=self.client.get(self.url)
        self.assert_current(response.content.decode())
        lot=response.context['lot']
        self.assertEqual(lot.amount,Decimal('1000'))
        self.assertEqual(lot._budget_display['quantity'],4)
        self.assertEqual(lot._budget_display['unit'],250)
        self.assertEqual(BeautifulSoup(response.content,'html.parser').select_one('#lot-detail')['data-pending'],'false')

    def test_worker_completion_after_open_is_returned_by_status_poll(self):
        def start(lot):
            Lot.objects.filter(pk=lot.pk).update(refresh_state='running',refresh_started_at=timezone.now())
        with patch('tender_search.core.views.queue_refresh',side_effect=start):
            opened=self.client.get(self.url)
        root=BeautifulSoup(opened.content,'html.parser').select_one('#lot-detail')
        self.assertEqual(root['data-pending'],'true')
        self.complete_worker()
        with patch('tender_search.core.views.queue_refresh') as queue:
            response=self.client.get(f'/lots/{self.lot.pk}/status/',
                {name:root[f'data-{name}'] for name in ('detail','documents','specification')})
        queue.assert_not_called()
        data=response.json()
        self.assertFalse(data['pending'])
        self.assert_current(data['html'])
        self.assertNotEqual(data['revisions']['detail'],root['data-detail'])

    def test_expiry_check_does_not_claim_worker_revision_after_losing_race(self):
        from .views import expire_lot_jobs
        Lot.objects.filter(pk=self.lot.pk).update(refresh_state='running',refresh_started_at=timezone.now()-timedelta(minutes=7))
        stale=Lot.objects.get(pk=self.lot.pk)
        checked=stale.checked_at
        self.complete_worker()
        expire_lot_jobs(stale)
        self.assertEqual(stale.checked_at,checked)
        self.assertEqual(stale.refresh_state,'running')
        self.assertEqual(Lot.objects.get(pk=stale.pk).refresh_state,'done')

    def test_timeout_then_late_completion_keeps_body_revision_consistent(self):
        from django.db.models.query import QuerySet
        from .views import expire_lot_jobs
        Lot.objects.filter(pk=self.lot.pk).update(refresh_state='running',refresh_started_at=timezone.now()-timedelta(minutes=7))
        stale=Lot.objects.get(pk=self.lot.pk)
        checked=stale.checked_at
        original_update=QuerySet.update
        def update(queryset, **values):
            count=original_update(queryset,**values)
            if values.get('refresh_state')=='error':
                self.complete_worker()
            return count
        with patch.object(QuerySet,'update',autospec=True,side_effect=update):
            expire_lot_jobs(stale)
        self.assertEqual(stale.checked_at,checked)
        self.assertEqual(stale.refresh_state,'error')
        self.assertEqual(Lot.objects.get(pk=stale.pk).refresh_state,'done')
