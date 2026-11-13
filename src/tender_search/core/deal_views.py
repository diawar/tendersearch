"""Private, on-demand estimates. These endpoints never submit applications/orders."""
import json
import re
from decimal import Decimal

import httpx
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from . import jetlogistic
from .deal_economics import calculate_deal, decimal_value
from .lot_pricing import company_vat_rate, price_budget, calculate_price
from .models import Lot, UserLotWorkflow


@login_required
@require_http_methods(['GET', 'POST'])
def obligation_costs(request, lot_id):
    from .models import LotObligationCosts
    from .obligation_costs import normalize_entries, suggestions
    from .preparation import preparation_specification_context
    workflow = get_object_or_404(UserLotWorkflow.objects.select_related('lot__announcement'),
        owner=request.user, lot_id=lot_id, stage='preparing')
    try:
        if request.method == 'POST':
            if len(request.body) > 50000:
                raise ValueError('Слишком большой список обязательств.')
            data = json.loads(request.body)
            entries, total = normalize_entries(data.get('entries') if isinstance(data, dict) else None)
            LotObligationCosts.objects.update_or_create(owner=request.user, lot_id=lot_id, defaults={'entries': entries})
            response = JsonResponse({'entries': entries, 'total': str(total)})
        else:
            docs = preparation_specification_context(request.user, workflow.lot)['specification_documents']
            response = JsonResponse({'entries': suggestions(docs), 'has_documents': bool(docs)})
    except (ValueError, TypeError):
        response = JsonResponse({'error': 'Проверьте названия и стоимость обязательств: неотрицательная сумма, до двух знаков после запятой.'}, status=400)
    response['Cache-Control'] = 'no-store, private'
    return response


def delivery_locations(lot):
    locations = (lot.raw or {}).get('delivery_addresses') or []
    return list(dict.fromkeys(str(item.get('name_ru') or item.get('name') or '').strip()
                              for item in locations if isinstance(item, dict)))


def suggest_destination(locations, cities):
    # Only a single explicit city. A region/customer's address is not a destination.
    if len(locations) != 1:
        return ''
    segments = [re.sub(r'^(?:г\.?|город)\s+', '', piece.strip(), flags=re.I).casefold()
                for piece in locations[0].split(',')]
    matches = [city for city in cities if city.split('-(')[0].casefold() in segments]
    return matches[0] if len(matches) == 1 else ''


