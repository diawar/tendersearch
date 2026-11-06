from django.test import TestCase, override_settings
from .models import ApplicationPreparation, CompanySettings, UserLotPrice, UserLotWorkflow
from .workflow_state import update_workflow


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PreparationTests(TestCase):
    def setUp(self):
        from .test_lot_pricing import LotPricingTests
        LotPricingTests.setUp(self)
        CompanySettings.objects.update_or_create(owner=self.owner,defaults={'company_bin':'241040001415'})
        self.url=f'/lots/{self.lot.pk}/preparation/'
        self.data={'product_name':'Товар','manufacturer':'Завод','country':'Казахстан',
            'legal_address':'Адрес организации','bank_account':'KZ000000000000000000','contact_phone':'+77000000000',
            'security':'wallet','notes':'Личная подготовка'}

    def test_save_is_private_and_does_not_submit_or_lock_price(self):
        self.assertEqual(self.client.post(self.url,self.data).status_code,302)
        p=ApplicationPreparation.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(p.company_bin,'241040001415')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'interesting')
        self.assertFalse(UserLotPrice.objects.filter(owner=self.owner,lot=self.lot,locked_at__isnull=False).exists())
        self.client.force_login(self.other)
        response=self.client.get(self.url)
        self.assertNotContains(response,'Личная подготовка')
        self.assertEqual(response['Cache-Control'],'no-store, private')
        self.assertEqual(self.client.post(self.url,self.data).status_code,200) # No own BIN.
        self.assertFalse(ApplicationPreparation.objects.filter(owner=self.other).exists())

    def test_missing_bin_invalid_iban_and_missing_product_are_rejected(self):
        for changes in [{'bank_account':'123'}, {'product_name':''}]:
            self.assertEqual(self.client.post(self.url,dict(self.data,**changes)).status_code,200)
            self.assertFalse(ApplicationPreparation.objects.exists())
        CompanySettings.objects.filter(owner=self.owner).update(company_bin='')
        self.assertContains(self.client.post(self.url,self.data),'Сначала укажите БИН')
        self.assertFalse(ApplicationPreparation.objects.exists())

    def test_fulfillment_is_read_only_and_changed_bin_is_flagged(self):
        self.client.post(self.url,self.data)
        CompanySettings.objects.filter(owner=self.owner).update(company_bin='123456789012')
        self.assertContains(self.client.get(self.url),'БИН изменён')
        update_workflow(self.owner.pk,self.lot.pk,stage='applied')
        self.assertEqual(self.client.post(self.url,self.data).status_code,403)
        self.assertContains(self.client.get(self.url),'Просмотр сохранённых данных')

    def test_unrelated_user_and_new_stage_cannot_prepare(self):
        from django.contrib.auth import get_user_model
        stranger=get_user_model().objects.create_user('stranger')
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.client.force_login(self.owner)
        update_workflow(self.owner.pk,self.lot.pk,stage='new')
        self.assertEqual(self.client.post(self.url,self.data).status_code,403)

    def test_export_checks_owner_company_stage_and_source(self):
        from unittest.mock import patch
        self.client.post(self.url,self.data)
        url=self.url+'export/'
        with patch('tender_search.core.outcome_service.supported_source',return_value=True):
            r=self.client.get(url)
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['company_bin'],'241040001415')
            self.assertNotIn('notes',r.json()['data'])
            self.assertEqual(r['Cache-Control'],'no-store, private')
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(url).status_code,404)
            self.client.force_login(self.owner)
            CompanySettings.objects.filter(owner=self.owner).update(company_bin='123456789012')
            self.assertEqual(self.client.get(url).status_code,403)
            update_workflow(self.owner.pk,self.lot.pk,stage='applied')
            self.assertEqual(self.client.get(url).status_code,404)

    def test_status_report_preserves_last_confirmation_and_rejects_stale_or_wrong_owner(self):
        import json
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.utils import timezone
        from datetime import timedelta
        self.client.post(self.url,self.data)
        report={'schema':'goszakup-status-v1','company_bin':'241040001415',
            'announcement_number':self.lot.announcement.number,'lot_number':self.lot.number,
            'observed_at':timezone.now().isoformat(),'state':'draft','number':'123','url':'https://zakup.gov.kz/cabinet/purchase/my-applications/123'}
        def upload(data):
            file=SimpleUploadedFile('status.json',json.dumps(data).encode(),content_type='application/json')
            return self.client.post(self.url+'status/',{'report':file})
        upload(report)
        item=ApplicationPreparation.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(item.portal_state,'draft');confirmed=item.portal_confirmed_at
        report.update(state='unknown',observed_at=(timezone.now()+timedelta(seconds=1)).isoformat())
        upload(report);item.refresh_from_db()
        self.assertEqual(item.portal_state,'draft');self.assertEqual(item.portal_confirmed_at,confirmed)
        self.assertTrue(item.portal_sync_error)
        report.update(state='submitted',company_bin='123456789012',observed_at=(timezone.now()+timedelta(seconds=2)).isoformat())
        upload(report);item.refresh_from_db();self.assertEqual(item.portal_state,'draft')
        report.update(company_bin='241040001415',url='https://evil.example/cabinet/application/123')
        upload(report);item.refresh_from_db();self.assertEqual(item.portal_state,'draft')
        report.update(url='https://zakup.gov.kz/cabinet/purchase/my-applications/123')
        upload(report);item.refresh_from_db();self.assertEqual(item.portal_state,'submitted')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'interesting')
        self.client.force_login(self.other)
        upload(report)
        self.assertFalse(ApplicationPreparation.objects.filter(owner=self.other).exists())
