import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from tender_search.client import Document as SourceDocument
from .models import Favorite, Document, DocumentVersion, LotDocumentDownload
from .search_service import store_lot
from .document_service import download_documents, cleanup_documents

@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class FavoriteDocumentTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.override=override_settings(MEDIA_ROOT=Path(self.temp.name));self.override.enable();self.addCleanup(self.override.disable)
        self.owner=get_user_model().objects.create_user('doc-owner')
        self.other=get_user_model().objects.create_user('doc-other')
        self.lot=store_lot({'id':600,'announcement_id':1100,'announcement_number':'1100-1','lot_number':'600-1','name_ru':'Laptop','customers':[]})
        Favorite.objects.create(owner=self.owner,lot=self.lot)
        self.client.force_login(self.owner)
    def api(self):
        api=Mock()
        api.documents.return_value=[SourceDocument('common','terms.pdf','https://zakup.gov.kz/terms',1100,None,None),SourceDocument('lot','spec.pdf','https://zakup.gov.kz/spec',1100,600,None),SourceDocument('other','other.pdf','https://zakup.gov.kz/other',1100,601,None)]
        def save(doc,directory):
            digest=hashlib.sha256(doc.id.encode()).hexdigest()
            path=directory/(digest+'.pdf');path.write_bytes(doc.id.encode())
            return {'path':str(path),'sha256':digest,'size':len(doc.id)}
        api.download.side_effect=save
        return api
    def download(self):
        LotDocumentDownload.objects.update_or_create(lot=self.lot,defaults={'state':'queued'})
        api=self.api();download_documents(self.lot.pk,api)
        return api
    def test_only_common_and_selected_lot_cached(self):
        api=self.download()
        self.assertEqual(api.download.call_count,2)
        self.assertEqual(DocumentVersion.objects.count(),2)
        self.assertEqual(LotDocumentDownload.objects.get(lot=self.lot).state,'done')
        api=self.download();api.download.assert_not_called()
    def test_add_favorite_queues_after_commit(self):
        Favorite.objects.all().delete()
        with patch('tender_search.core.tasks.download_favorite_documents.delay') as task:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(f'/lots/{self.lot.pk}/favorite/',{'action':'add'})
            task.assert_called_once_with(self.lot.pk)
    def test_remove_deletes_files_and_keeps_other_users_copy(self):
        self.download()
        Favorite.objects.create(owner=self.other,lot=self.lot)
        Favorite.objects.filter(owner=self.owner).delete()
        cleanup_documents(self.lot.announcement_id)
        self.assertEqual(DocumentVersion.objects.count(),2)
        Favorite.objects.filter(owner=self.other).delete()
        with self.captureOnCommitCallbacks(execute=True):
            cleanup_documents(self.lot.announcement_id)
        self.assertFalse(Document.objects.exists())
        self.assertFalse(list(Path(self.temp.name).iterdir()))
    def test_download_requires_own_favorite(self):
        self.download();version=DocumentVersion.objects.first()
        url=f'/lots/{self.lot.pk}/documents/{version.pk}/'
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        # Consume through Django's test-client wrapper so request_finished does
        # not close the PostgreSQL connection inside TestCase's transaction.
        self.assertTrue(b''.join(response.streaming_content))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code,404)
    def test_one_failed_file_does_not_lose_successful_files(self):
        LotDocumentDownload.objects.create(lot=self.lot)
        api=self.api();save=api.download.side_effect
        def failing(doc,directory):
            if doc.id=='lot':raise RuntimeError('failure')
            return save(doc,directory)
        api.download.side_effect=failing
        download_documents(self.lot.pk,api)
        self.assertEqual(LotDocumentDownload.objects.get(lot=self.lot).state,'partial')
        self.assertEqual(DocumentVersion.objects.count(),1)
