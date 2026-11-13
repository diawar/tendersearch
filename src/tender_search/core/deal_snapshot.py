"""Owner-scoped financial snapshots; no catalogue or carrier calls on read."""
import json
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from .models import LotDealEstimate, UserLotWorkflow, LotObligationCosts

COMMERCIAL_LOCKED={'contract','delivery','payment','won','lost','archived'}
DELIVERY_LOCKED={'payment','won','lost','archived'}
COMMERCIAL={'product_name','manufacturer','country','sku','procurement_mode','purchase_unit_gross',
            'unit_gross','purchase_vat_rate','purchase_vat_credit'}
DELIVERY={'delivery_mode','delivery_gross','delivery_vat_rate','delivery_vat_credit','origin',
          'destination','weight_kg','volume_m3','packages','cargo_type','delivery_option','local_delivery_gross'}
FIELDS=COMMERCIAL|DELIVERY|{'other_costs','refundable_security','freeze_days','annual_money_rate',
                           'tax_mode','income_tax_rate','obligations'}


def plain(value):
    return json.loads(json.dumps(value,cls=DjangoJSONEncoder))


def chosen(saved):
    if not saved:return None
    if 'result' in saved.snapshot:return saved.snapshot
    offers=saved.snapshot.get('comparison',{}).get('offers',[])
    return offers[saved.selected_offer] if saved.selected_offer is not None and saved.selected_offer<len(offers) else None


def effective_inputs(saved):
    values=dict(saved.inputs)
    offer=chosen(saved)
    if saved.snapshot.get('comparison') and offer:
        values.update(procurement_mode='manual',purchase_unit_gross=offer['purchase_unit_gross'],
                      origin=(offer.get('freight') or {}).get('origin') or offer['origin_city'],
                      product_name=saved.snapshot['comparison']['product']['name'])
    return values


def context(owner,lot):
    saved=LotDealEstimate.objects.filter(owner=owner,lot=lot).first()
    if not saved:return {'saved_deal':None,'saved_deal_summary':None}
    stage=UserLotWorkflow.objects.filter(owner=owner,lot=lot).values_list('stage',flat=True).first()
    item=chosen(saved)
    return {'saved_deal':{'inputs':effective_inputs(saved),'snapshot':saved.snapshot,
        'selected_offer':saved.selected_offer,'version':saved.updated_at.isoformat(),
        'commercial_locked':stage in COMMERCIAL_LOCKED,'delivery_locked':stage in DELIVERY_LOCKED},
        'saved_deal_summary':{'updated_at':saved.updated_at,'product_name':effective_inputs(saved).get('product_name',''),
            'result':item.get('result') if item else None,'delivery_gross':item.get('delivery_gross') if item else None}}


def persist(owner,lot,stage,inputs,snapshot,selected=None,version=None):
    with transaction.atomic():
        workflow=UserLotWorkflow.objects.select_for_update().filter(owner=owner,lot=lot).first()
        if not workflow or workflow.stage!=stage:raise ValueError('Этап изменился. Обновите страницу перед сохранением расчёта.')
        old=LotDealEstimate.objects.select_for_update().filter(owner=owner,lot=lot).first()
        if version is not None and (old.updated_at.isoformat() if old else '')!=version:
            raise ValueError('Расчёт изменён в другой вкладке. Обновите страницу.')
        saved,_=LotDealEstimate.objects.update_or_create(owner=owner,lot=lot,
            defaults={'inputs':plain({k:v for k,v in inputs.items() if k in FIELDS}),
                      'snapshot':plain(snapshot),'selected_offer':selected})
        if 'obligations' in inputs:
            LotObligationCosts.objects.update_or_create(owner=owner,lot=lot,defaults={'entries':plain(inputs['obligations'])})
        return saved
