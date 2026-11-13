from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from .test_lot_pricing import LotPricingTests
from .models import (CollectionLot, UserLotWorkflow, DraftQueueItem, ApplicationPreparation,
                     UserLotNote, UserLotPrice, Favorite, AnnouncementWatch, TelegramDelivery)
from .workflow_state import add_to_preparation, add_to_interesting, remove_from_work


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WorkStageTests(TestCase):
    def setUp(self):
        LotPricingTests.setUp(self)

    def test_old_entry_and_star_take_into_work_idempotently(self):
        add_to_interesting(self.owner.pk,self.lot.pk)
        queue=DraftQueueItem.objects.get(owner=self.owner,lot=self.lot)
        DraftQueueItem.objects.filter(pk=queue.pk).update(state='complete',message='Saved')
        add_to_preparation(self.owner.pk,self.lot.pk)
        queue.refresh_from_db()
        self.assertEqual(queue.state,'complete')
        self.assertEqual(queue.message,'Saved')
        self.assertTrue(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
        page=self.client.get('/workspace/drafts/')
        self.assertContains(page,'В работе')
        self.assertNotContains(page,'Интересные лоты')
        self.assertContains(page,'favorite-icon selected')
        self.assertContains(page,'status-watch-icon selected')

    def test_decline_clears_only_own_star_watch_queue_and_pending_notification(self):
        add_to_preparation(self.owner.pk,self.lot.pk)
        add_to_preparation(self.other.pk,self.lot.pk)
        self.assertTrue(remove_from_work(self.owner.pk,self.lot.pk))
        self.assertFalse(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertTrue(Favorite.objects.filter(owner=self.other,lot=self.lot).exists())
        self.assertFalse(AnnouncementWatch.objects.get(owner=self.owner,announcement=self.lot.announcement).enabled)
        self.assertTrue(AnnouncementWatch.objects.get(owner=self.other,announcement=self.lot.announcement).enabled)
        self.assertEqual(DraftQueueItem.objects.get(owner=self.owner,lot=self.lot).state,'cancelled')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'not_participated')

    def test_busy_queue_cannot_be_declined(self):
        add_to_preparation(self.owner.pk,self.lot.pk)
        DraftQueueItem.objects.filter(owner=self.owner,lot=self.lot).update(state='running')
        self.assertFalse(remove_from_work(self.owner.pk,self.lot.pk))
        self.assertTrue(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())

    def test_migration_preserves_private_data_and_existing_queue_generation(self):
        saved=ApplicationPreparation.objects.create(owner=self.owner,lot=self.lot,
            company_bin='241040001415',data={'product_name':'Private'},portal_number='12345')
        UserLotNote.objects.create(owner=self.owner,lot=self.lot,text='Private note')
        UserLotPrice.objects.create(owner=self.owner,lot=self.lot,unit_gross=116,vat_rate=16)
        add_to_preparation(self.owner.pk,self.lot.pk)
        queue=DraftQueueItem.objects.get(owner=self.owner,lot=self.lot)
        generation=queue.generation
        DraftQueueItem.objects.filter(pk=queue.pk).update(state='complete')
        CollectionLot.objects.filter(profile__owner=self.owner,lot=self.lot).update(stage='interesting')
        UserLotWorkflow.objects.filter(owner=self.owner,lot=self.lot).update(stage='interesting',archived_from_stage='interesting')
        module=import_module('tender_search.core.migrations.0052_merge_work_stage')
        module.merge_work(apps,SimpleNamespace(connection=connection))
        module.merge_work(apps,SimpleNamespace(connection=connection))
        saved.refresh_from_db();queue.refresh_from_db()
        self.assertEqual(saved.portal_number,'12345')
        self.assertEqual(saved.data,{'product_name':'Private'})
        self.assertEqual((queue.state,queue.generation),('complete',generation))
        self.assertEqual(UserLotNote.objects.get(owner=self.owner,lot=self.lot).text,'Private note')
        self.assertEqual(UserLotPrice.objects.get(owner=self.owner,lot=self.lot).unit_gross,116)
        state=UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual((state.stage,state.archived_from_stage),('preparing','preparing'))
        self.assertEqual(DraftQueueItem.objects.filter(owner=self.other,lot=self.lot).count(),1)

    def test_decline_available_after_specification_without_product_or_price(self):
        from bs4 import BeautifulSoup
        add_to_preparation(self.owner.pk,self.lot.pk)
        page=self.client.get(f'/lots/{self.lot.pk}/preparation/')
        self.assertEqual(page.status_code,200)
        soup=BeautifulSoup(page.content,'html.parser')
        action=soup.select_one('#prep-step-1 .preparation-decline form')
        self.assertIsNotNone(action)
        self.assertNotIn('disabled',action.select_one('button').attrs)
        self.assertLess(str(page.content).find('preparation-decline'),str(page.content).find('id="product-selection"'))
        response=self.client.post(action['action'],{'stage':'not_participated','return_to':'/workspace/drafts/?profile=all'})
        self.assertEqual(response.url,'/workspace/drafts/?profile=all')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'not_participated')
        self.assertFalse(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
