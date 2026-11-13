"""Render synthetic browser fixtures from current templates; no DB or API access.

Run with: python manage.py shell -c "exec(open('tests/render_browser_fixtures.py').read())"
"""
from pathlib import Path
from types import SimpleNamespace as NS
from decimal import Decimal
from django.template.loader import render_to_string
from django.test import override_settings
from tender_search.core.preparation import PreparationForm
from tender_search.core.lot_pricing import LotPriceForm
from tender_search.core.deal_economics import calculate_deal

destination=Path('tests/fixtures')
destination.mkdir(exist_ok=True)
lot=NS(pk=1,name='Тестовый монитор',number='10000000-ЗЦП1',announcement=NS(number='10000000-1',source='zakup'))
context=dict(lot=lot,user=NS(pk=1,username='test',is_authenticated=True),account_scope='fixture-scope',
    preparation=None,editable=True,company_bin='000000000000',preparation_workflow_stage='preparing',
    form=PreparationForm(),price_form=LotPriceForm(),csrf_token='test-csrf',
    price_policy={'quantity':1,'quantity_text':'1','measure':'шт.'},
    price_budget={'unit':Decimal('100'),'total':Decimal('100'),'quantity':Decimal('1')},price_has_vat=True,
    specification_documents=[],specification_auto_load=False,obligation_entries=[{'label':'Тестовый расход','amount':'15'}])
with override_settings(STORAGES={'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}}):
    html=render_to_string('core/application_preparation.html',context)
    (destination/'preparation.html').write_text(html,encoding='utf-8')
    inputs=dict(product_name='Private monitor',unit_gross='116',procurement_mode='manual',purchase_unit_gross='20',
        delivery_mode='manual',delivery_gross='0',purchase_vat_rate='0',purchase_vat_credit=False,
        delivery_vat_rate='0',delivery_vat_credit=False,other_costs='0',tax_mode='unknown')
    result=calculate_deal(unit_gross=Decimal('116'),quantity=Decimal('1'),sale_vat_rate=Decimal('16'),
        purchase_unit_gross=Decimal('20'),delivery_gross=Decimal('0'))
    context.update(form=PreparationForm(initial={'product_name':'Private monitor'}),saved_deal={'inputs':inputs,'snapshot':{'result':result,'delivery_gross':'0'},
        'commercial_locked':True,'delivery_locked':False,'version':'test-version'},obligation_entries=[])
    (destination/'deal-snapshot.html').write_text(render_to_string('core/application_preparation.html',context),encoding='utf-8')
print('Synthetic browser fixtures rendered from current templates.')
