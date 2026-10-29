"""Resumable shared index. No user query causes a fresh portal traversal."""
from datetime import datetime, timedelta
from decimal import Decimal
import uuid

from django.db import transaction
from django.utils import timezone

from tender_search.client import SourceError, utc_datetime
from tender_search.matching import match_lot
from .models import IndexedLot, ProcurementIndex, ReferenceEntry, WebSearch, SearchHit
from .procurement_groups import status_matches
from .search_service import announcement_details, extended_description, lot_identity, store_lot

ACTIVE_STATUSES = {6, 7, 9}
PAGE_SIZE = 50
PAGE_OVERLAP = 5


def incomplete_rows():
    return IndexedLot.objects.filter(current=True, deadline__gt=timezone.now(), row___details_pending=True)


def save_index_row(row, detail, cycle):
    enriched = dict(row, description_ru=extended_description(row, detail),
                    _extended_description=detail is not None, _details_pending=detail is None)
    IndexedLot.objects.update_or_create(source_id=row['id'], defaults={
        'row': enriched, 'deadline': utc_datetime(row['offer_end_date']),
        'status_id': row['status']['id'], 'method_id': row['purchase_method_id'],
        'indexed_at': timezone.now(), 'seen_cycle': cycle, 'current': True})


def retry_missing_details(api, state):
    """One deferred card per page; a broken card must not stop other lots."""
    record = incomplete_rows().filter(indexed_at__lt=timezone.now()-timedelta(minutes=15)).order_by('indexed_at').first()
    if record is None:
        return
    if not ProcurementIndex.objects.filter(pk=1, lease=state.lease, paused=False).exists():
        return
    try:
        detail = announcement_details(api, record.row['announcement_id'], {}, record.source_id).get(record.source_id)
    except SourceError:
        IndexedLot.objects.filter(pk=record.pk).update(indexed_at=timezone.now())
        return
    # Preserve the cycle marker: retrying a card does not confirm its presence
    # in the current active-list sweep.
    save_index_row(record.row, detail, record.seen_cycle)


def date_value(value):
    if not value:
        return None
    value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return timezone.make_aware(value) if timezone.is_naive(value) else value


def index_coverage():
    now = timezone.now()
    state = ProcurementIndex.objects.filter(pk=1).first()
    count = IndexedLot.objects.filter(current=True, deadline__gt=now).count()
    missing = incomplete_rows().count()
    fresh = bool(state and state.completed_at and state.completed_at >= now-timedelta(hours=6))
    return {'loaded': count, 'source_count': state.source_count if state else 0,
            'completed_at': state.completed_at.isoformat() if state and state.completed_at else None,
            'heartbeat_at': state.heartbeat_at.isoformat() if state and state.heartbeat_at else None,
            'building': bool(state and state.pending), 'missing_details': missing,
            'ready': fresh and not state.error and not state.paused and not missing if state else False,
            'paused': bool(state and state.paused), 'error': state.error if state else '',
            'available': bool(state)}


def claim_index():
    now = timezone.now()
    with transaction.atomic():
        state, _ = ProcurementIndex.objects.get_or_create(pk=1)
        state = ProcurementIndex.objects.select_for_update().get(pk=1)
        if state.paused or (state.lease_until and state.lease_until > now):
            return None
        if not state.pending:
            if state.next_run_at and state.next_run_at > now:
                return None
            state.cycle = uuid.uuid4().hex
            state.started_at = now
            state.pending = [{'lo': now.isoformat(), 'hi': None, 'offset': 0}]
            state.processed = 0
        state.lease = uuid.uuid4().hex
        state.lease_until = now+timedelta(minutes=30)
        state.heartbeat_at = now
        state.save()
        return state


