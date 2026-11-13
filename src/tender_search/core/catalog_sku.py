"""Exact public SKU → product URLs; p1 credentials are not needed here."""
import re
from xml.etree import ElementTree

import httpx
from django.core.cache import cache

from .overshop import BASE, fetch, parse_product, public_url

FEED_URL = BASE + '/sku.xml'
MAX_FEED_BYTES = 8 * 1024 * 1024


def parse_sku_index(data):
    if len(data) > MAX_FEED_BYTES or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, re.I):
        raise ValueError('Небезопасный или слишком большой справочник SKU.')
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError('Не удалось прочитать XML справочника SKU.') from error
    if root.tag != 'products':
        raise ValueError('Изменился формат справочника SKU.')
    result, conflicts = {}, set()
    for item in root.findall('product'):
        sku = (item.findtext('sku') or '').strip()
        url = public_url((item.findtext('url') or '').strip())
        if not re.fullmatch(r'[\w.-]{1,100}', sku) or not url:
            continue
        if sku in result and result[sku] != url:
            conflicts.add(sku)
        result[sku] = url
    for sku in conflicts:
        result.pop(sku, None)
    if not result:
        raise ValueError('Справочник SKU пуст.')
    return result


def sku_index():
    saved = cache.get('overshop-sku-index-v1')
    if saved is not None:
        return saved
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        with client.stream('GET', FEED_URL) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_FEED_BYTES:
                    raise ValueError('Справочник SKU слишком большой.')
    result = parse_sku_index(bytes(data))
    cache.set('overshop-sku-index-v1', result, 3600)
    return result


def product_by_sku(sku):
    sku = str(sku).strip()  # Keep leading zeroes. Never search by partial SKU.
    url = sku_index().get(sku)
    if not url:
        raise ValueError('SKU не найден в публичном справочнике Over-shop.')
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        product = parse_product(fetch(client, url), url)
    return {**product, 'sku': sku, 'sku_source': FEED_URL}
