"""Read-only page timings against real local data; never dispatch source refreshes."""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_search.web.settings')
import django
django.setup()
import argparse, json, statistics, cProfile, pstats
from time import perf_counter
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import RequestFactory, override_settings
from django.db.models import TextField
from django.db.models.functions import Length, Cast
from tender_search.core import views, collection_views
from tender_search.core.models import Favorite, LotSpecification
from tender_search.core.lot_state import visible_collection_lots

parser=argparse.ArgumentParser()
parser.add_argument('--output', required=True)
parser.add_argument('--cpu', action='store_true')
parser.add_argument('--runs', type=int, default=4)
args=parser.parse_args()
user=get_user_model().objects.get(username='admin')
factory=RequestFactory()
ids=list(Favorite.objects.filter(owner=user).values_list('lot_id',flat=True)[:3])
largest=LotSpecification.objects.filter(owner=user).annotate(size=Length(Cast('documents',TextField()))).order_by('-size').values_list('lot_id',flat=True).first()
if largest and largest not in ids:ids.append(largest)
cases=[('workspace100','/workspace/?profile=all&stage=all&per_page=100',collection_views.collections,{})]
control_ids=list(visible_collection_lots(user).filter(stage='new').order_by('lot_id').values_list('lot_id',flat=True).distinct()[:100])
cases.append(('controls100','/lots/ui-state/?ids='+','.join(map(str,control_ids)),views.lot_ui_state,{}))
cases += [(f'lot{pk}',f'/lots/{pk}/',views.lot_detail,{'lot_id':pk}) for pk in ids]
results={}
with override_settings(ALLOWED_HOSTS=['testserver','localhost']), patch.object(views,'queue_refresh'), patch.object(views,'expire_lot_jobs'):
    with transaction.atomic():
        with connection.cursor() as cur: cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        for label,path,view,kw in cases:
            runs=[]
            for _ in range(args.runs):
                queries=[]
                def measure(execute,sql,params,many,context):
                    t=perf_counter()
                    try:return execute(sql,params,many,context)
                    finally:queries.append({'seconds':round(perf_counter()-t,6),'sql':sql})
                request=factory.get(path);request.user=user;request.session={}
                start=perf_counter()
                profiler=cProfile.Profile()
                if args.cpu:profiler.enable()
                with connection.execute_wrapper(measure): response=view(request,**kw)
                if args.cpu:
                    profiler.disable()
                    if _==args.runs-1:pstats.Stats(profiler).strip_dirs().sort_stats('cumtime').print_stats(35)
                grouped={}
                for q in queries:
                    key=q['sql']
                    item=grouped.setdefault(key,{'count':0,'seconds':0})
                    item['count']+=1;item['seconds']+=q['seconds']
                runs.append({'seconds':round(perf_counter()-start,4),'queries':len(queries),'sql_seconds':round(sum(q['seconds'] for q in queries),4),'bytes':len(response.content),'rows':response.content.count(b'<tr>')-1,
                    'slow_queries':sorted([{'sql':k,**v} for k,v in grouped.items()],key=lambda x:x['seconds'],reverse=True)[:8]})
            results[label]={'path':path,'median':statistics.median(r['seconds'] for r in runs[1:]),'runs':runs}
            print(label,results[label]['median'],[(r['queries'],r['sql_seconds'],r['rows']) for r in runs],flush=True)
        transaction.set_rollback(True)
Path(args.output).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
