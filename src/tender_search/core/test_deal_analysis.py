import json
from decimal import Decimal
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from .catalog_sku import parse_sku_index, product_by_sku
from .deal_economics import calculate_deal
from .deal_views import suggest_destination
from .jetlogistic import parse_quote, quote
from .models import UserLotWorkflow, AnnouncementLotCache, CompanySettings, ApplicationPreparation, SearchProfile, CollectionLot
from .search_service import store_lot
from .test_index import row


class DealCalculationTests(SimpleTestCase):
    def deal(self, **kwargs):
        params = dict(unit_gross='116000', quantity=1, sale_vat_rate=16,
                      purchase_unit_gross='58000', purchase_vat_rate=16,
                      purchase_vat_credit=True, delivery_gross=0,
                      tax_mode='profit', income_tax_rate=20)
        return calculate_deal(**{**params, **kwargs})

    def test_vat_is_not_subtracted_twice(self):
        result = self.deal()
        self.assertEqual(result['profit_before_tax'], Decimal('50000'))
        self.assertEqual(result['vat_balance'], Decimal('8000'))
        self.assertEqual(result['income_tax'], Decimal('10000'))
        self.assertEqual(result['profit_after_tax'], Decimal('40000'))
        self.assertEqual(result['margin_pct'], Decimal('40'))
        self.assertEqual(result['revenue_gross'] - result['purchase_gross'] - result['vat_balance'] - result['income_tax'], result['profit_after_tax'])

    def test_non_vat_payer_cannot_claim_purchase_credit(self):
        result = self.deal(sale_vat_rate=0)
        self.assertEqual(result['purchase_vat_credit'], 0)
        self.assertEqual(result['profit_before_tax'], Decimal('58000'))
        self.assertEqual(result['economic_profit'], Decimal('46400'))

    def test_missing_credit_and_unknown_income_tax_are_not_invented(self):
        result = self.deal(purchase_vat_credit=False, tax_mode='unknown')
        self.assertEqual(result['purchase_net'], Decimal('58000'))
        self.assertIsNone(result['economic_profit'])
        self.assertIsNone(result['margin_pct'])

    def test_refundable_security_and_opportunity_cost_are_separate(self):
        result = self.deal(refundable_security=100000, freeze_days=365, annual_money_rate=10)
        self.assertEqual(result['profit_before_tax'], Decimal('50000'))
        self.assertEqual(result['income_tax'], Decimal('10000'))
        self.assertEqual(result['freeze_cost'], Decimal('10000'))
        self.assertEqual(result['economic_profit'], Decimal('30000'))
        self.assertEqual(result['cash_required'], Decimal('158000'))

    def test_loss_does_not_generate_negative_profit_tax_but_turnover_tax_remains(self):
        result = self.deal(purchase_unit_gross=200000)
        self.assertLess(result['profit_before_tax'], 0)
        self.assertEqual(result['income_tax'], 0)
        result = self.deal(purchase_unit_gross=200000, tax_mode='turnover', income_tax_rate=4)
        self.assertEqual(result['income_tax'], Decimal('4000'))

    def test_delivery_vat_requires_explicit_credit(self):
        result = self.deal(delivery_gross=11600, delivery_vat_rate=16)
        self.assertEqual(result['delivery_net'], Decimal('11600'))
        result = self.deal(delivery_gross=11600, delivery_vat_rate=16, delivery_vat_credit=True)
        self.assertEqual(result['delivery_net'], Decimal('10000'))

    def test_invalid_and_missing_inputs_fail(self):
        for change in [dict(purchase_unit_gross=None), dict(delivery_gross=None),
                       dict(quantity='NaN'), dict(unit_gross='Infinity'),
                       dict(delivery_gross=-1), dict(tax_mode='profit', income_tax_rate=None),
                       dict(quantity='.000001', unit_gross='.01')]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.deal(**change)


class CatalogueSkuTests(SimpleTestCase):
    def test_exact_mapping_preserves_zeroes_and_removes_conflicts_external_urls(self):
        data = b'''<products>
          <product><sku>0012</sku><url>https://over-shop.kz/catalog/monitory/123/</url></product>
          <product><sku>13</sku><url>https://evil.example/catalog/monitory/123/</url></product>
          <product><sku>14</sku><url>https://over-shop.kz/catalog/monitory/123/</url></product>
          <product><sku>14</sku><url>https://over-shop.kz/catalog/monitory/124/</url></product>
        </products>'''
        self.assertEqual(parse_sku_index(data), {'0012': 'https://over-shop.kz/catalog/monitory/123/'})
        with patch('tender_search.core.catalog_sku.sku_index', return_value={'0012': 'unused'}), patch('tender_search.core.catalog_sku.httpx.Client') as client:
            with self.assertRaises(ValueError):
                product_by_sku('12')
            client.assert_not_called()

    def test_entities_and_empty_or_wrong_schema_are_rejected(self):
        for data in [b'<!DOCTYPE x><products/>', b'<html/>', b'<products/>']:
            with self.assertRaises(ValueError):
                parse_sku_index(data)