def collect_index_page(api):
    """Commit cursor only after the whole page. A failed page is safe to replay."""
    state = claim_index()
    if state is None:
        return False
    try:
        window = state.pending[0]
        params = {'status_id__in': '6__7__9', 'offer_end_date__gte': window['lo'],
                  'limit': PAGE_SIZE, 'offset': window['offset'], 'ordering': '-publish_date'}
        if window['hi']:
            params['offer_end_date__lte'] = window['hi']
        page = api.search(**params)
        lo, hi = date_value(window['lo']), date_value(window['hi'])
        # Verify that the source actually applied the partition; ignored filters
        # must never turn into a false claim of complete coverage.
        for row in page.results:
            end = utc_datetime(row.get('offer_end_date'))
            if not end or end < lo or (hi and end > hi) or (row.get('status') or {}).get('id') not in ACTIVE_STATUSES:
                raise SourceError('Источник не подтвердил фильтры активной базы. Сбор приостановлен до повторной проверки.')
        if window['offset'] == 0 and not window['hi'] and window['lo'] == state.started_at.isoformat():
            state.source_count = page.count
        if page.count > 9000:
            end = hi or lo+timedelta(days=2)
            if end-lo < timedelta(seconds=1):
                raise SourceError('Слишком много лотов с одним сроком; требуется дополнительное разбиение индекса.')
            middle = (lo+(end-lo)/2).isoformat()
            state.pending[:1] = [{'lo': window['lo'], 'hi': middle, 'offset': 0},
                                 {'lo': middle, 'hi': window['hi'], 'offset': 0}]
        else:
            details_cache = {}
            for row in page.results:
                if not ProcurementIndex.objects.filter(pk=1, lease=state.lease, paused=False).exists():
                    return False
                detail = announcement_details(api, row['announcement_id'], details_cache, row['id'], max_age=timedelta(hours=24)).get(row['id'])
                save_index_row(row, detail, state.cycle)
                ProcurementIndex.objects.filter(pk=1, lease=state.lease).update(
                    heartbeat_at=timezone.now(), lease_until=timezone.now()+timedelta(minutes=30))
            retry_missing_details(api, state)
            state.processed += len(page.results)
            if page.next_url:
                if not page.results:
                    raise SourceError('Источник вернул пустую страницу с продолжением.')
                window['offset'] += max(1, len(page.results)-PAGE_OVERLAP)
                if window['offset']+PAGE_SIZE > 10000:
                    raise SourceError('Окно источника изменилось во время обхода; требуется повторное разбиение.')
            else:
                state.pending.pop(0)
            if not state.pending:
                # Retain rows for diagnostics, but do not advertise disappeared
                # or cancelled procurements as current after a full sweep.
                IndexedLot.objects.filter(current=True).exclude(seen_cycle=state.cycle).update(current=False)
                if not incomplete_rows().exists():
                    state.completed_at = timezone.now()
                state.next_run_at = timezone.now()+timedelta(minutes=15)
        ProcurementIndex.objects.filter(pk=1, lease=state.lease).update(
            pending=state.pending, processed=state.processed, source_count=state.source_count,
            completed_at=state.completed_at, next_run_at=state.next_run_at,
            error='', heartbeat_at=timezone.now())
        return True
    except Exception as exc:
        ProcurementIndex.objects.filter(pk=1, lease=state.lease).update(
            error=str(exc)[:300] if isinstance(exc, SourceError) else 'Ошибка обновления индекса. Повторим страницу.',
            heartbeat_at=timezone.now())
        return False
    finally:
        ProcurementIndex.objects.filter(pk=1, lease=state.lease).update(lease='', lease_until=None)


