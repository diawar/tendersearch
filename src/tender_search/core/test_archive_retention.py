from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase,override_settings
from django.utils import timezone
from . import models as m
from .archive_retention import purge_archived_lots,drain_file_deletions,enqueue_file
from .workflow_state import update_workflow,add_to_preparation
from .document_service import cleanup_documents,documents_needed

@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ArchiveRetentionTests(TestCase):
    def setUp(self):
        from .test_lot_pricing import LotPricingTests
        LotPricingTests.setUp(self)
        self.now=timezone.now()
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        setting=override_settings(MEDIA_ROOT=self.tmp.name);setting.enable();self.addCleanup(setting.disable)
        self.lot.announcement.deadline=self.now-timedelta(days=60);self.lot.announcement.save()
        self.archive(self.owner,30)
        cache.clear()

    def archive(self,owner,days):
        update_workflow(owner.pk,self.lot.pk,stage='archived',archived_at=self.now-timedelta(days=days))

    def private_data(self,owner):
        m.UserLotNote.objects.create(owner=owner,lot=self.lot,text='Private note')
        m.UserLotPrice.objects.create(owner=owner,lot=self.lot,unit_gross=116,vat_rate=16)
        m.ApplicationPreparation.objects.create(owner=owner,lot=self.lot,company_bin='241040001415',data={'secret':'private'})
        m.LotDealEstimate.objects.create(owner=owner,lot=self.lot,inputs={'purchase':58},snapshot={'margin':50})
        m.LotObligationCosts.objects.create(owner=owner,lot=self.lot,entries=[{'label':'Lift','amount':'10'}])
        m.DraftQueueItem.objects.create(owner=owner,lot=self.lot,state='complete')
        m.ProductAnalysis.objects.create(owner=owner,lot=self.lot,generation='old',result={'private':'product'})
        m.LotSpecification.objects.create(owner=owner,lot=self.lot,documents=[{'text':'spec'}])
        shipment=m.LotShipment(owner=owner,lot=self.lot,mode='own')
        shipment.act.save('test.pdf',ContentFile(b'private act'),save=True)
        return Path(shipment.act.path)

    def shared_document(self):
        doc=m.Document.objects.create(announcement=self.lot.announcement,lot=self.lot,source_id='shared',kind='technical',status='done')
        path=Path(self.tmp.name)/'shared.pdf';path.write_bytes(b'shared document')
        version=m.DocumentVersion.objects.create(document=doc,sha256='a'*64,storage_path=str(path),text='search text')
        return doc,version,path

    def test_all_personal_data_and_act_removed_only_for_due_owner(self):
        own=self.private_data(self.owner);other=self.private_data(self.other)
        with self.captureOnCommitCallbacks(execute=True):self.assertEqual(purge_archived_lots(now=self.now),1)
        for model in (m.UserLotWorkflow,m.CollectionLot,m.UserLotNote,m.UserLotPrice,m.ApplicationPreparation,
                      m.LotDealEstimate,m.LotObligationCosts,m.LotShipment,m.DraftQueueItem,m.ProductAnalysis,m.LotSpecification):
            field='profile__owner' if model is m.CollectionLot else 'owner'
            self.assertFalse(model.objects.filter(**{field:self.owner},lot=self.lot).exists(),model.__name__)
            self.assertTrue(model.objects.filter(**{field:self.other},lot=self.lot).exists(),model.__name__)
        self.assertFalse(own.exists());self.assertTrue(other.exists())
        self.assertTrue(m.PurgedUserLot.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertEqual(purge_archived_lots(now=self.now),0)

    def test_thirty_days_measured_from_archive_not_payment_or_updated_at(self):
        self.archive(self.owner,29)
        m.UserLotWorkflow.objects.filter(owner=self.owner).update(completed_at=self.now-timedelta(days=100))
        self.assertEqual(purge_archived_lots(now=self.now),0)
        self.archive(self.owner,30)
        self.assertEqual(purge_archived_lots(now=self.now),1)

    def test_restore_or_new_archive_resets_retention(self):
        update_workflow(self.owner.pk,self.lot.pk,stage='payment',archived_at=None)
        self.assertEqual(purge_archived_lots(now=self.now),0)
        self.archive(self.owner,0)
        self.assertEqual(purge_archived_lots(now=self.now),0)

    def test_shared_file_kept_for_active_other_owner_then_removed_after_last_archive(self):
        doc,version,path=self.shared_document()
        with self.captureOnCommitCallbacks(execute=True):purge_archived_lots(now=self.now)
        version.refresh_from_db();self.assertTrue(version.storage_path);self.assertTrue(path.exists())
        self.archive(self.other,31)
        with self.captureOnCommitCallbacks(execute=True):purge_archived_lots(now=self.now)
        version.refresh_from_db();self.assertEqual((version.storage_path,version.text,version.search_text),('','',''))
        self.assertFalse(path.exists())

    def test_recent_archive_protects_shared_documents_despite_old_payment(self):
        self.archive(self.other,2)
        m.UserLotWorkflow.objects.filter(owner=self.other).update(completed_at=self.now-timedelta(days=100))
        self.assertTrue(documents_needed([self.lot.pk],self.lot.announcement.deadline,'contract',now=self.now))

    def test_storage_failure_retried_and_external_path_never_deleted(self):
        path=self.private_data(self.owner)
        with self.captureOnCommitCallbacks(execute=False):purge_archived_lots(now=self.now)
        storage=m.LotShipment._meta.get_field('act').storage
        with patch.object(storage,'delete',side_effect=OSError('unavailable')):drain_file_deletions()
        self.assertTrue(path.exists());self.assertTrue(m.StoredFileDeletion.objects.filter(kind='act',attempts=1).exists())
        drain_file_deletions();self.assertFalse(path.exists())
        with TemporaryDirectory() as outside:
            external=Path(outside)/'keep.pdf';external.write_bytes(b'keep')
            enqueue_file('document',str(external));drain_file_deletions()
            self.assertTrue(external.exists())

    def test_explicit_take_into_work_clears_only_suppression_marker(self):
        purge_archived_lots(now=self.now)
        add_to_preparation(self.owner.pk,self.lot.pk)
        self.assertFalse(m.PurgedUserLot.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertEqual(m.UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'preparing')
        self.assertFalse(m.UserLotNote.objects.filter(owner=self.owner,lot=self.lot).exists())

    def test_history_watches_and_direct_private_download_are_removed(self):
        path=self.private_data(self.owner)
        shipment=m.LotShipment.objects.get(owner=self.owner,lot=self.lot)
        workflow=m.UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot)
        m.ContractWatch.objects.create(workflow=workflow,evidence={'private':'contract'})
        outcome=m.LotOutcomeWatch.objects.create(owner=self.owner,lot=self.lot)
        m.OutcomeDelivery.objects.create(watch=outcome,event_key='retention-outcome',payload={'private':'result'})
        m.WorkflowDelivery.objects.create(owner=self.owner,lot=self.lot,company_bin='241040001415',kind='paid',event_key='retention-paid',payload={'private':'payment'})
        with self.captureOnCommitCallbacks(execute=True):purge_archived_lots(now=self.now)
        self.assertFalse(m.ContractWatch.objects.filter(workflow_id=workflow.pk).exists())
        self.assertFalse(m.OutcomeDelivery.objects.filter(watch_id=outcome.pk).exists())
        self.assertFalse(m.WorkflowDelivery.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertEqual(self.client.get(f'/lots/{self.lot.pk}/shipment/act/download/?act={shipment.pk}').status_code,404)
        self.assertEqual(self.client.get(f'/lots/{self.lot.pk}/preparation/').status_code,404)
        response=self.client.post(f'/lots/{self.lot.pk}/note/',{'note':'late browser write'})
        self.assertEqual(response.status_code,302)
        self.assertFalse(m.UserLotNote.objects.filter(owner=self.owner,lot=self.lot).exists())
        from .specification_service import queue_specification
        with patch('tender_search.core.tasks.extract_lot_specification.delay') as enqueue:
            queue_specification(self.owner,self.lot)
            enqueue.assert_not_called()
        self.assertFalse(m.LotSpecification.objects.filter(owner=self.owner,lot=self.lot).exists())

    def test_hourly_monitor_and_backlog_batches(self):
        from .archive_retention import cleanup_archived_lots
        with patch('tender_search.core.archive_retention.purge_archived_lots',return_value=100) as purge, patch('tender_search.core.archive_retention.drain_file_deletions'):
            cleanup_archived_lots();cleanup_archived_lots()
            self.assertEqual(purge.call_count,2)
            purge.return_value=1
            cleanup_archived_lots();cleanup_archived_lots()
            self.assertEqual(purge.call_count,3)


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ArchiveSuppressionTests(TestCase):
    def test_refresh_does_not_recreate_purged_memberships(self):
        from .test_collections import CollectionsTests
        CollectionsTests.setUp(self)
        CollectionsTests.run_refresh(self)
        lot=m.CollectionLot.objects.get(profile=self.profile).lot
        update_workflow(self.owner.pk,lot.pk,stage='archived',archived_at=timezone.now()-timedelta(days=31))
        with self.captureOnCommitCallbacks(execute=True):self.assertEqual(purge_archived_lots(),1)
        CollectionsTests.run_refresh(self)
        self.assertFalse(m.CollectionLot.objects.filter(profile__owner=self.owner,lot=lot).exists())
        self.assertFalse(m.UserLotWorkflow.objects.filter(owner=self.owner,lot=lot).exists())
