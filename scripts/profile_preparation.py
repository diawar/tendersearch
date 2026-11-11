"""Read-only preparation/queue timings; never create portal drafts or expire jobs."""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_search.web.settings')
import django
django.setup()
import argparse, json, statistics
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import RequestFactory, override_settings
from tender_search.core import preparation, draft_queue
from tender_search.core.models import DraftQueueItem

parser=argparse.ArgumentParser()
parser.add_argument('--output', required=True)
parser.add_argument('--save', action='store_true', help='Time a local save inside a rolled-back transaction')
args=parser.parse_args()
user=get_user_model().objects.get(username='admin')
items=list(DraftQueueItem.objects.filter(owner=user).order_by('-pk')[:3])
cases=[('queue','/workspace/drafts/',draft_queue.queue,{})]
cases += [(f'preparation{i.lot_id}',f'/lots/{i.lot_id}/preparation/',preparation.prepare_application,{'lot_id':i.lot_id}) for i in items]
cases += [(f'packet{i.pk}',f'/workspace/drafts/{i.pk}/packet/',draft_queue.packet,{'item_id':i.pk}) for i in items]
factory=RequestFactory();results={}
with override_settings(ALLOWED_HOSTS=['testserver']), patch.object(draft_queue,'expire_draft_queue'):
    with transaction.atomic():
        with connection.cursor() as cursor:cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        for name,path,view,kwargs in cases:
            runs=[]
            for _ in range(5):
                queries=[]
                def measure(execute,sql,params,many,context):
                    started=perf_counter()
                    try:return execute(sql,params,many,context)
                    finally:queries.append({'sql':sql,'seconds':perf_counter()-started})
                request=(factory.post(path) if name.startswith('packet') else factory.get(path))
                request.user=user;request.session={}
                started=perf_counter()
                with connection.execute_wrapper(measure):response=view(request,**kwargs)
                runs.append({'seconds':perf_counter()-started,'queries':len(queries),'status':response.status_code,'sql_seconds':sum(q['seconds'] for q in queries),'sql':queries})
            results[name]={'median':statistics.median(r['seconds'] for r in runs[1:]),'runs':runs}
            print(name,round(results[name]['median'],4),[(r['queries'],r['status']) for r in runs],flush=True)
if args.save:
    from django.contrib.messages.storage.fallback import FallbackStorage
    from tender_search.core.models import ApplicationPreparation, UserLotWorkflow
    from tender_search.core.lot_pricing import price_budget, company_vat_rate
    from decimal import Decimal
    candidate=ApplicationPreparation.objects.filter(owner=user,lot_id__in=UserLotWorkflow.objects.filter(owner=user,stage='preparing').values('lot_id')).exclude(lot_id__in=DraftQueueItem.objects.filter(owner=user,state__in=('running','waiting')).values('lot_id')).select_related('lot__announcement').first()
    if candidate:
        budget=price_budget(candidate.lot)
        data=dict(candidate.data)
        if budget:
            data.update(unit_gross=str((budget['unit']*(1+company_vat_rate(user)/100)).quantize(Decimal('.01'))),confirm_discount='on')
        runs=[]
        for _ in range(5):
            request=factory.post(f'/lots/{candidate.lot_id}/preparation/',data)
            request.user=user;request.session={};request._messages=FallbackStorage(request)
            queries=[]
            def count(execute,sql,params,many,context):
                queries.append(sql)
                return execute(sql,params,many,context)
            started=perf_counter()
            with override_settings(ALLOWED_HOSTS=['testserver']), transaction.atomic(), connection.execute_wrapper(count):
                response=preparation.prepare_application(request,candidate.lot_id)
                transaction.set_rollback(True)
            runs.append({'seconds':perf_counter()-started,'queries':len(queries),'status':response.status_code})
        results['save_rollback']={'median':statistics.median(r['seconds'] for r in runs[1:]),'runs':runs}
        print('save_rollback',round(results['save_rollback']['median'],4),[(r['queries'],r['status']) for r in runs],flush=True)
Path(args.output).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
