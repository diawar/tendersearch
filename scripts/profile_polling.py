"""Measure pending-result rendering using existing data in a read-only snapshot."""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','tender_search.web.settings')
import django
django.setup()
import argparse, json, statistics
from datetime import timedelta
from time import perf_counter
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import RequestFactory, override_settings
from django.utils import timezone
from bs4 import BeautifulSoup
from tender_search.core import views
from tender_search.core.models import WebSearch

parser=argparse.ArgumentParser()
parser.add_argument('--output',required=True)
args=parser.parse_args()
user=get_user_model().objects.get(username='admin')
identifier=WebSearch.objects.filter(owner=user).order_by('-created_at').values_list('pk',flat=True).first()
if not identifier:raise SystemExit('No existing search for measurement')
original_get=views.get_object_or_404
def queued(*a,**kw):
    obj=original_get(*a,**kw)
    if isinstance(obj,WebSearch):
        # Simulate waiting in memory, without changing the real search.
        obj.status='queued';obj.created_at=timezone.now()-timedelta(seconds=60);obj.updated_at=timezone.now()
    return obj
runs=[]
with transaction.atomic(),override_settings(ALLOWED_HOSTS=['testserver','localhost']),patch.object(views,'get_object_or_404',side_effect=queued):
    with connection.cursor() as cursor:cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    for _ in range(6):
        count=[0]
        def execute(fn,sql,params,many,context):
            count[0]+=1;return fn(sql,params,many,context)
        request=RequestFactory().get(f'/search/{identifier}/',HTTP_HX_REQUEST='true')
        request.user=user;request.session={}
        start=perf_counter()
        with connection.execute_wrapper(execute):response=views.search_results(request,identifier)
        elapsed=perf_counter()-start
        root=BeautifulSoup(response.content,'html.parser').select_one('#search-results')
        runs.append({'seconds':round(elapsed,5),'queries':count[0],'bytes':len(response.content),'trigger':root.get('hx-trigger')})
    transaction.set_rollback(True)
result={'median':statistics.median(r['seconds'] for r in runs[1:]),'runs':runs}
Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf8')
print(json.dumps(result),flush=True)