def local_matches(row, spec, now, address_prefixes):
    filters = spec.get('filters', {})
    if not status_matches(row, spec.get('status_selection', []), now):
        return False
    customer_bin = spec.get('customer_bin')
    if customer_bin and not any(str(c.get('bin') or c.get('iin_bin') or '') == customer_bin for c in row.get('customers') or []):
        return False
    for key in ('lot_number', 'announcement_number'):
        if filters.get(key) and str(filters[key]).strip().casefold() not in str(row.get(key) or '').casefold():
            return False
    addresses = filters.get('delivery_address_id__in')
    if addresses:
        ids = set(str(addresses).split('__'))
        if not any(str(a.get('id')) in ids or any(str(a.get('code') or '').startswith(prefix) for prefix in address_prefixes)
                   for a in row.get('delivery_addresses') or []):
            return False
    return True


def execute_local_search(job_id):
    if not WebSearch.objects.filter(pk=job_id, status='queued').update(status='running', updated_at=timezone.now()):
        return
    job = WebSearch.objects.get(pk=job_id)
    try:
        now, spec = timezone.now(), job.specification
        filters = spec.get('filters', {})
        coverage = index_coverage()
        rows = IndexedLot.objects.filter(current=True, deadline__gt=now)
        for key, field in [('status_id', 'status_id'), ('purchase_method_id', 'method_id')]:
            raw = filters.get(key+'__in', filters.get(key))
            if raw:
                rows = rows.filter(**{field+'__in': [int(value) for value in str(raw).split('__')]})
        if filters.get('offer_end_date__gte'):
            rows = rows.filter(deadline__gte=date_value(filters['offer_end_date__gte']))
        if filters.get('offer_end_date__lte'):
            rows = rows.filter(deadline__lte=date_value(filters['offer_end_date__lte']))
        ids = str(filters.get('delivery_address_id__in') or '').split('__')
        prefixes = [str(ref.raw.get('code')).rstrip('0') for ref in ReferenceEntry.objects.filter(kind__in=['katos','cities'],source_id__in=[int(i) for i in ids if i.isdigit()]) if ref.raw.get('code')]
        supported = bool(spec.get('status_selection')) and all(str(s).startswith(('open:', 'upcoming:')) for s in spec['status_selection'])
        coverage['scope_supported'] = supported
        coverage['scope_note'] = '' if supported else 'Индекс охватывает только опубликованные закупки с неистёкшим сроком подачи. Архив и другие статусы не загружены полностью.'
        job.source_counts = {'__index__': coverage, '__local_candidates__': rows.count()}
        accepted = set()
        for record in rows.order_by('source_id').iterator(chunk_size=200):
            if job.scanned % 100 == 0:
                if not WebSearch.objects.filter(pk=job.pk,status='running').exists():
                    return
                WebSearch.objects.filter(pk=job.pk,status='running').update(scanned=job.scanned,source_counts=job.source_counts,updated_at=timezone.now())
            job.scanned += 1
            row = record.row
            if not local_matches(row, spec, now, prefixes):
                continue
            amount = row.get('total_price')
            if filters.get('total_price__gte') is not None and (amount is None or Decimal(str(amount)) < Decimal(str(filters['total_price__gte']))):
                continue
            if filters.get('total_price__lte') is not None and (amount is None or Decimal(str(amount)) > Decimal(str(filters['total_price__lte']))):
                continue
            found = match_lot(row.get('name_ru') or row.get('name'), row.get('description_ru'), spec)
            identity = lot_identity(row)
            if found and identity not in accepted:
                accepted.add(identity)
                lot = store_lot(row)
                SearchHit.objects.get_or_create(search=job,lot=lot,defaults={'terms':found})
        job.complete = coverage['ready'] and supported
        job.status = 'done' if job.complete else 'partial'
    except Exception as exc:
        job.status = 'partial' if job.hits.exists() else 'error'
        job.complete = False
        job.error = str(exc)[:300] if isinstance(exc, SourceError) else 'Не удалось проверить локальную базу. Повторите поиск.'
    WebSearch.objects.filter(pk=job.pk,status='running').update(status=job.status,complete=job.complete,error=job.error,
        scanned=job.scanned,source_counts=job.source_counts,finished_at=timezone.now(),updated_at=timezone.now())
