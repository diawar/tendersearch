from django.test import TestCase, override_settings
from .models import CompanySettings, ApplicationPreparation
from . import test_preparation


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CompanyRequisitesTests(TestCase):
    def setUp(self):
        test_preparation.PreparationTests.setUp(self)
        self.requisites={'action':'company','company_bin':'241040001415','company_name':'ТОО Пример',
            'legal_address':'Адрес для проверки','bank_account':'kz000000000000000000','bank_name':'Тестовый банк',
            'bank_bic':'hsbkkzkx','contact_phone':'+77000000000'}

    def test_settings_are_personal_and_prefill_only_unsaved_preparation(self):
        self.assertEqual(self.client.post('/settings/',self.requisites).status_code,302)
        company=CompanySettings.objects.get(owner=self.owner)
        self.assertEqual(company.bank_account,'KZ000000000000000000')
        self.assertEqual(company.bank_bic,'HSBKKZKX')
        response=self.client.get(self.url)
        self.assertEqual(response.context['form'].initial['contact_phone'],'+77000000000')
        self.assertEqual(response.context['form'].initial['legal_address'],'Адрес для проверки')
        self.client.post(self.url,self.data)
        self.assertEqual(ApplicationPreparation.objects.get(owner=self.owner).data['bank_bic'],'HSBKKZKX')
        self.client.post('/settings/',dict(self.requisites,contact_phone='+77111111111'))
        self.assertEqual(self.client.get(self.url).context['form'].initial['contact_phone'],self.data['contact_phone'])
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get('/settings/'),'Адрес для проверки')
        self.assertFalse(CompanySettings.objects.filter(owner=self.other).exists())

    def test_invalid_bank_codes_and_phone_do_not_write(self):
        for key,value in [('bank_account','bad'),('bank_bic','bad'),('contact_phone','abc')]:
            response=self.client.post('/settings/',dict(self.requisites,**{key:value}))
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.context['company_form'].errors)
            self.assertEqual(CompanySettings.objects.get(owner=self.owner).legal_address,'')

    def test_bin_only_update_preserves_saved_requisites(self):
        self.client.post('/settings/',self.requisites)
        self.client.post('/settings/',{'action':'company','company_bin':'241040001415'})
        self.assertEqual(CompanySettings.objects.get(owner=self.owner).bank_bic,'HSBKKZKX')

    def test_taxpayer_choice_is_private_validated_and_exported(self):
        self.lot.announcement.external_id=17920228;self.lot.announcement.save()
        from django import forms
        from .forms import CompanySettingsForm
        self.assertIsInstance(CompanySettingsForm.base_fields['taxpayer_type'],forms.ChoiceField)
        data=dict(self.requisites,taxpayer_type='Юридическое лицо')
        self.assertEqual(self.client.post('/settings/',data).status_code,302)
        self.assertEqual(CompanySettings.objects.get(owner=self.owner).taxpayer_type,'Юридическое лицо')
        self.assertEqual(self.client.get(self.url).context['form'].initial['taxpayer_type'],'Юридическое лицо')
        self.client.post(self.url,dict(self.data,taxpayer_type='Юридическое лицо'))
        self.assertEqual(self.client.get(self.url+'export/').json()['data']['taxpayer_type'],'Юридическое лицо')
        self.client.post('/settings/',dict(data,taxpayer_type='Выдуманный тип'))
        self.assertEqual(CompanySettings.objects.get(owner=self.owner).taxpayer_type,'Юридическое лицо')
        self.client.post('/settings/',{'action':'company','company_bin':self.requisites['company_bin']})
        self.assertEqual(CompanySettings.objects.get(owner=self.owner).taxpayer_type,'Юридическое лицо')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get('/settings/').context['company_form'].initial['taxpayer_type'],'')

    def test_older_preparation_inherits_type_for_export_but_keeps_saved_choice(self):
        self.lot.announcement.external_id=17920228;self.lot.announcement.save()
        self.client.post(self.url,self.data)
        saved=ApplicationPreparation.objects.get(owner=self.owner)
        saved.data.pop('taxpayer_type',None);saved.save()
        self.client.post('/settings/',dict(self.requisites,taxpayer_type='Юридическое лицо'))
        self.assertEqual(self.client.get(self.url+'export/').json()['data']['taxpayer_type'],'Юридическое лицо')
        saved.data['taxpayer_type']='Юридическое лицо нерезидент';saved.save()
        self.assertEqual(self.client.get(self.url+'export/').json()['data']['taxpayer_type'],'Юридическое лицо нерезидент')
