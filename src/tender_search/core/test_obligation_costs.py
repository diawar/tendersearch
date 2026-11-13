from decimal import Decimal
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings
from .obligation_costs import suggestions, normalize_entries
from .test_deal_analysis import DealEstimateAccessTests
from .models import LotObligationCosts, ApplicationPreparation, Document, DocumentVersion


class ObligationExtractionTests(SimpleTestCase):
    def test_evidence_categories_and_unknown_costs_without_ai(self):
        docs=[{'name':'ТС.pdf','text':'Поставка с разгрузкой и подъёмом на 15-й этаж. Настройка и ввод в эксплуатацию. Гарантийное обслуживание.'}]
        found=suggestions(docs)
        self.assertEqual(len(found),4)
        self.assertTrue(all(r['amount']=='' and r['document']=='ТС.pdf' for r in found))
        self.assertIn('Подъём и занос в помещение',[r['label'] for r in found])
        self.assertEqual(suggestions(docs+docs),found)

    def test_zero_differs_from_unpriced_and_money_is_not_rounded_silently(self):
        self.assertEqual(normalize_entries([{'label':'Подъем','amount':'0'}],require_amounts=True)[1],0)
        for value in ['',None,'-1','NaN','1.001']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_entries([{'label':'Подъем','amount':value}],require_amounts=True)


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ObligationIntegrationTests(TestCase):
    def setUp(self):
        DealEstimateAccessTests.setUp(self)
        self.cost_url=f'/lots/{self.lot.pk}/preparation/obligations/'
        self.client.force_login(self.owner)

    def test_costs_reduce_profit_once_for_entire_lot_and_increase_cash(self):
        entries=[{'label':'Подъём','amount':'15'}, {'label':'Настройка','amount':'5'}]
        base=self.client.post(self.url,self.data,content_type='application/json').json()['result']
        response=self.client.post(self.url,{**self.data,'obligations':entries,'other_costs':3},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        result=response.json()['result']
        self.assertEqual(result['obligations_total'],'20.00')
        self.assertEqual(Decimal(base['profit_before_tax'])-Decimal(result['profit_before_tax']),23)
        self.assertEqual(Decimal(result['cash_required'])-Decimal(base['cash_required']),23)
        self.assertEqual(result['purchase_vat_credit'],base['purchase_vat_credit'])
        self.assertEqual(result['other_costs_excluding_obligations'],'3')

    def test_saved_costs_survive_reload_without_creating_or_overwriting_preparation(self):
        response=self.client.post(self.cost_url,{'entries':[{'label':'Монтаж','amount':'10'}]},content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertFalse(ApplicationPreparation.objects.exists())
        saved=LotObligationCosts.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(saved.entries[0]['amount'],'10.00')
        page=self.client.get(f'/lots/{self.lot.pk}/preparation/')
        self.assertContains(page,'10.00')
        result=self.client.post(self.url,self.data,content_type='application/json').json()['result']
        self.assertEqual(result['obligations_total'],'10.00')

    def test_cost_rows_are_inside_calculator_and_saved_rows_remain(self):
        from pathlib import Path
        LotObligationCosts.objects.create(owner=self.owner,lot=self.lot,entries=[{'label':'Подъём','amount':'15.00'}])
        response=self.client.get(f'/lots/{self.lot.pk}/preparation/')
        self.assertEqual(response.status_code,200)
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(response.content,'html.parser')
        self.assertIsNotNone(soup.select_one('#deal-estimate-form #obligation-costs'))
        self.assertFalse(soup.select('#obligation-costs form'))
        self.assertFalse(soup.select('#obligation-costs-form'))
        self.assertContains(response,'15.00')
        target=Path('/work/data')
        if target.is_dir():(target/'calculator-compact-preview.html').write_bytes(response.content)

    def test_user_added_unpriced_costs_block_estimate_before_carrier_request(self):
        doc=Document.objects.create(announcement=self.lot.announcement,lot=self.lot,source_id='obl',kind='technical',name='ТС',url='https://example.com/ts')
        DocumentVersion.objects.create(document=doc,sha256='a'*64,text='Требуется разгрузка и настройка оборудования.')
        LotObligationCosts.objects.create(owner=self.owner,lot=self.lot,entries=[{'label':'Разгрузка','amount':''}])
        with patch('tender_search.core.jetlogistic.quote') as quote:
            response=self.client.post(self.url,{**self.data,'delivery_mode':'jet'},content_type='application/json')
            self.assertEqual(response.status_code,400)
            self.assertIn('Оцените все',response.json()['error'])
            quote.assert_not_called()

    def test_costs_are_private_and_stage_restricted(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.cost_url,{'entries':[]},content_type='application/json').status_code,404)
        self.assertFalse(LotObligationCosts.objects.exists())
        self.client.force_login(self.owner)
        from .models import UserLotWorkflow
        UserLotWorkflow.objects.filter(owner=self.owner,lot=self.lot).update(stage='applied')
        self.assertEqual(self.client.post(self.cost_url,{'entries':[]},content_type='application/json').status_code,404)


class ManualCostContextTests(SimpleTestCase):
    def test_no_automatic_cost_rows_from_specification(self):
        from .obligation_costs import context
        with patch('tender_search.core.models.LotObligationCosts.objects') as objects:
            objects.filter.return_value.first.return_value=None
            self.assertEqual(context(None,None,[{'text':'Подъём и настройка','name':'ТС'}])['obligation_entries'],[])
