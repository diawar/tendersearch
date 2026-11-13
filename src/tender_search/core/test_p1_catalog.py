import json
from decimal import Decimal
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, override_settings

from .p1_catalog import ENDPOINT, P1CatalogError, fetch_page, procurement_offers


@override_settings(P1_API_TOKEN='test-token')
class P1CatalogTests(SimpleTestCase):
    def test_confirmed_warehouses_cost_and_stock(self):
        result = procurement_offers({'dealersData': [
            'OVER AS (15) - 63063 / 70000 / 80000',
            'OVER PAV (14) - 1 / 2 / 3',
            'VENDER_P (50) - 8854 / 9500 / 15680',
        ]}, quantity=15)
        self.assertEqual([offer['origin_city'] for offer in result['offers']], ['Астана', 'Алматы'])
        self.assertEqual(result['offers'][1]['purchase_unit_gross'], Decimal('8854'))
        self.assertTrue(result['offers'][1]['purchase_vat_included'])

    def test_excluded_and_unknown_warehouses_never_used(self):
        result = procurement_offers({'dealersData': [
            'OVER ALB (100) - 1 / 2 / 3', 'VENDER_P_S (100) - 1 / 2 / 3',
            'NEW WAREHOUSE (100) - 1 / 2 / 3',
        ]})
        self.assertEqual(result['offers'], [])
        self.assertEqual(result['excluded_warehouses'], ['OVER ALB', 'VENDER_P_S'])
        self.assertEqual(result['unknown_warehouses'], ['NEW WAREHOUSE'])

    def test_insufficient_stock_is_not_pooled_and_bad_rows_not_guessed(self):
        result = procurement_offers({'dealersData': [
            'Abris (5) - 100 / 200 / 300', 'ASBIS (5) - 100 / 200 / 300',
            'Abris (100) - 0 / 200 / 300', 'unexpected format', None,
        ]}, quantity=10)
        self.assertEqual(result['offers'], [])
        self.assertEqual(result['invalid_entries'], 3)
        for quantity in ('NaN', 'Infinity', 0, -1, None):
            with self.assertRaises(P1CatalogError):
                procurement_offers({'dealersData': []}, quantity)

    def test_conflicting_rows_for_one_warehouse_do_not_double_stock(self):
        result=procurement_offers({'dealersData':[
            'Abris (5) - 100 / 200 / 300', 'Abris (5) - 110 / 200 / 300',
        ]})
        self.assertEqual(result['offers'],[])
        self.assertEqual(result['invalid_entries'],1)

    def request_page(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch('tender_search.core.p1_catalog.httpx.Client', return_value=client):
            return fetch_page()

    @override_settings(P1_API_TOKEN='')
    def test_missing_token_does_not_connect(self):
        with patch('tender_search.core.p1_catalog.httpx.Client') as client:
            with self.assertRaises(P1CatalogError):
                fetch_page()
            client.assert_not_called()

    def test_exact_contract_preserves_unknown_fields_and_prices(self):
        def handler(request):
            self.assertEqual(str(request.url), ENDPOINT)
            self.assertEqual(request.method, 'POST')
            self.assertEqual(request.headers['authorization'], 'Bearer test-token')
            self.assertEqual(json.loads(request.content), {
                'pageSize': 25, 'pageNumber': 0, 'filterModels': [],
                'sort': {'active': 'skuOver', 'direction': 'asc'},
            })
            return httpx.Response(200, content=b'{"rows":[{"skuOver":"00123","priceLoan":123.45,"priceBonus":100.00,"other":7}]}')
        result = self.request_page(handler)['rows'][0]
        self.assertEqual(result['skuOver'], '00123')
        self.assertEqual(result['priceLoan'], Decimal('123.45'))
        self.assertEqual(result['other'], 7)
        self.assertNotIn('cost', result)

    def test_redirect_and_auth_errors_are_sanitized(self):
        for status in (302, 401, 403, 500):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, headers={'location': 'https://example.com'}, text='test-token')
            with self.assertRaises(P1CatalogError) as caught:
                self.request_page(handler)
            self.assertEqual(len(calls), 1)
            self.assertNotIn('test-token', str(caught.exception))

    def test_invalid_response_is_rejected(self):
        for content in (b'<html>test-token</html>', b'null', b'{"price":NaN}'):
            with self.assertRaises(P1CatalogError):
                self.request_page(lambda request: httpx.Response(200, content=content))

    def test_page_bounds(self):
        for args in ((True, 25), (-1, 25), (0, 101), (0, 0)):
            with self.assertRaises(P1CatalogError):
                fetch_page(*args)
