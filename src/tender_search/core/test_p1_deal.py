from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import httpx
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import P1CatalogSnapshot
from .p1_catalog import P1CatalogError
from .p1_sync import product, process, claim
from .p1_deal import city_plans
from django.test import SimpleTestCase
from .test_deal_analysis import DealEstimateAccessTests


class CityAllocationTests(SimpleTestCase):
    def offer(self, warehouse, stock, cost, city='Алматы'):
        return {'warehouse':warehouse,'origin_city':city,'stock':Decimal(stock),'purchase_unit_gross':Decimal(cost)}

    def test_cheapest_stock_first_then_higher_with_weighted_cost(self):
        rows=[self.offer('expensive',20,120),self.offer('cheap',3,80),self.offer('middle',4,100)]
        plan=city_plans(rows,10)[0]
        self.assertEqual([(row['warehouse'],row['quantity']) for row in plan['allocations']], [('cheap',3),('middle',4),('expensive',3)])
        self.assertEqual(plan['purchase_total_gross'],Decimal('1000'))
        self.assertEqual(plan['purchase_unit_gross'],Decimal('100'))
        self.assertTrue(plan['multiple_pickups'])

    def test_cities_never_pool_stock(self):
        rows=[self.offer('a',5,1),self.offer('b',5,1,city='Астана')]
        self.assertEqual(city_plans(rows,6),[])

    def test_repeating_average_does_not_change_total(self):
        from .deal_economics import calculate_deal
        plan=city_plans([self.offer('a',1,1),self.offer('b',2,2)],3)[0]
        result=calculate_deal(unit_gross=10,quantity=3,sale_vat_rate=0,
            purchase_unit_gross=plan['purchase_unit_gross'],purchase_total_gross=plan['purchase_total_gross'],delivery_gross=0)
        self.assertEqual(result['purchase_gross'],Decimal('5.00'))


