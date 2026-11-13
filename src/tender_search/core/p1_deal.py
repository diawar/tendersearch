"""Choose the lowest stocked purchase price; show delivery and margin separately."""
from decimal import Decimal, ROUND_HALF_UP

import httpx

from . import jetlogistic
from .p1_catalog import procurement_offers
from .p1_sync import product
from .deal_economics import decimal_value


def canonical_city(name, cities):
    matches = [city for city in cities if city.split('-(')[0].casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValueError('Город склада не найден однозначно в Jet Logistic.')
    return matches[0]


def compare(data, quantity, calculate):
    item, checked_at = product(data.get('sku'))
    parsed = procurement_offers(item, Decimal('.000001'))
    plans = city_plans(parsed['offers'], quantity)
    allowed = jetlogistic.cities()
    destination = data.get('destination')
    if destination not in allowed:
        raise ValueError('Выберите город поставки из справочника Jet Logistic.')
    if data.get('delivery_mode') != 'jet':
        raise ValueError('Для сравнения складов выберите расчёт Jet Logistic.')
    # Validate common inputs before any route: malformed dimensions/taxes must
    # not be mistaken for a carrier failure affecting only one warehouse.
    weight = decimal_value(data.get('weight_kg'), minimum=Decimal('.001'), maximum=Decimal(5000))
    volume = decimal_value(data.get('volume_m3'), minimum=Decimal('.000001'), maximum=Decimal(20))
    packages = decimal_value(data.get('packages'), minimum=Decimal(1), maximum=Decimal(10000))
    if packages != packages.to_integral_value() or data.get('cargo_type') not in jetlogistic.CARGO_TYPES:
        raise ValueError('Проверьте число мест и характер груза.')
    calculate(Decimal('.01'), Decimal(0), None)
    routes, offers, unavailable = {}, [], []
    for offer in plans:
        city = offer['origin_city']
        if city not in routes:
            try:
                origin = canonical_city(city, allowed)
                if origin == destination:
                    # Same-city delivery is not a free intercity quote.
                    amount = decimal_value(data.get('local_delivery_gross'))
                    routes[city] = (amount, {'local_manual': True, 'origin':origin, 'destination':destination})
                else:
                    freight = jetlogistic.quote(origin=origin, destination=destination, weight_kg=weight,
                        volume_m3=volume, packages=packages, cargo_type=data.get('cargo_type'))
                    if data.get('delivery_option') not in freight['options']:
                        raise ValueError('Выбранный вариант доставки недоступен.')
                    routes[city] = (Decimal(freight['options'][data['delivery_option']]), freight)
            except (ValueError, httpx.HTTPError):
                routes[city] = None
        route = routes[city]
        if route is None:
            unavailable.append({'warehouse':offer['warehouse'], 'origin_city':city,
                'reason':'Не получен тариф. Для доставки внутри города укажите подтверждённую сумму.'})
            continue
        delivery, freight = route
        result = calculate(offer['purchase_unit_gross'], delivery, freight, offer['purchase_total_gross'])
        offers.append({**offer, 'result': result, 'delivery_gross': delivery, 'freight':freight,
            'landed_gross':result['purchase_gross']+delivery,
            'landed_expense':result['purchase_net']+result['delivery_net']})
    offers.sort(key=lambda value:(value['purchase_total_gross'],value['landed_gross'],value['warehouse']))
    minimum = min(plans, key=lambda plan:plan['purchase_total_gross']) if plans else None
    return {'product': {'sku': str(item['skuOver']), 'name':item.get('name') or ''},
        'minimum_purchase':minimum,
        'catalog_checked_at':checked_at.isoformat(), 'offers':offers, 'unavailable':unavailable,
        'unknown_warehouses':parsed['unknown_warehouses'], 'invalid_entries':parsed['invalid_entries'],
        'comparison_complete':not unavailable and not parsed['unknown_warehouses'] and not parsed['invalid_entries']}


def city_plans(offers, quantity):
    """Fill the lot in each city: cheapest available stock first, then higher prices."""
    groups = {}
    for offer in offers:
        groups.setdefault(offer['origin_city'], []).append(offer)
    plans = []
    required = decimal_value(quantity, minimum=Decimal('.000001'))
    for city, rows in groups.items():
        remaining, parts = required, []
        for row in sorted(rows, key=lambda row:(row['purchase_unit_gross'],row['warehouse'])):
            take = min(remaining, row['stock'])
            if take > 0:
                parts.append({**row, 'quantity':take,
                    'purchase_gross':(take*row['purchase_unit_gross']).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)})
                remaining -= take
            if remaining == 0:
                break
        if remaining > 0:
            continue
        total = sum(part['purchase_gross'] for part in parts)
        plans.append({'warehouse':' + '.join(part['warehouse'] for part in parts),
            'origin_city':city, 'stock':sum(row['stock'] for row in rows),
            'purchase_unit_gross':(total/required).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP),
            'purchase_total_gross':total, 'purchase_vat_included':True, 'allocations':parts,
            'multiple_pickups':len(parts)>1})
    return plans
