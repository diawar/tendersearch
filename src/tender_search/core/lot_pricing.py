"""Personal bid calculator. Only explicit Goszakup identity and verified single-position cards."""
import re
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_UP
from django import forms
from .models import AnnouncementLotCache, UserLotPrice, CollectionLot

RULE_URL = 'https://wiki.goszakup.gov.kz/pages/viewpage.action?pageId=1311324'
CONFIRMED_METHODS = {11, 12, 23, 35, 36, 41}
CENT = Decimal('0.01')


def budget_unit_price(lot):
    """Use only a verified single-position quantity already saved with this lot.

    No per-row requests or database lookups while rendering result tables.
    """
    detail=(lot.raw or {}).get('_pricing_detail')
    if not isinstance(detail,dict) or detail.get('id')!=lot.source_id or detail.get('is_combined') is not False:
        return None
    plans=detail.get('plan_items')
    if not isinstance(plans,list) or len(plans)!=1 or not isinstance(plans[0],dict):return None
    try:
        quantity=Decimal(str(plans[0].get('count')))
        amount=Decimal(str(lot.amount))
        if not quantity.is_finite() or not amount.is_finite() or not 0<quantity<=Decimal('1000000000000') or quantity.as_tuple().exponent < -6 or amount<0:return None
        with localcontext() as ctx:
            ctx.prec=50
            return (amount/quantity).quantize(CENT,rounding=ROUND_HALF_UP)
    except (InvalidOperation,TypeError,ValueError):
        return None


def pricing_detail_snapshot(detail):
    result = {key: detail.get(key) for key in ('id', 'system', 'is_combined')}
    plans = detail.get('plan_items')
    result['plan_items'] = [{key: plan.get(key) for key in ('id','count','measure','purchase_method','system')} for plan in plans if isinstance(plan, dict)] if isinstance(plans, list) else None
    return result


def verified_pricing(lot):
    if lot.announcement.source != 'zakup':
        return None
    raw = lot.raw or {}
    try:
        method = int(raw.get('purchase_method_id'))
    except (ValueError, TypeError):
        return None
    if method not in CONFIRMED_METHODS:
        return None
    detail = raw.get('_pricing_detail')
    if not isinstance(detail, dict) or detail.get('id') != lot.source_id:
        cached = AnnouncementLotCache.objects.filter(pk=lot.announcement.source_id).first()
        detail = next((item for item in (cached.rows if cached else []) if item.get('id') == lot.source_id), None)
    if not detail or detail.get('is_combined') is not False:
        return None
    plans = detail.get('plan_items')
    if not isinstance(plans, list) or len(plans) != 1 or not isinstance(plans[0], dict):
        return None
    identities = set()
    for data in (raw, lot.announcement.raw or {}, detail, plans[0]):
        system = data.get('system')
        for value in (data.get('system_id'), system.get('id') if isinstance(system, dict) else system):
            if value is not None and value != '':
                identities.add(str(value))
    if identities != {'1'}:
        return None
    # Do not reuse a rule if the verified plan contradicts the registry method.
    plan_method = plans[0].get('purchase_method')
    if isinstance(plan_method, dict) and plan_method.get('id') is not None and str(plan_method['id']) != str(method):
        return None
    quantity = None
    try:
        quantity = Decimal(str(plans[0].get('count')))
        if not quantity.is_finite() or not 0 < quantity <= Decimal('1000000000000') or quantity.as_tuple().exponent < -6:
            quantity = None
    except InvalidOperation:
        pass
    measure = plans[0].get('measure') or {}
    return {'quantity': quantity, 'quantity_text': format(quantity, 'f') if quantity is not None else '', 'measure': measure.get('name_ru') or measure.get('name') or '' if isinstance(measure, dict) else str(measure), 'source_url': RULE_URL}


class UnitGrossField(forms.DecimalField):
    def to_python(self, value):
        if isinstance(value, str):
            value = re.sub(r'[\s\u00a0\u202f]', '', value).replace(',', '.')
            if value and not re.fullmatch(r'\d{1,20}(?:\.\d{0,2})?', value):
                raise forms.ValidationError('Введите положительную цену, не более двух знаков после запятой.')
        return super().to_python(value)


