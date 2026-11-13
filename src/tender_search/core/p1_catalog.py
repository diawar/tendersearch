"""Server-only p1 transport and dealer offers; sale prices are never costs."""
import json
import re
from decimal import Decimal

import httpx
from django.conf import settings


ENDPOINT = 'https://p1.overclockers.kz/api/master-catalog/all'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class P1CatalogError(ValueError):
    pass


def procurement_offers(product, quantity=1):
    """One independently stocked warehouse per offer; never pool stock.

    Owner-confirmed dealersData format: warehouse (stock) - purchase incl. VAT
    / unused price / unused price. VAT invoice eligibility remains separate.
    Unknown/changed formats are reported, never guessed or priced at zero.
    """
    from .p1_warehouses import WAREHOUSE_CITIES
    try:
        required = Decimal(str(quantity))
        if not required.is_finite() or required <= 0:
            raise ValueError()
    except (ValueError, ArithmeticError):
        raise P1CatalogError('Укажите положительное количество товара.') from None
    offers, unknown, excluded, invalid = [], set(), set(), 0
    entries = product.get('dealersData')
    if not isinstance(entries, list):
        return {'offers': [], 'unknown_warehouses': [], 'excluded_warehouses': [], 'invalid_entries': 1}
    number = r'\d+(?:[.,]\d+)?'
    pattern = rf'(.+?)\s*\(\s*({number})\s*\)\s*[-–—]\s*({number})\s*/\s*[^/]+\s*/\s*[^/]+'
    for entry in entries:
        match = re.fullmatch(pattern, entry.strip()) if isinstance(entry, str) else None
        if not match:
            invalid += 1
            continue
        warehouse, stock_text, cost_text = match.groups()
        warehouse = warehouse.strip()
        if warehouse not in WAREHOUSE_CITIES:
            unknown.add(warehouse)
            continue
        city = WAREHOUSE_CITIES[warehouse]
        if city is None:
            excluded.add(warehouse)
            continue
        stock, cost = (Decimal(value.replace(',', '.')) for value in (stock_text, cost_text))
        if cost <= 0:
            invalid += 1
            continue
        if stock < required:
            continue
        offer = {'warehouse': warehouse, 'origin_city': city, 'stock': stock,
                 'purchase_unit_gross': cost, 'purchase_vat_included': True}
        if offer not in offers:
            offers.append(offer)
    seen, conflicts = {}, set()
    for offer in offers:
        warehouse = offer['warehouse']
        if warehouse in seen and seen[warehouse] != offer:
            conflicts.add(warehouse)
        seen[warehouse] = offer
    invalid += len(conflicts)
    offers = [offer for offer in offers if offer['warehouse'] not in conflicts]
    return {'offers': offers, 'unknown_warehouses': sorted(unknown),
            'excluded_warehouses': sorted(excluded), 'invalid_entries': invalid}


def fetch_page(page_number=0, page_size=25):
    """Return one unmodified JSON page; never infer cost from sale prices.

    No automatic pagination until the real response's rows/total schema is
    confirmed. No tokens or response bodies are included in error messages.
    """
    if type(page_number) is not int or not 0 <= page_number <= 100000:
        raise P1CatalogError('Неверный номер страницы p1.')
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise P1CatalogError('Размер страницы p1 должен быть от 1 до 100.')
    token = getattr(settings, 'P1_API_TOKEN', '').strip()
    if not token:
        raise P1CatalogError('Добавьте P1_API_TOKEN в серверный .env.')
    if any(ord(char) < 32 or ord(char) > 126 for char in token):
        raise P1CatalogError('Неверный формат токена p1.')
    payload = {
        'pageSize': page_size, 'pageNumber': page_number,
        'filterModels': [], 'sort': {'active': 'skuOver', 'direction': 'asc'},
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(25, connect=10), follow_redirects=False) as client:
            with client.stream('POST', ENDPOINT, headers={
                'Authorization': 'Bearer ' + token,
                'Content-Type': 'application/json', 'Accept': 'application/json',
            }, json=payload) as response:
                if response.status_code in (401, 403):
                    raise P1CatalogError('p1 отклонил доступ: проверьте токен и права чтения каталога.')
                if not 200 <= response.status_code < 300:
                    raise P1CatalogError(f'p1 вернул HTTP {response.status_code}.')
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise P1CatalogError('Ответ p1 слишком большой. Уменьшите размер страницы.')
        def reject_constant(value):
            raise ValueError('Non-finite JSON number')
        result = json.loads(data, parse_float=Decimal, parse_constant=reject_constant)
        if not isinstance(result, (dict, list)):
            raise ValueError('Unexpected JSON root')
        return result
    except (httpx.HTTPError, ValueError, RecursionError) as error:
        if isinstance(error, P1CatalogError):
            raise
        raise P1CatalogError('Не удалось получить корректный ответ каталога p1.') from None