class P1DealTests(DealEstimateAccessTests):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.sku = '00123'
        P1CatalogSnapshot.objects.create(pk=1,completed_at=timezone.now(),products={self.sku:{
            'skuOver':self.sku,'name':'Монитор из p1','isArchived':False,'dealersData':[
                'OVER AS (2) - 40 / 900 / 999',
                'OVER PAV (2) - 50 / 900 / 999',
                'Abris (2) - 55 / 900 / 999',
                'ASBIS (2) - 60 / 900 / 999',
                'STN (0) - 1 / 2 / 3',
                'VENDER_P_S (100) - 1 / 2 / 3',
                'OVER ALB (100) - 1 / 2 / 3',
        ]}})
        self.p1_data={**self.data,'procurement_mode':'p1','sku':self.sku,'delivery_mode':'jet',
            'destination':'Усть-Каменогорск-(ВКО)','weight_kg':'10','volume_m3':'.1','packages':2,
            'cargo_type':'ОРГТЕХНИКА','delivery_option':'door_to_door',
            'origin':'IGNORE CLIENT CITY','purchase_unit_gross':'1','quantity':'100000'}
        cities=patch('tender_search.core.jetlogistic.cities',return_value=[
            'Алматы-(Алма-Ата)','Астана-(Астана)','Павлодар-(Павлодар)','Усть-Каменогорск-(ВКО)'])
        cities.start();self.addCleanup(cities.stop)

    def quote(self, **data):
        cost={'Астана':100,'Павлодар':30,'Алматы':10}[data['origin'].split('-(')[0]]
        return {'options':{'door_to_door':str(cost)},'days_min':2,'days_max':3}

    def test_owner_and_stage_are_required(self):
        self.client.logout()
        super().test_owner_and_stage_are_required()

    def test_ranks_cheapest_stocked_purchase_and_counts_delivery_ignoring_client_overrides(self):
        with patch('tender_search.core.jetlogistic.quote',side_effect=self.quote) as provider:
            response=self.client.post(self.url,self.p1_data,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        compared=response.json()['comparison']
        self.assertTrue(compared['comparison_complete'])
        self.assertEqual([row['warehouse'] for row in compared['offers']],['OVER AS','OVER PAV','Abris'])
        first=compared['offers'][0]
        self.assertEqual(Decimal(first['purchase_unit_gross']),Decimal('40'))
        self.assertEqual(first['result']['purchase_gross'],'80.00')
        self.assertEqual(first['landed_gross'],'180.00')
        self.assertEqual(provider.call_count,3)  # Once per city, not per supplier.
        self.assertEqual(compared['product']['sku'],self.sku)

    def test_partial_carrier_failure_is_visible_and_never_free(self):
        def tariff(**data):
            if data['origin'].startswith('Астана'):
                raise httpx.ReadTimeout('offline')
            return self.quote(**data)
        with patch('tender_search.core.jetlogistic.quote',side_effect=tariff):
            response=self.client.post(self.url,self.p1_data,content_type='application/json')
        comparison=response.json()['comparison']
        self.assertFalse(comparison['comparison_complete'])
        self.assertEqual(comparison['unavailable'][0]['warehouse'],'OVER AS')
        self.assertNotIn('OVER AS',[row['warehouse'] for row in comparison['offers']])

    def test_local_delivery_needs_explicit_amount_and_counts_it(self):
        with patch('tender_search.core.jetlogistic.quote',return_value={'options':{'door_to_door':'50'}}):
            missing=self.client.post(self.url,{**self.p1_data,'destination':'Алматы-(Алма-Ата)'},content_type='application/json').json()['comparison']
            explicit=self.client.post(self.url,{**self.p1_data,'destination':'Алматы-(Алма-Ата)','local_delivery_gross':'25'},content_type='application/json').json()['comparison']
        self.assertEqual(len(missing['unavailable']),1)
        row=next(row for row in explicit['offers'] if row['warehouse']=='Abris')
        self.assertEqual(row['delivery_gross'],'25')
        self.assertEqual(row['landed_gross'],'135.00')
        self.assertEqual(row['result']['delivery_vat_credit'],'0')

    def test_stale_catalog_and_unknown_sku_stop_before_freight(self):
        P1CatalogSnapshot.objects.update(completed_at=timezone.now()-timedelta(hours=3))
        with patch('tender_search.core.jetlogistic.quote') as provider:
            response=self.client.post(self.url,self.p1_data,content_type='application/json')
            self.assertEqual(response.status_code,400)
            provider.assert_not_called()
        with self.assertRaises(P1CatalogError):product('123')  # Exact SKU, preserve zeros.

    def test_sku_resolves_only_exact_public_url(self):
        with patch('tender_search.core.catalog_sku.sku_index',return_value={self.sku:'https://over-shop.kz/catalog/monitory/123/'}):
            response=self.client.get(self.url,{'product_url':'https://over-shop.kz/catalog/monitory/123/'})
            self.assertEqual(response.json()['sku'],self.sku)
            response=self.client.get(self.url,{'product_url':'https://other.example/123/'})
            self.assertEqual(response.status_code,400)


@override_settings(P1_API_TOKEN='test-token')
class P1SyncTests(TestCase):
    def test_partial_failure_keeps_previous_snapshot_and_sets_retry(self):
        old=timezone.now()-timedelta(hours=2)
        P1CatalogSnapshot.objects.create(pk=1,products={'old':{'name':'old'}},completed_at=old)
        lease=claim()
        first={'currentPage':0,'totalPages':2,'totalElements':2,'hasNext':True,'pageItems':[{'skuOver':'1','name':'one'}]}
        with patch('tender_search.core.p1_sync.fetch_page',side_effect=[first,P1CatalogError('failure')]):
            with self.assertRaises(P1CatalogError):process(lease)
        state=P1CatalogSnapshot.objects.get(pk=1)
        self.assertEqual(state.products,{'old':{'name':'old'}})
        self.assertEqual(state.completed_at,old)
        self.assertTrue(state.retry_at)

    def test_full_snapshot_and_lease_prevent_overlap(self):
        lease=claim()
        self.assertIsNone(claim())
        first={'currentPage':0,'totalPages':1,'totalElements':1,'hasNext':False,'pageItems':[
            {'skuOver':'00123','name':'one','dealersData':['Abris (2) - 10 / 20 / 30'],'isArchived':False,'secret_ignored':'value'}]}
        with patch('tender_search.core.p1_sync.fetch_page',return_value=first):process(lease)
        item,completed=product('00123')
        self.assertNotIn('secret_ignored',item)
        self.assertTrue(completed)
        self.assertIsNone(claim())
