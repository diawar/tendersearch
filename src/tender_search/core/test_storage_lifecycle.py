"""Regression tests: concurrent shared-file lifecycle and durable private uploads."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch
import os
from django.db import connections, transaction
from django.test import TransactionTestCase, TestCase, override_settings
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from . import models as m
from . import test_archive_retention as fixtures
from .workflow_state import update_workflow
from .fulfillment import advance
from .document_service import cleanup_documents
from .archive_retention import enqueue_file,drain_file_deletions,collect_orphan_acts,purge_archived_lots
from .storage_lifecycle import publish_download

STORAGE={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}}

@override_settings(STORAGES=STORAGE)
class ConcurrentRetentionTests(TransactionTestCase):
    archive=fixtures.ArchiveRetentionTests.archive
    def setUp(self):
        mock=patch('tender_search.core.tasks.cleanup_favorite_documents.delay');mock.start();self.addCleanup(mock.stop)
        fixtures.ArchiveRetentionTests.setUp(self)
        self.errors=[]

    def run_thread(self,fn):
        def run():
            connections.close_all()
            try:fn()
            except Exception as exc:self.errors.append(repr(exc))
            finally:connections.close_all()
        thread=Thread(target=run,daemon=True);thread.start();return thread

    def join(self,*threads):
        for thread in threads:
            thread.join(10);self.assertFalse(thread.is_alive(),'Storage lock did not finish')
        self.assertEqual(self.errors,[])

    def test_other_owner_restore_serializes_with_shared_cleanup(self):
        self.archive(self.other,31)
        update_workflow(self.other.pk,self.lot.pk,archived_from_stage='won')
        m.AnnouncementWatch.objects.filter(owner=self.other).delete()
        doc,version,path=fixtures.ArchiveRetentionTests.shared_document(self)
        restored=Event();release=Event();attempted=Event()
        def restore():
            with transaction.atomic():
                advance(self.other.pk,self.lot.pk,'restore');restored.set()
                if not release.wait(10):raise RuntimeError('restore timeout')
        from .storage_lifecycle import lock_retention
        def locking(*args,**kwargs):
            attempted.set();return lock_retention(*args,**kwargs)
        first=self.run_thread(restore)
        try:
            self.assertTrue(restored.wait(5))
            with patch('tender_search.core.storage_lifecycle.lock_retention',side_effect=locking):
                second=self.run_thread(lambda:cleanup_documents(self.lot.announcement_id))
                self.assertTrue(attempted.wait(5))
                self.assertTrue(path.exists())
                release.set();self.join(first,second)
        finally:release.set();first.join(10)
        version.refresh_from_db()
        self.assertTrue(path.exists());self.assertTrue(version.storage_path);self.assertTrue(version.text)
        self.assertEqual(m.UserLotWorkflow.objects.get(owner=self.other,lot=self.lot).stage,'won')

    def test_republished_hash_from_other_announcement_survives_pending_delete(self):
        path=Path(self.tmp.name)/('a'*64+'.pdf');path.write_bytes(b'old')
        enqueue_file('document',str(path))
        announcement=m.Announcement.objects.create(source_id=999001,name='Another active announcement')
        doc=m.Document.objects.create(announcement=announcement,source_id='new',kind='technical')
        published=Event();release=Event();deleting=Event()
        class API:
            def download(self,file,directory):
                temporary=directory/path.name;temporary.write_bytes(b'new shared bytes')
                return {'path':str(temporary),'sha256':'a'*64}
        def publish():
            with publish_download(API(),None) as result:
                m.DocumentVersion.objects.create(document=doc,sha256=result['sha256'],storage_path=result['path'])
                published.set()
                if not release.wait(10):raise RuntimeError('publish timeout')
        from .storage_lifecycle import lock_path
        def locking(name):
            deleting.set();return lock_path(name)
        first=self.run_thread(publish)
        try:
            self.assertTrue(published.wait(5))
            with patch('tender_search.core.storage_lifecycle.lock_path',side_effect=locking):
                second=self.run_thread(drain_file_deletions)
                self.assertTrue(deleting.wait(5));release.set();self.join(first,second)
        finally:release.set();first.join(10)
        self.assertEqual(path.read_bytes(),b'new shared bytes')
        self.assertTrue(m.DocumentVersion.objects.filter(storage_path=str(path)).exists())
        self.assertFalse(m.StoredFileDeletion.objects.exists())

    def test_download_in_progress_is_staged_until_delete_finishes(self):
        path=Path(self.tmp.name)/('b'*64+'.pdf');path.write_bytes(b'old')
        enqueue_file('document',str(path))
        staged=Event();release=Event()
        doc=m.Document.objects.create(announcement=self.lot.announcement,source_id='staged',kind='technical')
        class API:
            def download(self,file,directory):
                temporary=directory/path.name;temporary.write_bytes(b'fresh')
                staged.set()
                if not release.wait(10):raise RuntimeError('download timeout')
                return {'path':str(temporary),'sha256':'b'*64}
        def publish():
            with publish_download(API(),None) as result:
                m.DocumentVersion.objects.create(document=doc,sha256=result['sha256'],storage_path=result['path'])
        thread=self.run_thread(publish)
        try:
            self.assertTrue(staged.wait(5));drain_file_deletions()
            self.assertFalse(path.exists());release.set();self.join(thread)
        finally:release.set();thread.join(10)
        self.assertEqual(path.read_bytes(),b'fresh')
        self.assertTrue(m.DocumentVersion.objects.filter(storage_path=str(path)).exists())

@override_settings(STORAGES=STORAGE)
class PrivateActCleanupTests(TestCase):
    def setUp(self):
        from .test_lot_pricing import LotPricingTests
        LotPricingTests.setUp(self)
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        setting=override_settings(MEDIA_ROOT=self.tmp.name);setting.enable();self.addCleanup(setting.disable)
        update_workflow(self.owner.pk,self.lot.pk,stage='delivery',outcome='won')
        self.shipment=m.LotShipment.objects.create(owner=self.owner,lot=self.lot,mode='own',shipped_at=timezone.now())
        self.shipment.act.save('old.pdf',SimpleUploadedFile('old.pdf',b'%PDF-old'),save=True)
        self.old=Path(self.shipment.act.path)

    def upload(self):
        return self.client.post(f'/lots/{self.lot.pk}/shipment/act/',{'act':SimpleUploadedFile('new.pdf',b'%PDF-new')})

    def test_failed_delete_of_replaced_act_is_retried_without_touching_new_act(self):
        storage=m.LotShipment._meta.get_field('act').storage
        with patch.object(storage,'delete',side_effect=OSError('offline')):
            with self.captureOnCommitCallbacks(execute=True):self.assertEqual(self.upload().status_code,302)
        self.shipment.refresh_from_db();new=Path(self.shipment.act.path)
        self.assertTrue(self.old.exists());self.assertTrue(new.exists())
        self.assertTrue(m.StoredFileDeletion.objects.filter(name=str(self.old.relative_to(self.tmp.name)).replace('\\','/'),attempts=1).exists())
        drain_file_deletions()
        self.assertFalse(self.old.exists());self.assertTrue(new.exists())

    def test_rollback_new_upload_is_removed_and_original_act_preserved(self):
        with patch('tender_search.core.fulfillment.accept_uploaded_act',side_effect=ValueError('rollback')):
            with self.assertRaises(ValueError):self.upload()
        self.shipment.refresh_from_db()
        self.assertEqual(Path(self.shipment.act.path),self.old)
        self.assertEqual(list(Path(self.tmp.name).rglob('*.pdf')),[self.old])

    def test_legacy_orphan_act_is_reclaimed_but_other_user_act_survives(self):
        other=m.LotShipment.objects.create(owner=self.other,lot=self.lot,mode='own')
        other.act.save('other.pdf',SimpleUploadedFile('other.pdf',b'%PDF-other'),save=True)
        orphan=self.old.parent/'legacy.pdf';orphan.write_bytes(b'%PDF-orphan')
        old=(timezone.now()-timedelta(days=40)).timestamp()
        for path in (orphan,self.old,Path(other.act.path)):os.utime(path,(old,old))
        collect_orphan_acts();drain_file_deletions()
        self.assertFalse(orphan.exists());self.assertTrue(self.old.exists());self.assertTrue(Path(other.act.path).exists())
        update_workflow(self.owner.pk,self.lot.pk,stage='archived',archived_at=timezone.now()-timedelta(days=31))
        with self.captureOnCommitCallbacks(execute=True):purge_archived_lots()
        self.assertFalse(self.old.exists());self.assertTrue(Path(other.act.path).exists())
