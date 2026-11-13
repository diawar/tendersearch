from unittest.mock import patch
from decimal import Decimal
from django.test import TestCase, override_settings
from .models import ApplicationPreparation, UserLotPrice, UserLotNote, LotObligationCosts, CompanySettings


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PersonalDataPrivacyTests(TestCase):
    def setUp(self):
        from .test_preparation import PreparationTests
        PreparationTests.setUp(self)
        self.memo_url=f'/lots/{self.lot.pk}/note/'
        self.cost_url=self.url+'obligations/'
        for user,marker,amount in [(self.owner,'ALPHA_PRIVATE',116),(self.other,'BETA_PRIVATE',232)]:
            CompanySettings.objects.update_or_create(owner=user,defaults={'company_bin':'241040001415'})
            ApplicationPreparation.objects.create(owner=user,lot=self.lot,company_bin='241040001415',
                data={**self.data,'product_name':marker+'_PRODUCT','notes':marker+'_PREPARATION_NOTE'})
            UserLotNote.objects.create(owner=user,lot=self.lot,text=marker+'_MEMO')
            UserLotPrice.objects.create(owner=user,lot=self.lot,unit_gross=amount,vat_rate=16)
            LotObligationCosts.objects.create(owner=user,lot=self.lot,entries=[{'label':marker+'_EXPENSE','amount':str(amount)}])

    def test_same_lot_pages_and_fragments_only_render_current_users_data(self):
        for user,own,foreign in [(self.owner,'ALPHA_PRIVATE','BETA_PRIVATE'),(self.other,'BETA_PRIVATE','ALPHA_PRIVATE')]:
            self.client.force_login(user)
            for headers in [{},{'HTTP_HX_REQUEST':'true'}]:
                page=self.client.get(f'/lots/{self.lot.pk}/',**headers)
                self.assertContains(page,own+'_MEMO')
                self.assertNotContains(page,foreign)
                self.assertEqual(page.context['saved_price'].owner_id,user.pk)
                self.assertIn('no-store',page['Cache-Control'])
            page=self.client.get(self.url)
            self.assertContains(page,own+'_PRODUCT');self.assertContains(page,own+'_EXPENSE')
            self.assertContains(page,own+'_PREPARATION_NOTE');self.assertNotContains(page,foreign)
            self.assertEqual(page.context['saved_price'].owner_id,user.pk)
            self.assertIn('no-store',page['Cache-Control'])
            for path in ['/workspace/?profile=all&stage=preparing','/favorites/','/search/']:
                page=self.client.get(path,follow=True)
                self.assertEqual(page.status_code,200)
                self.assertNotContains(page,foreign)
                self.assertIn('private',page['Cache-Control']);self.assertIn('no-store',page['Cache-Control'])

    def test_saving_notes_expenses_and_preparation_cannot_target_another_owner(self):
        self.client.force_login(self.other)
        self.client.post(self.memo_url,{'note':'BETA_NEW_MEMO','owner':self.owner.pk})
        self.client.post(self.cost_url,{'entries':[{'label':'BETA_NEW_EXPENSE','amount':'9'}],'owner':self.owner.pk},content_type='application/json')
        response=self.client.post(self.url,{**self.data,'product_name':'BETA_NEW_PRODUCT','owner':self.owner.pk})
        self.assertEqual(response.status_code,302)
        response=self.client.post(self.endpoint,{'unit_gross':'348','confirm_discount':'on','owner':self.owner.pk})
        self.assertEqual(response.status_code,302)
        self.assertEqual(UserLotPrice.objects.get(owner=self.other,lot=self.lot).unit_gross,Decimal(348))
        self.assertEqual(UserLotNote.objects.get(owner=self.owner,lot=self.lot).text,'ALPHA_PRIVATE_MEMO')
        self.assertEqual(LotObligationCosts.objects.get(owner=self.owner,lot=self.lot).entries[0]['label'],'ALPHA_PRIVATE_EXPENSE')
        self.assertEqual(ApplicationPreparation.objects.get(owner=self.owner,lot=self.lot).data['product_name'],'ALPHA_PRIVATE_PRODUCT')
        self.assertEqual(UserLotPrice.objects.get(owner=self.owner,lot=self.lot).unit_gross,Decimal(116))

    def test_export_is_current_users_preparation_even_if_same_bin_and_lot(self):
        self.client.force_login(self.other)
        with patch('tender_search.core.preparation.supports_preparation_export',return_value=True):
            response=self.client.get(self.url+'export/',{'owner':self.owner.pk})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['data']['product_name'],'BETA_PRIVATE_PRODUCT')
        self.assertNotIn('ALPHA_PRIVATE',response.content.decode())
        self.assertNotIn('notes',response.json()['data'])