class JetCalculatorTests(SimpleTestCase):
    def response(self):
        return {'success': 1, 'answer': ['3385', '2510', '1', '0', 'Павлодар', 'Павлодар', 'Алматы', 'Алматы', '677', '502', '0', '0', '3', '3']}

    def test_linehaul_is_not_mistaken_for_door_to_door_total(self):
        result = parse_quote(self.response())
        self.assertEqual(result['options']['terminal_to_terminal'], '2510')
        self.assertEqual(result['options']['door_to_door'], '5896')
        self.assertEqual(result['currency'], 'KZT')
        self.assertIsNone(result['vat_included'])
        self.assertEqual(result['days_max'], 3)

    def test_unavailable_or_changed_responses_are_not_zero_freight(self):
        for data in [{'success': 0, 'answer': 'unavailable'}, {'success': 1, 'answer': []},
                     {'success': 1, 'answer': ['NaN'] * 14}]:
            with self.assertRaises(ValueError):
                parse_quote(data)

    def test_missing_or_fractional_dimensions_and_unsupported_city_stop_before_post(self):
        params = dict(origin='A', destination='B', weight_kg=10, volume_m3='.05', packages=1, cargo_type='ОРГТЕХНИКА')
        with patch('tender_search.core.jetlogistic.cities', return_value=['A', 'B']), patch('tender_search.core.jetlogistic.httpx.Client') as client:
            for change in [dict(origin='Unknown'), dict(packages='1.5'), dict(volume_m3=None), dict(weight_kg=0), dict(cargo_type='')]:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    quote(**{**params, **change})
            client.assert_not_called()

    def test_city_is_not_inferred_from_customer_or_region(self):
        cities = ['Павлодар-(Павлодар город)', 'Алматы-(Алма-Ата)']
        self.assertEqual(suggest_destination(['Казахстан, г. Павлодар'], cities), cities[0])
        self.assertEqual(suggest_destination(['Павлодарская область'], cities), '')
        self.assertEqual(suggest_destination(['Павлодар', 'Алматы'], cities), '')


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DealEstimateAccessTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('deal-owner')
        self.other = get_user_model().objects.create_user('deal-other')
        self.lot = store_lot(row(1, system_id=1, purchase_method_id=12))
        UserLotWorkflow.objects.create(owner=self.owner, lot=self.lot, stage='preparing')
        profile = SearchProfile.objects.create(owner=self.owner, name='Мои лоты')
        CollectionLot.objects.create(profile=profile, lot=self.lot, stage='preparing')
        CompanySettings.objects.create(owner=self.owner, company_bin='241040001415', is_vat_payer=True)
        AnnouncementLotCache.objects.create(pk=self.lot.announcement.source_id, rows=[{
            'id': self.lot.source_id, 'system': {'id': 1}, 'is_combined': False,
            'plan_items': [{'count': 2, 'unit_price': 100, 'system': {'id': 1}, 'purchase_method': {'id': 12}}]}])
        self.lot.amount = 200
        self.lot.save(update_fields=['amount'])
        self.url = f'/lots/{self.lot.pk}/preparation/deal-estimate/'
        self.data = {'unit_gross': '116', 'purchase_unit_gross': '58', 'purchase_vat_credit': True,
                     'purchase_vat_rate': 16, 'delivery_mode': 'manual', 'delivery_gross': 0,
                     'tax_mode': 'profit', 'income_tax_rate': 20}

    def test_owner_and_stage_are_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.other)
        with patch('tender_search.core.jetlogistic.cities') as provider:
            self.assertEqual(self.client.get(self.url).status_code, 404)
            provider.assert_not_called()
        self.client.force_login(self.owner)
        UserLotWorkflow.objects.filter(owner=self.owner).update(stage='applied')
        self.assertEqual(self.client.post(self.url, self.data, content_type='application/json').status_code, 200)

    def test_preparation_page_has_manual_dimensions_and_no_blocking_carrier_request(self):
        self.client.force_login(self.owner)
        with patch('tender_search.core.jetlogistic.cities') as provider:
            response = self.client.get(f'/lots/{self.lot.pk}/preparation/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Объём всей отправки с упаковкой')
        self.assertContains(response, 'Число грузовых мест')
        provider.assert_not_called()

    def test_manual_estimate_uses_company_vat_and_verified_quantity_without_writes(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {**self.data, 'sale_vat_rate': 0, 'quantity': 1000}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['result']['economic_profit'], '80.00')
        self.assertEqual(response.json()['quantity'], '2')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner).stage, 'preparing')
        self.assertFalse(ApplicationPreparation.objects.exists())

    def test_budget_and_unknown_delivery_block_estimate(self):
        self.client.force_login(self.owner)
        for data in [{**self.data, 'unit_gross': 200}, {**self.data, 'delivery_gross': ''}]:
            self.assertEqual(self.client.post(self.url, data, content_type='application/json').status_code, 400)

    def test_non_vat_company_uses_entered_price_without_dividing_by_116(self):
        self.client.force_login(self.owner)
        CompanySettings.objects.filter(owner=self.owner).update(is_vat_payer=False)
        response = self.client.post(self.url, {**self.data, 'unit_gross': '100'}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result']['economic_profit'], '67.20')
        self.assertEqual(response.json()['result']['purchase_vat_credit'], '0')

    def test_quote_failure_does_not_become_free_delivery(self):
        self.client.force_login(self.owner)
        with patch('tender_search.core.jetlogistic.quote', side_effect=httpx.ReadTimeout('unavailable')):
            response = self.client.post(self.url, {**self.data, 'delivery_mode': 'jet'}, content_type='application/json')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('result', response.json())

    def test_only_door_to_door_is_used_for_new_estimates(self):
        self.client.force_login(self.owner)
        with patch('tender_search.core.jetlogistic.quote',return_value={'options':{'door_to_door':'10','terminal_to_terminal':'1'}}):
            response=self.client.post(self.url,{**self.data,'delivery_mode':'jet','delivery_option':'terminal_to_terminal'},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(str(response.json()['delivery_gross']),'10')
        self.assertEqual(response.json()['saved']['inputs']['delivery_option'],'door_to_door')
