import os
from pathlib import Path
from django.test import TestCase, override_settings
from .models import ApplicationPreparation
from .workflow_state import update_workflow


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PreparationStepsTests(TestCase):
    def setUp(self):
        from .test_preparation import PreparationTests
        PreparationTests.setUp(self)

    def test_steps_missing_data_and_no_premature_launch(self):
        response=self.client.get(self.url)
        self.assertContains(response,'aria-label="Шаги подготовки заявки"')
        self.assertEqual(response.content.count(b'data-step="'),6)
        self.assertFalse(response.context['preparation_launch_ready'])
        self.assertIn('Наименование предлагаемого товара',response.context['preparation_product_missing'])
        self.assertContains(response,'Ожидаем итоги')
        self.assertContains(response,'id="preparation-form"',count=1)
        self.assertNotContains(response,'name="file"')
        if os.environ.get('PREPARATION_PREVIEW_PATH'):
            Path(os.environ['PREPARATION_PREVIEW_PATH']).write_bytes(response.content)

    def test_saved_preparation_does_not_claim_submission_or_complete_finances(self):
        self.client.post(self.url,self.data)
        response=self.client.get(self.url)
        self.assertContains(response,'data-saved="true"')
        self.assertContains(response,'data-submitted="false"')
        self.assertContains(response,'Необязательно')
        self.assertEqual(ApplicationPreparation.objects.get(owner=self.owner).portal_number,'')

    def test_waiting_results_is_readonly_and_keeps_bin_check_explanation(self):
        update_workflow(self.owner.pk,self.lot.pk,stage='waiting_results',result_tracking=True)
        response=self.client.get(self.url)
        self.assertContains(response,'Текущий этап: Ожидаем итоги')
        self.assertFalse(response.context['preparation_launch_ready'])
        self.assertNotContains(response,'id="prepare-with-extension"')
        self.assertNotContains(response,'id="save-preparation"')
