from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from pypdf import PdfWriter
from tender_search.client import Document, SourceError
from .models import LotSpecification, Favorite, AnnouncementWatch
from .search_service import store_lot
from .specification_service import extract_text, process_specification, queue_specification
from .test_index import row


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SpecificationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('spec-owner')
        self.other = get_user_model().objects.create_user('other-spec-owner')
        self.lot = store_lot(row(1))
        self.lot.refresh_state = 'done'; self.lot.checked_at = timezone.now(); self.lot.save()
        self.client.force_login(self.owner)

    def test_extract_formats_and_explicit_scan_failure(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'spec'
            path.write_text('Мышь беспроводная\nКоличество: 2', encoding='utf-8')
            self.assertIn('Количество: 2', extract_text(path, 'spec.txt')[0])
            with ZipFile(path, 'w') as archive:
                archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Клавиатура USB</w:t></w:r></w:p></w:body></w:document>')
            self.assertEqual(extract_text(path, 'spec.docx'), ('Клавиатура USB', False))
            writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
            with path.open('wb') as output: writer.write(output)
            with self.assertRaisesMessage(SourceError, 'OCR'): extract_text(path, 'scan.pdf')
            path.write_bytes(b'unsupported')
            with self.assertRaises(SourceError): extract_text(path, 'spec.doc')

    def test_document_scope_private_text_and_no_star_or_plane_side_effects(self):
        files = [Document(str(i), name, f'https://files.example/{i}', self.lot.announcement.source_id, lot_id, 'Техническая спецификация' if i != 4 else 'Договор')
                 for i, name, lot_id in [(1, 'Техспецификация.txt', self.lot.source_id), (2, 'Техспецификация.txt', None),
                                         (3, 'Техспецификация.txt', self.lot.source_id + 1), (4, 'Договор.txt', self.lot.source_id)]]
        job = LotSpecification.objects.create(owner=self.owner, lot=self.lot, state='queued', generation='job')
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory), patch('tender_search.core.specification_service.lot_client') as factory:
            api = factory.return_value.__enter__.return_value
            api.documents.return_value = files
            def download(file, destination, **kwargs):
                path = destination / (file.id + '.txt')
                path.write_text('<script>Характеристики ' + file.id + '</script>', encoding='utf-8')
                return {'path': str(path)}
            api.download.side_effect = download
            process_specification(job.pk, 'stale')
            api.documents.assert_not_called()
            process_specification(job.pk, 'job')
            self.assertEqual(api.download.call_count, 2)
            self.assertFalse(list((Path(directory) / 'specification-work').iterdir()))
        job.refresh_from_db()
        self.assertEqual(job.state, 'done')
        self.assertEqual([doc['scope'] for doc in job.documents], ['lot', 'announcement'])
        response = self.client.get(f'/lots/{self.lot.pk}/')
        self.assertContains(response, '&lt;script&gt;Характеристики 1&lt;/script&gt;')
        self.assertNotContains(response, '<script>Характеристики')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(f'/lots/{self.lot.pk}/'), 'Характеристики 1')
        self.assertFalse(Favorite.objects.exists()); self.assertFalse(AnnouncementWatch.objects.exists())

    def test_enqueue_requires_login_and_does_not_duplicate_pending_job(self):
        endpoint = f'/lots/{self.lot.pk}/specification/'
        with patch('tender_search.core.tasks.extract_lot_specification.delay') as queued:
            self.assertEqual(self.client.get(endpoint).status_code, 405)
            for _ in range(2): self.assertEqual(self.client.post(endpoint).status_code, 302)
            queued.assert_called_once()
            self.assertEqual(LotSpecification.objects.filter(owner=self.owner).count(), 1)
            self.client.logout()
            self.assertEqual(self.client.post(endpoint).status_code, 302)
            queued.assert_called_once()
