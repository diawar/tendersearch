"""Read-only adapter for Jet Logistic's public calculator (not a partner API)."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from django.core.cache import cache

from .deal_economics import decimal_value

BASE = 'https://jetlogistic.kz'
QUOTE_URL = BASE + '/ajax/calc.php'
CARGO_TYPES = ('ОРГТЕХНИКА', 'КОМПЛЕКТУЮЩИЕ', 'АКСЕССУАРЫ', 'КАНЦТОВАРЫ',
               'ОБОРУДОВАНИЕ', 'ПРОМЫШЛЕННОЕ ОБОРУДОВАНИЕ', 'БЫТОВАЯ ТЕХНИКА')


def read_response(response, limit):
    response.raise_for_status()
    data = bytearray()
    for chunk in response.iter_bytes():
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError('Слишком большой ответ Jet Logistic.')
    return bytes(data)


def cities():
    saved = cache.get('jetlogistic-cities-v1')
    if saved is not None:
        return saved
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        with client.stream('GET', BASE + '/cabinet/cities.csv') as response:
            text = read_response(response, 2 * 1024 * 1024).decode('utf-8-sig')
    result = sorted(set(line.split(';')[0].strip() for line in text.splitlines()
                        if '-(' in line.split(';')[0] and len(line.split(';')[0]) < 250))
    if not result:
        raise ValueError('Не удалось прочитать города Jet Logistic.')
    cache.set('jetlogistic-cities-v1', result, 86400)
    return result


def parse_quote(data):
    values = data.get('answer') if isinstance(data, dict) and data.get('success') == 1 else None
    if not isinstance(values, list) or len(values) != 14:
        raise ValueError('Jet Logistic не рассчитал тариф или изменил формат ответа.')
    pickup, linehaul, last_mile, extra = [decimal_value(v, maximum=Decimal('1000000000')) for v in values[:4]]
    if linehaul <= 0:
        raise ValueError('Не подтверждена стоимость перевозки Jet Logistic.')
    days = [decimal_value(v, maximum=Decimal(365)) for v in values[12:14]]
    if any(v != v.to_integral_value() for v in days) or days[0] > days[1]:
        raise ValueError('Не подтверждён срок перевозки Jet Logistic.')
    names = ('door_to_door', 'terminal_to_door', 'door_to_terminal', 'terminal_to_terminal')
    totals = (pickup + linehaul + last_mile + extra, linehaul + last_mile + extra,
              pickup + linehaul + extra, linehaul + extra)
    almaty_route = values[4] == 'Алматы' or values[7] == 'Алматы'
    available = (bool((pickup > 0 and last_mile > 0) or almaty_route),
                 bool(last_mile > 0 or almaty_route), bool(pickup > 0 or almaty_route), True)
    return {'currency': 'KZT', 'options': {key: str(value) for key, value, valid in zip(names, totals, available) if valid},
            'days_min': int(days[0]), 'days_max': int(days[1]), 'vat_included': None,
            'source_url': QUOTE_URL, 'quoted_at': datetime.now(timezone.utc).isoformat(),
            'preliminary': True}


def quote(*, origin, destination, weight_kg, volume_m3, packages, cargo_type):
    allowed = cities()
    if origin not in allowed or destination not in allowed or origin == destination:
        raise ValueError('Выберите разные города из справочника Jet Logistic.')
    if cargo_type not in CARGO_TYPES:
        raise ValueError('Укажите характер груза из списка.')
    weight = decimal_value(weight_kg, minimum=Decimal('.001'), maximum=Decimal(5000))
    volume = decimal_value(volume_m3, minimum=Decimal('.000001'), maximum=Decimal(20))
    count = decimal_value(packages, minimum=Decimal(1), maximum=Decimal(10000))
    if count != count.to_integral_value():
        raise ValueError('Число мест должно быть целым.')
    payload = {'cityfrom': origin, 'cityto': destination, 'ves': str(weight), 'obm3': str(volume),
               'mest': str(int(count)), 'noAuth': '1', 'NAME_PARAMETR2': cargo_type}
    key = 'jetlogistic-quote-v1-' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    saved = cache.get(key)
    if saved is not None:
        return saved
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        with client.stream('POST', QUOTE_URL, data=payload,
                           headers={'Referer': BASE + '/', 'X-Requested-With': 'XMLHttpRequest'}) as response:
            data = json.loads(read_response(response, 65536).decode('utf-8-sig'))
    result = {**parse_quote(data), 'shipment': payload}
    cache.set(key, result, 900)
    return result
