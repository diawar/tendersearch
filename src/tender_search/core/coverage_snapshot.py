"""Administrative display only: never use this snapshot to decide search completeness."""
from datetime import timedelta
import json
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone
from .models import ProcurementCoverageSnapshot
from .index_service import date_value

MAX_AGE = timedelta(minutes=3)


def refresh_snapshot():
    from .index_service import index_coverage
    from .medical_index import coverage
    from .platform_status import platform_statuses
    measured_at = timezone.now()
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cursor.execute("SET LOCAL statement_timeout = '60s'")
        index = index_coverage()
        payload = dict(index_coverage=index, medical_coverage=coverage(), platform_statuses=platform_statuses(index))
        payload = json.loads(json.dumps(payload, cls=DjangoJSONEncoder))
        ProcurementCoverageSnapshot.objects.update_or_create(pk=1, defaults={
            'measured_at': measured_at, 'payload': payload})


def display_snapshot():
    """Exactly one small SELECT, including on cold start; no synchronous fallback."""
    row = ProcurementCoverageSnapshot.objects.filter(pk=1).values('payload', 'measured_at').first()
    if not row:
        return dict(index_coverage={}, medical_coverage={}, platform_statuses=[], coverage_pending=True)
    payload = row['payload']
    index = dict(payload.get('index_coverage', {}))
    now = timezone.now()
    age = now - row['measured_at']
    stale = age > MAX_AGE or age < timedelta(0)
    index['completed_date'] = date_value(index.get('completed_at'))
    if index.get('building') and index.get('elapsed_seconds') is not None and not stale:
        elapsed = index['elapsed_seconds'] + max(0, int(age.total_seconds()))
        index.update(elapsed_seconds=elapsed, elapsed_label=f'{elapsed//3600:02d}:{elapsed//60%60:02d}:{elapsed%60:02d}')
    index['counter_running'] = bool(index.get('building') and not stale)
    platforms = []
    for item in payload.get('platform_statuses', []):
        item = dict(item, completed_at=date_value(item.get('completed_at')))
        if stale and item.get('state') not in ('error', 'paused'):
            item.update(state='stale', label='Показатели устарели')
        platforms.append(item)
    return dict(index_coverage=index, medical_coverage=payload.get('medical_coverage', {}),
        platform_statuses=platforms, coverage_measured_at=row['measured_at'], coverage_stale=stale)
