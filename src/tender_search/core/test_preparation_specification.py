from unittest.mock import patch
from django.test import TestCase,override_settings,Client
from .models import Document,DocumentVersion,Lot,LotSpecification


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PreparationSpecificationTests(TestCase):
    def setUp(self):
        from .test_preparation import PreparationTests
        PreparationTests.setUp(self)
        self.endpoint=self.url+'specification/'

    def document(self,source,lot,text):
        doc=Document.objects.create(announcement=self.lot.announcement,lot=lot,
            source_id=source,name='Техническая спецификация '+source,kind='technical',url='https://zakup.gov.kz/'+source)
        return DocumentVersion.objects.create(document=doc,sha256=source*64,text=text)

    def test_shared_cache_shows_latest_own_lot_and_announcement_documents_without_download(self):
        own=self.document('a',self.lot,'Старая версия')
        DocumentVersion.objects.create(document=own.document,sha256='d'*64,text='Требование: IPS <script>')
        self.document('b',None,'Общее требование')
        other=Lot.objects.create(announcement=self.lot.announcement,source_id=999,name='Другой лот')
        self.document('c',other,'Только для другого лота')
        with patch('tender_search.core.specification_service.queue_specification') as queue:
            response=self.client.get(self.url)
        queue.assert_not_called()
        self.assertContains(response,'Требование: IPS &lt;script&gt;')
        self.assertContains(response,'Общее требование')
        self.assertNotContains(response,'Старая версия')
        self.assertNotContains(response,'Только для другого лота')
        self.assertNotContains(response,'hx-trigger="load"')
        self.assertContains(response,'Копировать номер объявления')

    def test_missing_specification_loads_asynchronously_without_replacing_price_form(self):
        with patch('tender_search.core.specification_service.queue_specification') as queue:
            page=self.client.get(self.url)
            queue.assert_not_called()
            self.assertContains(page,'hx-trigger="load"')
            LotSpecification.objects.create(owner=self.owner,lot=self.lot,state='queued')
            response=self.client.post(self.endpoint)
            queue.assert_called_once()
        self.assertContains(response,'every 4s, app:resume')
        self.assertNotContains(response,'id="preparation-form"')
        self.assertEqual(response['Cache-Control'],'no-store, private')

    def test_terminal_failure_does_not_retry_on_every_poll_or_page_load(self):
        LotSpecification.objects.create(owner=self.owner,lot=self.lot,state='not_found',error='Файл не найден')
        response=self.client.get(self.endpoint)
        self.assertContains(response,'Файл не найден')
        self.assertNotContains(response,'every 4s')
        self.assertNotContains(self.client.get(self.url),'hx-trigger="load"')

    def test_requires_personal_workflow_and_csrf_for_extraction(self):
        from django.contrib.auth import get_user_model
        stranger=get_user_model().objects.create_user('spec-stranger')
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(self.endpoint).status_code,404)
        self.assertEqual(self.client.post(self.endpoint).status_code,404)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.owner)
        self.assertEqual(strict.post(self.endpoint).status_code,403)

    def test_interactive_extraction_has_its_own_queue_and_progress_states(self):
        from django.conf import settings
        self.assertEqual(settings.CELERY_TASK_ROUTES['tender_search.core.tasks.extract_lot_specification']['queue'],'specifications')
        job=LotSpecification.objects.create(owner=self.owner,lot=self.lot,state='queued')
        self.assertContains(self.client.get(self.endpoint),'В очереди загрузки техспецификации')
        job.state='running';job.save()
        self.assertContains(self.client.get(self.endpoint),'Скачиваем документ и извлекаем текст')

    def test_poll_renders_cached_text_even_while_background_refresh_is_queued(self):
        LotSpecification.objects.create(owner=self.owner,lot=self.lot,state='queued')
        self.document('f',self.lot,'Готовый общий текст')
        with patch('tender_search.core.specification_service.queue_specification') as queue:
            response=self.client.get(self.endpoint)
        self.assertContains(response,'Готовый общий текст')
        queue.assert_not_called()