class LotPriceForm(forms.Form):
    unit_gross = UnitGrossField(label='Наша цена за единицу с НДС, ₸', required=True,
        max_digits=22, decimal_places=2, min_value=Decimal('0.01'),
        widget=forms.TextInput(attrs={'inputmode':'decimal', 'autocomplete':'off', 'placeholder':'Например, 1160,00', 'maxlength':32}))
    # New prices always use 16%; POST data cannot change the rate.
    # Existing locked prices retain their recorded rate through form.initial.
    vat_rate = forms.DecimalField(initial=Decimal('16'), disabled=True, widget=forms.HiddenInput)


def calculate_price(gross, rate, quantity=None):
    with localcontext() as ctx:
        ctx.prec = 70
        money = lambda value: value.quantize(CENT, rounding=ROUND_HALF_UP)
        net = money(gross / (1 + rate / 100))
        result = {'unit_net':net, 'unit_vat':money(gross-net)}
        if quantity is not None:
            total_net = money(net * quantity)
            total_vat = money(total_net * rate / 100)
            total_gross = total_net + total_vat
            desired_total = money(gross * quantity)
            result.update(total_net=total_net, total_vat=total_vat, total_gross=total_gross,
                desired_total=desired_total, rounding_difference=money(total_gross-desired_total))
        return result


def price_entry_data(lot):
    if lot.announcement.source != 'zakup':
        return None
    detail = lot.raw.get('_pricing_detail')
    if not isinstance(detail, dict) or detail.get('id') != lot.source_id:
        cached = AnnouncementLotCache.objects.filter(pk=lot.announcement.source_id).first()
        detail = next((row for row in (cached.rows if cached else []) if row.get('id')==lot.source_id), None)
    if not detail or detail.get('is_combined') is not False:
        return None
    plans=detail.get('plan_items')
    if not isinstance(plans,list) or len(plans)!=1 or not isinstance(plans[0],dict):return None
    quantity=None
    try:
        value=Decimal(str(plans[0].get('count')))
        if value.is_finite() and 0<value<=Decimal('1000000000000') and value.as_tuple().exponent>=-6:quantity=value
    except InvalidOperation:pass
    return {'quantity':quantity,'quantity_text':format(quantity,'f') if quantity is not None else '',
            'measure':(plans[0].get('measure') or {}).get('name','') if isinstance(plans[0].get('measure'),dict) else '',
            'source_url':RULE_URL}


def pricing_context(owner, lot, form=None):
    entry=price_entry_data(lot)
    if entry is None:return {}
    saved=UserLotPrice.objects.filter(owner=owner,lot=lot).first()
    interested=CollectionLot.objects.filter(profile__owner=owner,lot=lot,stage='interesting').exists()
    if not interested and saved is None:return {}
    policy=verified_pricing(lot)
    entry['confirmed']=policy is not None
    if policy:entry.update(policy)
    locked=bool(saved and saved.locked_at)
    if form is None:
        form=LotPriceForm(initial={'unit_gross':format(saved.unit_gross,'.2f') if saved else '',
            'vat_rate':format(saved.vat_rate,'f').rstrip('0').rstrip('.') if saved else '16'})
    gross=saved.unit_gross if saved else None
    rate=saved.vat_rate if saved else Decimal('16')
    if form.is_bound:
        gross=form.cleaned_data['unit_gross'] if form.is_valid() else None
        if form.is_valid():rate=form.cleaned_data['vat_rate']
    result=calculate_price(gross,rate,entry['quantity']) if gross is not None else {}
    if gross is not None and entry['quantity'] is not None:
        with localcontext() as ctx:
            ctx.prec=70
            result['entered_total_gross']=(gross*entry['quantity']).quantize(CENT,rounding=ROUND_HALF_UP)
    if locked:
        for field in form.fields.values():field.disabled=True
    workflows=CollectionLot.objects.filter(profile__owner=owner,lot=lot)
    can_cancel=locked and workflows.filter(stage='applied').exists() and not workflows.filter(stage__in=['won','lost','contract','delivery','payment']).exists()
    if can_cancel:
        from .models import ApplicationPreparation
        can_cancel=not ApplicationPreparation.objects.filter(owner=owner,lot=lot,portal_state__in=('submitted','completed')).exists()
    return {'price_policy':entry,'price_form':form,'price_result':result,'price_locked':locked,'saved_price':saved,'price_can_cancel':can_cancel}