@login_required
@require_http_methods(['GET', 'POST'])
def estimate(request, lot_id):
    lot = get_object_or_404(Lot.objects.select_related('announcement'), pk=lot_id)
    workflow=get_object_or_404(UserLotWorkflow, owner=request.user, lot=lot)
    from . import deal_snapshot as snapshots
    from .models import LotDealEstimate
    saved=LotDealEstimate.objects.filter(owner=request.user,lot=lot).first()
    stage=workflow.stage
    try:
        if request.method == 'GET':
            if request.GET.get('product_url'):
                from .catalog_sku import sku_index
                from .overshop import public_url
                url = public_url(request.GET['product_url'])
                matches = [sku for sku, product_url in sku_index().items() if url and product_url == url]
                if len(matches) != 1:
                    raise ValueError('Не удалось однозначно определить SKU. Укажите его вручную.')
                response = JsonResponse({'sku':matches[0]})
                response['Cache-Control'] = 'no-store, private'
                return response
            city_list = jetlogistic.cities()
            locations = delivery_locations(lot)
            response = JsonResponse({'cities': city_list, 'cargo_types': jetlogistic.CARGO_TYPES,
                                     'locations': locations,
                                     'destination': suggest_destination(locations, city_list)})
        else:
            if len(request.body) > 50000:
                raise ValueError('Слишком большой запрос.')
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError('Ожидается объект с параметрами расчёта.')
            if data.get('action')=='select_offer':
                if stage in snapshots.COMMERCIAL_LOCKED:
                    raise ValueError('Товар и закупка зафиксированы с этапа «Заключение договора».')
                if not saved or data.get('version')!=saved.updated_at.isoformat():
                    raise ValueError('Расчёт изменён. Обновите страницу.')
                index=data.get('index')
                if type(index) is not int or not 0<=index<len(saved.snapshot.get('comparison',{}).get('offers',[])):
                    raise ValueError('Предложение не найдено в сохранённом расчёте.')
                saved=snapshots.persist(request.user,lot,stage,saved.inputs,saved.snapshot,index,data['version'])
                response=JsonResponse({'saved':snapshots.context(request.user,lot)['saved_deal']})
                response['Cache-Control']='no-store, private'
                return response
            original=dict(data)
            fixed=snapshots.chosen(saved)
            if stage in snapshots.COMMERCIAL_LOCKED:
                if not saved or not fixed:
                    raise ValueError('Нет сохранённого расчёта закупки. Товар и цены уже зафиксированы этапом.')
                previous=snapshots.effective_inputs(saved)
                for key in snapshots.COMMERCIAL:
                    if key in data and str(data[key])!=str(previous.get(key,'')):
                        raise ValueError('Товар, цена закупки и цена продажи зафиксированы с этапа «Заключение договора».')
                data={**previous,**{k:v for k,v in data.items() if k not in snapshots.COMMERCIAL}}
            if stage in snapshots.DELIVERY_LOCKED:
                previous=snapshots.effective_inputs(saved) if saved else {}
                for key in snapshots.DELIVERY:
                    if key in original and str(original[key])!=str(previous.get(key,'')):
                        raise ValueError('Доставка зафиксирована с этапа «Оплата».')
            if stage not in snapshots.DELIVERY_LOCKED:
                data['delivery_option']='door_to_door'
            for key in ('product_name','manufacturer','country','sku'):
                if key in data and (not isinstance(data[key],str) or len(data[key])>500):
                    raise ValueError('Проверьте описание товара.')
            budget = saved.snapshot.get('budget') if stage in snapshots.COMMERCIAL_LOCKED else price_budget(lot)
            if not budget:
                raise ValueError('Количество и бюджет лота пока не подтверждены. Обновите карточку лота.')
            budget={**budget,**{key:decimal_value(budget[key]) for key in ('unit','total','quantity')}}
            rate = decimal_value(saved.snapshot['vat_rate']) if stage in snapshots.COMMERCIAL_LOCKED else company_vat_rate(request.user)
            unit = decimal_value(data.get('unit_gross'), minimum=Decimal('.01'))
            sale = calculate_price(unit, rate, budget['quantity'])
            if sale['unit_net'] > decimal_value(budget['unit']) or sale['total_net'] > decimal_value(budget['total']):
                raise ValueError('Наша цена без НДС превышает бюджет лота.')
            from .obligation_costs import normalize_entries, context
            from .preparation import preparation_specification_context
            if 'obligations' in data:
                entries = data['obligations']
            else:
                documents = preparation_specification_context(request.user, lot)['specification_documents']
                entries = context(request.user, lot, documents)['obligation_entries']
            obligations, obligations_total = normalize_entries(entries, require_amounts=True)
            data['obligations']=obligations
            other_costs = decimal_value(data.get('other_costs', 0))
            def calculate(purchase, delivery, freight, purchase_total=None):
                result = calculate_deal(unit_gross=unit, quantity=budget['quantity'], sale_vat_rate=rate,
                    purchase_unit_gross=purchase, purchase_total_gross=purchase_total, delivery_gross=delivery,
                    purchase_vat_rate=data.get('purchase_vat_rate', 0),
                    purchase_vat_credit=data.get('purchase_vat_credit') is True,
                    delivery_vat_rate=data.get('delivery_vat_rate', 0) if not freight else 0,
                    delivery_vat_credit=data.get('delivery_vat_credit') is True and not freight,
                    other_costs=other_costs+obligations_total,
                    **{key:data.get(key,0) for key in ('refundable_security','freeze_days','annual_money_rate')},
                    tax_mode=data.get('tax_mode','unknown'),income_tax_rate=data.get('income_tax_rate'))
                result.update(additional_obligations=obligations, obligations_total=obligations_total,
                              other_costs_excluding_obligations=other_costs)
                return result
            if data.get('procurement_mode') == 'p1':
                from .p1_deal import compare
                comparison = compare(data, budget['quantity'], calculate)
                payload={'comparison':comparison,'quantity':str(budget['quantity']),'vat_rate':str(rate),'budget':budget}
                if comparison['offers']:
                    selected=0
                    if comparison.get('minimum_purchase') and comparison['minimum_purchase']['purchase_total_gross']<comparison['offers'][0]['purchase_total_gross']:
                        selected=None
                    snapshots.persist(request.user,lot,stage,data,payload,selected,original.get('estimate_version',saved.updated_at.isoformat() if saved else ''))
                    payload['saved']=snapshots.context(request.user,lot)['saved_deal']
                response=JsonResponse(payload)
                response['Cache-Control'] = 'no-store, private'
                return response
            freight = None
            if stage in snapshots.DELIVERY_LOCKED:
                delivery_gross=decimal_value(fixed['delivery_gross']);freight=fixed.get('freight')
            elif data.get('delivery_mode') == 'jet':
                freight = jetlogistic.quote(**{key: data.get(key) for key in
                    ('origin', 'destination', 'weight_kg', 'volume_m3', 'packages', 'cargo_type')})
                option = data.get('delivery_option')
                if option not in freight['options']:
                    raise ValueError('Выбранный вариант доставки недоступен для маршрута.')
                delivery_gross = freight['options'][option]
            elif data.get('delivery_mode') == 'manual':
                delivery_gross = decimal_value(data.get('delivery_gross'))
            else:
                raise ValueError('Выберите способ расчёта доставки.')
            purchase_total=fixed['result']['purchase_gross'] if stage in snapshots.COMMERCIAL_LOCKED else None
            result = calculate(data.get('purchase_unit_gross'), delivery_gross, freight,purchase_total)
            payload={'result':result,'freight':freight,'quantity':str(budget['quantity']),
                     'delivery_gross':str(delivery_gross),'vat_rate':str(rate),'budget':budget}
            if saved and stage in snapshots.COMMERCIAL_LOCKED:
                payload['procurement_plan']=saved.snapshot.get('procurement_plan') or ({k:fixed[k] for k in ('origin_city','warehouse','allocations','multiple_pickups') if k in fixed})
                payload['catalog_checked_at']=saved.snapshot.get('catalog_checked_at') or saved.snapshot.get('comparison',{}).get('catalog_checked_at')
            snapshots.persist(request.user,lot,stage,data,payload,version=original.get('estimate_version',saved.updated_at.isoformat() if saved else ''))
            payload['saved']=snapshots.context(request.user,lot)['saved_deal']
            response=JsonResponse(payload)
    except (ValueError, TypeError, KeyError, ArithmeticError) as error:
        # Validation errors are ours; never expose provider HTML or request data.
        message = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else 'Проверьте параметры расчёта.'
        response = JsonResponse({'error': message[:250]}, status=400)
    except httpx.HTTPError:
        response = JsonResponse({'error': 'Jet Logistic временно недоступен. Повторите расчёт позже или укажите подтверждённую доставку вручную.'}, status=502)
    response['Cache-Control'] = 'no-store, private'
    return response
