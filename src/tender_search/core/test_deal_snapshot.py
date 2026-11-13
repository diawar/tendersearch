from unittest.mock import patch
from django.test import TestCase, override_settings
from . import test_deal_analysis
from .models import LotDealEstimate, UserLotWorkflow

@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SavedDealTests(TestCase):
    def setUp(self):
        test_deal_analysis.DealEstimateAccessTests.setUp(self)
        self.client.force_login(self.owner)
        self.data.update(product_name='Private monitor',procurement_mode='manual')

    def post(self,**changes):
        return self.client.post(self.url,{**self.data,**changes},content_type='application/json')

    def test_save_restore_and_private_direct_links(self):
        response=self.post()
        self.assertEqual(response.status_code,200,response.content)
        saved=LotDealEstimate.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(saved.inputs['product_name'],'Private monitor')
        with patch('tender_search.core.jetlogistic.quote') as quote,patch('tender_search.core.p1_deal.compare') as compare:
            page=self.client.get(f'/lots/{self.lot.pk}/preparation/')
            self.assertContains(page,'saved-deal-estimate')
            quote.assert_not_called();compare.assert_not_called()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.assertEqual(self.post().status_code,404)
        self.assertEqual(LotDealEstimate.objects.count(),1)

    def test_contract_freezes_commercial_and_delivery_changes_recalculate(self):
        self.assertEqual(self.post().status_code,200)
        UserLotWorkflow.objects.filter(owner=self.owner,lot=self.lot).update(stage='contract')
        for change in ({'product_name':'Changed'},{'unit_gross':'100'},{'purchase_unit_gross':'40'}):
            self.assertEqual(self.post(**change).status_code,400)
        response=self.post(delivery_gross=10)
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['result']['economic_profit'],'72.00')
        UserLotWorkflow.objects.filter(owner=self.owner,lot=self.lot).update(stage='delivery')
        self.assertEqual(self.post(delivery_gross=20).status_code,200)
        UserLotWorkflow.objects.filter(owner=self.owner,lot=self.lot).update(stage='payment')
        self.assertEqual(self.post(delivery_gross=30).status_code,400)
        response=self.post(delivery_gross=20,other_costs=5)
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['delivery_gross'],'20')
        self.assertTrue(response.json()['saved']['commercial_locked'])
        self.assertTrue(response.json()['saved']['delivery_locked'])

    def test_failure_and_stage_change_do_not_overwrite_saved_result(self):
        self.assertEqual(self.post().status_code,200)
        old=LotDealEstimate.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(self.post(unit_gross=200).status_code,400)
        saved=LotDealEstimate.objects.get(pk=old.pk)
        self.assertEqual(saved.updated_at,old.updated_at)
        def changed(**kwargs):
            UserLotWorkflow.objects.filter(owner=self.owner,lot=self.lot).update(stage='contract')
            return {'options':{'door_to_door':10},'days_min':1,'days_max':2}
        with patch('tender_search.core.jetlogistic.quote',side_effect=changed):
            response=self.post(delivery_mode='jet',delivery_option='door_to_door')
        self.assertEqual(response.status_code,400,response.content)
        saved.refresh_from_db();self.assertEqual(saved.updated_at,old.updated_at)

    def test_stale_tab_and_foreign_selection_cannot_overwrite(self):
        response=self.post();old_version=response.json()['saved']['version']
        self.assertEqual(self.post(delivery_gross=10).status_code,200)
        self.assertEqual(self.post(delivery_gross=20,estimate_version=old_version).status_code,400)
        self.client.force_login(self.other)
        response=self.client.post(self.url,{'action':'select_offer','version':old_version,'index':0},content_type='application/json')
        self.assertEqual(response.status_code,404)

    def test_restored_contract_page_displays_persisted_data(self):
        self.assertEqual(self.post().status_code,200)
        UserLotWorkflow.objects.filter(owner=self.owner,lot=self.lot).update(stage='contract')
        page=self.client.get(f'/lots/{self.lot.pk}/preparation/')
        self.assertContains(page,'saved-deal-estimate')
        self.assertTrue(page.context['saved_deal']['commercial_locked'])
        self.assertFalse(page.context['saved_deal']['delivery_locked'])
        import os
        from pathlib import Path
        if os.environ.get('DEAL_PREVIEW_PATH'):
            Path(os.environ['DEAL_PREVIEW_PATH']).write_bytes(page.content)

    def test_same_lot_two_owners_have_independent_products_and_costs(self):
        self.assertEqual(self.post().status_code,200)
        UserLotWorkflow.objects.create(owner=self.other,lot=self.lot,stage='preparing')
        self.client.force_login(self.other)
        self.assertEqual(self.post(product_name='Other private product',unit_gross='100',delivery_gross=3).status_code,200)
        self.assertEqual(LotDealEstimate.objects.count(),2)
        self.assertEqual(LotDealEstimate.objects.get(owner=self.owner,lot=self.lot).inputs['product_name'],'Private monitor')
        self.assertEqual(LotDealEstimate.objects.get(owner=self.other,lot=self.lot).inputs['product_name'],'Other private product')


    def test_p1_selected_allocation_survives_contract_delivery_recalculation(self):
        from .test_p1_deal import P1DealTests
        from .models import P1CatalogSnapshot
        from django.utils import timezone
        from . import deal_snapshot
        P1CatalogSnapshot.objects.create(pk=1,completed_at=timezone.now(),products={'00123':{
            'skuOver':'00123','name':'Stocked monitor','dealersData':['OVER AS (2) - 40 / 50 / 60','OVER PAV (2) - 50 / 60 / 70']}})
        data={**self.data,'procurement_mode':'p1','sku':'00123','delivery_mode':'jet','destination':'Target',
              'weight_kg':10,'volume_m3':'.1','packages':2,'cargo_type':'ОРГТЕХНИКА','delivery_option':'door_to_door'}
        with patch('tender_search.core.jetlogistic.cities',return_value=['Астана','Павлодар','Target']),patch('tender_search.core.jetlogistic.quote',return_value={'options':{'door_to_door':10},'days_min':1,'days_max':2}):
            response=self.client.post(self.url,data,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        response=self.client.post(self.url,{'action':'select_offer','version':response.json()['saved']['version'],'index':1},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        saved=LotDealEstimate.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(saved.selected_offer,1)
        values=deal_snapshot.effective_inputs(saved)
        self.assertEqual(values['origin'],'Павлодар')
        UserLotWorkflow.objects.filter(owner=self.owner,lot=self.lot).update(stage='contract')
        with patch('tender_search.core.p1_deal.compare') as source:
            response=self.client.post(self.url,{**values,'delivery_mode':'manual','delivery_gross':12},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['result']['purchase_gross'],'100.00')
        self.assertEqual(response.json()['saved']['snapshot']['procurement_plan']['allocations'][0]['warehouse'],'OVER PAV')
        source.assert_not_called()
        self.assertEqual(self.client.post(self.url,{'action':'select_offer','version':response.json()['saved']['version'],'index':0},content_type='application/json').status_code,400)
