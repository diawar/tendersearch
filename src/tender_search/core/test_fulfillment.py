from unittest.mock import patch
from django.test import TestCase, override_settings
from .models import (CollectionLot, UserLotWorkflow, UserLotPrice, Favorite, AnnouncementWatch,
    LotOutcomeWatch, OutcomeDelivery, SearchProfile, UserLotNote)
from .fulfillment import advance
from .workflow_state import update_workflow, inherit_workflows
from .outcome_service import schedule_outcomes, check_outcome, move_closed_unsubmitted


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class FulfillmentTests(TestCase):
    def setUp(self):
        from .test_lot_pricing import LotPricingTests
        LotPricingTests.setUp(self)
        self.client.post(self.endpoint,{'unit_gross':'116'})
        update_workflow(self.owner.pk,self.lot.pk,stage='won',outcome='won',result_tracking=True)
        self.action_url=f'/workspace/lots/{self.lot.pk}/action/'

    def state(self):
        return UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot)

    def test_winner_to_paid_archive_preserves_price_and_note_and_stops_checks(self):
        UserLotNote.objects.create(owner=self.owner,lot=self.lot,text='Договор и поставка')
        for action,expected in [('contract','contract'),('delivery','delivery'),('payment','payment'),('paid','archived')]:
            response=self.client.post(self.action_url,{'action':action,'next':'/workspace/?stage=won'})
            self.assertEqual(response.status_code,302)
            self.assertEqual(self.state().stage,expected)
            self.assertTrue(UserLotPrice.objects.get(owner=self.owner,lot=self.lot).locked_at)
        self.assertTrue(self.state().completed_at)
        self.assertTrue(self.state().archived_at)
        self.assertFalse(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertFalse(AnnouncementWatch.objects.get(owner=self.owner,announcement=self.lot.announcement).enabled)
        self.assertFalse(LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot).enabled)
        self.assertEqual(UserLotNote.objects.get(owner=self.owner,lot=self.lot).text,'Договор и поставка')
        self.assertEqual(CollectionLot.objects.get(profile__owner=self.other,lot=self.lot).stage,'interesting')

    def test_cannot_skip_steps_or_archive_unfinished_fulfillment(self):
        for action in ('delivery','payment','paid'):
            with self.assertRaises(ValueError):advance(self.owner.pk,self.lot.pk,action)
        advance(self.owner.pk,self.lot.pk,'contract')
        for action in ('contract','archive','restore','paid'):
            with self.assertRaises(ValueError):advance(self.owner.pk,self.lot.pk,action)
        self.assertEqual(self.state().stage,'contract')

    def test_archive_invalidates_running_outcome_and_pending_delivery(self):
        from .models import TelegramDelivery
        watch=LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot)
        watch.lease='old-task';watch.save()
        delivery=OutcomeDelivery.objects.create(watch=watch,event_key='queued',generation=watch.generation,revision=watch.revision)
        pending=TelegramDelivery.objects.create(owner=self.owner,profile=CollectionLot.objects.get(
            profile__owner=self.owner,lot=self.lot).profile,lot=self.lot,announcement_key='pending')
        advance(self.owner.pk,self.lot.pk,'archive')
        delivery.refresh_from_db();self.assertEqual(delivery.state,'cancelled')
        pending.refresh_from_db();self.assertEqual(pending.state,'cancelled')
        with patch('tender_search.core.outcome_service.source_client') as source:
            check_outcome(watch.pk,'old-task');source.assert_not_called()
        with patch('tender_search.core.tasks.check_lot_outcome.delay') as task:
            schedule_outcomes();task.assert_not_called()

    def test_archive_survives_refresh_and_restore_is_shared(self):
        second=SearchProfile.objects.create(owner=self.owner,name='Другая')
        advance(self.owner.pk,self.lot.pk,'archive')
        CollectionLot.objects.bulk_create(inherit_workflows(self.owner.pk,[CollectionLot(profile=second,lot=self.lot)]))
        move_closed_unsubmitted()
        self.assertEqual(CollectionLot.objects.get(profile=second).stage,'archived')
        advance(self.owner.pk,self.lot.pk,'restore')
        self.assertEqual(set(CollectionLot.objects.filter(profile__owner=self.owner,lot=self.lot).values_list('stage',flat=True)),{'won'})
        self.assertTrue(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertTrue(LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot).enabled)

    def test_archive_ui_and_count_and_owner_isolation(self):
        advance(self.owner.pk,self.lot.pk,'archive')
        response=self.client.get('/workspace/',{'stage':'archived'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertContains(response,'Вернуть из архива')
        data=self.client.get('/lots/ui-state/',{'ids':self.lot.pk}).json()
        self.assertEqual(data['collection_count'],0)
        self.assertIn(str(self.lot.pk),data['locked_lots'])
        from django.contrib.auth import get_user_model
        stranger=get_user_model().objects.create_user('no-access')
        self.client.force_login(stranger)
        self.assertEqual(self.client.post(self.action_url,{'action':'restore'}).status_code,404)
        self.assertEqual(self.client.get(self.action_url).status_code,405)

    def test_archive_keeps_documents_when_last_favorite_removed(self):
        from .document_service import cleanup_documents
        from .models import Document, DocumentVersion
        # Other user's interest must not hide a failure to preserve archive files.
        CollectionLot.objects.filter(profile__owner=self.other).delete()
        self.client.post(self.action_url,{'action':'archive'})
        doc=Document.objects.create(announcement=self.lot.announcement,lot=self.lot,
            source_id='spec',name='Specification',url='https://example.com/spec.pdf')
        version=DocumentVersion.objects.create(document=doc,sha256='a'*64,text='Saved requirements')
        cleanup_documents(self.lot.announcement_id)
        self.assertTrue(Document.objects.filter(pk=doc.pk).exists())
        self.assertTrue(DocumentVersion.objects.filter(pk=version.pk,text='Saved requirements').exists())

    def test_archived_documents_are_readable_only_by_authorized_owner_without_refresh(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from .models import Document, DocumentVersion
        from django.contrib.auth import get_user_model
        advance(self.owner.pk,self.lot.pk,'archive')
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            path=Path(directory)/'saved.txt';path.write_text('Saved specification')
            doc=Document.objects.create(announcement=self.lot.announcement,lot=self.lot,
                source_id='archive-file',name='saved.txt',url='https://example.com/saved.txt')
            version=DocumentVersion.objects.create(document=doc,sha256='b'*64,storage_path=str(path))
            self.lot.refresh_state='';self.lot.save()
            with patch('tender_search.core.views.queue_refresh') as refresh:
                page=self.client.get(f'/lots/{self.lot.pk}/')
                refresh.assert_not_called()
            self.assertContains(page,'saved.txt')
            url=f'/lots/{self.lot.pk}/documents/{version.pk}/'
            response=self.client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertEqual(b''.join(response.streaming_content),b'Saved specification')
            stranger=get_user_model().objects.create_user('archive-stranger')
            self.client.force_login(stranger)
            self.assertEqual(self.client.get(url).status_code,404)
