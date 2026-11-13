from django.test import TestCase, override_settings
from .models import ApplicationPreparation, CompanySettings, UserLotPrice, UserLotWorkflow
from .workflow_state import update_workflow


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PreparationTests(TestCase):
    def test_country_dictionary_and_legacy_names(self):
        from .preparation import PreparationForm
        from .goszakup_countries import COUNTRIES
        self.assertEqual(len(COUNTRIES), 256)
        self.assertIn(('398', 'КАЗАХСТАН'), COUNTRIES)
        self.assertIn(('156', 'КИТАЙ'), COUNTRIES)
        field = PreparationForm.base_fields['country']
        self.assertEqual(field.clean('Казахстан'), 'КАЗАХСТАН')
        self.assertEqual(field.prepare_value('Китай'), 'КИТАЙ')
        self.assertContains(self.client.get(self.url), '<select name="country"', html=False)
        response = self.client.post(self.url, dict(self.data, country='Несуществующая страна'))
        self.assertContains(response, 'Выберите страну из справочника Госзакупа.')
        self.assertFalse(ApplicationPreparation.objects.exists())

    def setUp(self):
        from .test_lot_pricing import LotPricingTests
        LotPricingTests.setUp(self)
        for user in (self.owner,self.other):update_workflow(user.pk,self.lot.pk,stage='preparing')
        CompanySettings.objects.update_or_create(owner=self.owner,defaults={'company_bin':'241040001415'})
        self.url=f'/lots/{self.lot.pk}/preparation/'
        self.data={'product_name':'Товар','manufacturer':'Завод','country':'Казахстан',
            'legal_address':'Адрес организации','bank_account':'KZ000000000000000000','contact_phone':'+77000000000',
            'security':'wallet','notes':'Личная подготовка'}

    def test_save_is_private_and_does_not_submit_or_lock_price(self):
        self.assertEqual(self.client.post(self.url,self.data).status_code,302)
        p=ApplicationPreparation.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(p.company_bin,'241040001415')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'preparing')
        self.assertFalse(UserLotPrice.objects.filter(owner=self.owner,lot=self.lot,locked_at__isnull=False).exists())
        self.client.force_login(self.other)
        response=self.client.get(self.url)
        self.assertNotContains(response,'Личная подготовка')
        self.assertEqual(response['Cache-Control'],'no-store, private')
        self.assertEqual(self.client.post(self.url,self.data).status_code,200) # No own BIN.
        self.assertFalse(ApplicationPreparation.objects.filter(owner=self.other).exists())

    def test_export_position_count_requires_identity_checked_detail(self):
        from .preparation import draft_position_count
        self.lot.raw={'system_id':1,'purchase_method_id':12,'_pricing_detail':{
            'id':self.lot.source_id,'is_combined':False,'plan_items':[
                {'id':11,'purchase_method':{'id':12}}, {'id':12,'purchase_method':{'id':12}}]}}
        self.lot.announcement.external_id=17920228
        self.lot.announcement.raw={'system':{'id':4}}
        self.lot.announcement.save()
        self.lot.save()
        self.assertEqual(draft_position_count(self.lot),2)
        self.client.post(self.url,self.data)
        data=self.client.get(self.url+'export/').json()
        self.assertEqual(data['position_count'],2)
        self.assertEqual(data['data']['country'],'КАЗАХСТАН')
        self.assertFalse(data['single_position'])
        self.assertNotIn('repeat_product',data) # Consent belongs to the extension user.
        detail=self.lot.raw['_pricing_detail']
        detail['plan_items'][1]['id']=11
        self.assertIsNone(draft_position_count(self.lot))
        detail['plan_items'][1]['id']=12
        detail['plan_items'][1]['purchase_method']['id']=99
        self.assertIsNone(draft_position_count(self.lot))

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
        self.assertContains(self.client.get(self.url),'БИН организации изменён')
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
        with patch('tender_search.core.preparation.supports_preparation_export',return_value=True):
            r=self.client.get(url)
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['company_bin'],'241040001415')
            self.assertNotIn('notes',r.json()['data'])
            self.assertEqual(r['Cache-Control'],'no-store, private')
            self.assertContains(self.client.get(self.url),'id="prepare-with-extension"')
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(url).status_code,404)
            self.client.force_login(self.owner)
            CompanySettings.objects.filter(owner=self.owner).update(company_bin='123456789012')
            self.assertEqual(self.client.get(url).status_code,403)
            update_workflow(self.owner.pk,self.lot.pk,stage='applied')
            self.assertEqual(self.client.get(url).status_code,404)

    def test_export_supports_epz_metadata_without_expanding_outcome_support(self):
        from .outcome_service import supported_source
        self.client.post(self.url,self.data)
        announcement=self.lot.announcement
        announcement.external_id=17926777
        announcement.raw={'system':{'id':4,'name':'EPZ'}}
        announcement.save()
        self.lot.raw={'system_id':1,'system':{'id':1}}
        self.lot.save()
        self.assertFalse(supported_source(self.lot))
        response=self.client.get(self.url+'export/')
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response['Content-Disposition'])
        self.assertEqual(response.json()['announcement_external_id'],'17926777')
        self.assertContains(self.client.get(self.url),'Сохранить в черновик на zakup.gov.kz')
        for system in (2,3,6,7,99):
            announcement.raw={'system':{'id':system}};announcement.save()
            self.assertRedirects(self.client.get(self.url+'export/'),self.url)
            unsupported=self.client.get(self.url)
            self.assertFalse(unsupported.context['preparation_launch_ready'])
            self.assertContains(unsupported,'data-launch-ready="false" disabled')
        announcement.raw={};announcement.save()
        self.lot.raw={};self.lot.save()
        self.assertRedirects(self.client.get(self.url+'export/'),self.url)

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
        report.update(state='completed',observed_at=(timezone.now()+timedelta(seconds=3)).isoformat())
        upload(report);item.refresh_from_db();self.assertEqual(item.portal_state,'completed')
        self.assertContains(self.client.get(self.url),'Заявка завершена на портале')
        report.update(state='submitted',observed_at=(timezone.now()+timedelta(seconds=4)).isoformat())
        upload(report);item.refresh_from_db();self.assertEqual(item.portal_state,'completed')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'applied')
        self.client.force_login(self.other)
        upload(report)
        self.assertFalse(ApplicationPreparation.objects.filter(owner=self.other).exists())
