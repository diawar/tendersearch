"""Resumable shared index. No user query causes a fresh portal traversal."""
from datetime import datetime, timedelta
from decimal import Decimal
import uuid
import hashlib
import json

from django.db import transaction
from django.utils import timezone

from tender_search.client import NotFound, SourceError, utc_datetime
from tender_search.matching import match_lot
from .models import IndexedLot, ProcurementIndex, ReferenceEntry, WebSearch, SearchHit, AnnouncementLotCache
from .announcement_index import announcement_lot_row
from .procurement_groups import status_matches
from .advances import has_advance
from .search_service import announcement_details, extended_description, lot_identity, store_lot

ACTIVE_STATUSES = {6, 7, 9}
PAGE_SIZE = 100
PAGE_OVERLAP = 5
DETAIL_MAX_AGE = timedelta(hours=6)


def registry_fingerprint(row):
    """Hash source fields, not the enriched local representation."""
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def incomplete_rows():
    return IndexedLot.objects.filter(current=True, deadline__gt=timezone.now(), row___details_pending=True)


def save_index_row(row, detail, cycle, fingerprint=None):
    enriched = dict(row, description_ru=extended_description(row, detail),
                    _extended_description=detail is not None, _details_pending=False,
                    _has_advance=has_advance(detail))
    defaults = {
        'announcement_source_id': row['announcement_id'],
        'row': enriched, 'deadline': utc_datetime(row['offer_end_date']),
        'status_id': row['status']['id'], 'method_id': row.get('purchase_method_id'),
        'indexed_at': timezone.now(), 'seen_cycle': cycle,
        'current': detail is not None, 'details_skipped': detail is None}
    if fingerprint is not None:
        defaults['registry_fingerprint'] = fingerprint
    IndexedLot.objects.update_or_create(source_id=row['id'], defaults=defaults)
    if detail is None:
        # Remove stale search results, retaining favorites and their documents.
        SearchHit.objects.filter(lot__announcement__source='zakup', lot__source_id=row['id']).delete()


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
    elapsed = None
    if state and state.started_at:
        end = now if state.pending else state.completed_at
        if end and end >= state.started_at:
            elapsed = int((end-state.started_at).total_seconds())
    return {'loaded': count, 'source_count': state.source_count if state else 0,
            'elapsed_seconds': elapsed,
            'elapsed_label': f'{elapsed//3600:02d}:{elapsed//60%60:02d}:{elapsed%60:02d}' if elapsed is not None else '',
            'skipped': IndexedLot.objects.filter(details_skipped=True, deadline__gt=now).count(),
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
            state.pending = [{'resource': resource, 'lo': now.isoformat(), 'hi': None, 'offset': 0}
                             for resource in ('lots', 'announcements')]
            state.processed = 0
        elif any('resource' not in window for window in state.pending):
            # Upgrade an interrupted old collector without losing its cursor.
            for window in state.pending:
                window.setdefault('resource', 'lots')
            state.pending.append({'resource': 'announcements', 'lo': state.started_at.isoformat(),
                                  'hi': None, 'offset': 0})
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
        resource = window['resource']
        params = {'status_id__in': '6__7__9', 'offer_end_date__gte': window['lo'],
                  'limit': PAGE_SIZE, 'offset': window['offset'], 'ordering': '-publish_date'}
        if window['hi']:
            params['offer_end_date__lte'] = window['hi']
        page = api.search(**params) if resource == 'lots' else api.search('announcements', **params)
        lo, hi = date_value(window['lo']), date_value(window['hi'])
        # Verify that the source actually applied the partition; ignored filters
        # must never turn into a false claim of complete coverage.
        for row in page.results:
            end = utc_datetime(row.get('offer_end_date'))
            status = row.get('status_id') if resource == 'announcements' else (row.get('status') or {}).get('id')
            if not end or end < lo or (hi and end > hi) or status not in ACTIVE_STATUSES:
                raise SourceError('Источник не подтвердил фильтры активной базы. Сбор приостановлен до повторной проверки.')
        if resource == 'lots' and window['offset'] == 0 and not window['hi'] and window['lo'] == state.started_at.isoformat():
            state.source_count = page.count
        if page.count > 9000:
            end = hi or lo+timedelta(days=2)
            if end-lo < timedelta(seconds=1):
                raise SourceError('Слишком много лотов с одним сроком; требуется дополнительное разбиение индекса.')
            middle = (lo+(end-lo)/2).isoformat()
            state.pending[:1] = [{'resource': resource, 'lo': window['lo'], 'hi': middle, 'offset': 0},
                                 {'resource': resource, 'lo': middle, 'hi': window['hi'], 'offset': 0}]
        else:
            details_cache = {}
            unavailable_announcements = set()
            refreshed_announcements = set()
            old_fingerprints = dict(IndexedLot.objects.filter(
                source_id__in=[row['id'] for row in page.results]
            ).values_list('source_id', 'registry_fingerprint')) if resource == 'lots' else {}
            cycle_cards = set(AnnouncementLotCache.objects.filter(
                source_id__in=[row['announcement_id'] for row in page.results],
                checked_at__gte=max(state.started_at, timezone.now()-DETAIL_MAX_AGE)
            ).values_list('source_id', flat=True)) if resource == 'lots' else set()
            skipped_ids = set(IndexedLot.objects.filter(
                details_skipped=True, source_id__in=[row['id'] for row in page.results]
            ).values_list('source_id', flat=True))
            for row in page.results:
                if not ProcurementIndex.objects.filter(pk=1, lease=state.lease, paused=False).exists():
                    return False
                if resource == 'announcements':
                    collect_announcement(api, row, state)
                    ProcurementIndex.objects.filter(pk=1, lease=state.lease).update(
                        heartbeat_at=timezone.now(), lease_until=timezone.now()+timedelta(minutes=30))
                    continue
                if row['id'] in skipped_ids:
                    continue
                detail = None
                fingerprint = registry_fingerprint(row)
                if row['announcement_id'] not in unavailable_announcements:
                    try:
                        changed = old_fingerprints.get(row['id']) != fingerprint
                        force = (changed and row['announcement_id'] not in refreshed_announcements
                                 and (old_fingerprints.get(row['id']) or row['announcement_id'] not in cycle_cards))
                        if force:
                            details_cache.pop(row['announcement_id'], None)
                        detail = announcement_details(api, row['announcement_id'], details_cache, row['id'],
                            max_age=timedelta(0) if force else DETAIL_MAX_AGE).get(row['id'])
                        if force:
                            refreshed_announcements.add(row['announcement_id'])
                    except NotFound:
                        # Permanently skip only missing cards, not source outages.
                        unavailable_announcements.add(row['announcement_id'])
                save_index_row(row, detail, state.cycle, fingerprint)
                ProcurementIndex.objects.filter(pk=1, lease=state.lease).update(
                    heartbeat_at=timezone.now(), lease_until=timezone.now()+timedelta(minutes=30))
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


def collect_announcement(api, announcement, state):
    """Read every lot, including those omitted by the separate lot registry.

    Unchanged registry entries reuse recent cards across cycles. A bounded TTL
    also checks changes to specifications that are not exposed in the registry.
    """
    fingerprint = registry_fingerprint(announcement)
    cached = AnnouncementLotCache.objects.filter(source_id=announcement['id']).first()
    reusable = bool(cached and cached.checked_at >= timezone.now()-DETAIL_MAX_AGE and (
        cached.registry_fingerprint == fingerprint or (
            not cached.registry_fingerprint and cached.checked_at >= state.started_at)))
    if reusable:
        details = cached.rows
        if cached.registry_fingerprint != fingerprint:
            AnnouncementLotCache.objects.filter(pk=cached.pk).update(registry_fingerprint=fingerprint)
    else:
        try:
            details = api.announcement_lots(announcement['id'])
        except NotFound:
            # Disappeared announcement cards do not stop the remaining registry.
            IndexedLot.objects.filter(announcement_source_id=announcement['id']).update(current=False)
            return
        AnnouncementLotCache.objects.update_or_create(source_id=announcement['id'], defaults={
            'rows': details, 'checked_at': timezone.now(), 'registry_fingerprint': fingerprint})
    # A card can lose individual lots while its announcement remains active.
    IndexedLot.objects.filter(announcement_source_id=announcement['id']).exclude(
        source_id__in=[d['id'] for d in details]).update(current=False)
    skipped = set(IndexedLot.objects.filter(details_skipped=True,
        source_id__in=[d['id'] for d in details]).values_list('source_id', flat=True))
    for detail in details:
        if not ProcurementIndex.objects.filter(pk=1, lease=state.lease, paused=False).exists():
            raise SourceError('Обход объявлений приостановлен; страница будет повторена.')
        if detail['id'] in skipped:
            continue
        row = announcement_lot_row(announcement, detail)
        if (detail.get('is_externally_deleted') is True or row['status'].get('id') not in ACTIVE_STATUSES
                or utc_datetime(row['offer_end_date']) <= timezone.now()):
            IndexedLot.objects.filter(source_id=detail['id']).update(current=False, seen_cycle=state.cycle)
            continue
        save_index_row(row, detail, state.cycle)


def local_matches(row, spec, now, address_prefixes):
    from .subjects import subject_matches
    filters = spec.get('filters', {})
    if not subject_matches(row, filters.get('subject')):
        return False
    if filters.get('only_advance') and row.get('_has_advance') is not True:
        return False
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
        from itertools import chain
        from .models import MedicalIndexedLot
        from .medical_index import coverage as medical_coverage
        medical = medical_coverage()
        coverage = medical.copy() if filters.get('only_medical') else index_coverage()
        if not filters.get('only_medical'):
            coverage['ready'] = coverage['ready'] and medical['ready']
        coverage['medical'] = medical
        sources = [MedicalIndexedLot.objects.filter(current=True, deadline__gt=now)]
        if not filters.get('only_medical'):
            sources.append(IndexedLot.objects.filter(current=True, details_skipped=False, deadline__gt=now))
        for key, field in [('status_id', 'status_id'), ('purchase_method_id', 'method_id')]:
            raw = filters.get(key+'__in', filters.get(key))
            if raw:
                sources = [rows.filter(**{field+'__in': [int(value) for value in str(raw).split('__')]}) for rows in sources]
        if filters.get('offer_end_date__gte'):
            sources = [rows.filter(deadline__gte=date_value(filters['offer_end_date__gte'])) for rows in sources]
        if filters.get('offer_end_date__lte'):
            sources = [rows.filter(deadline__lte=date_value(filters['offer_end_date__lte'])) for rows in sources]
        ids = str(filters.get('delivery_address_id__in') or '').split('__')
        prefixes = [str(ref.raw.get('code')).rstrip('0') for ref in ReferenceEntry.objects.filter(kind__in=['katos','cities'],source_id__in=[int(i) for i in ids if i.isdigit()]) if ref.raw.get('code')]
        supported = bool(spec.get('status_selection')) and all(str(s).startswith(('open:', 'upcoming:')) for s in spec['status_selection'])
        coverage['scope_supported'] = supported
        coverage['scope_note'] = '' if supported else 'Индекс охватывает только опубликованные закупки с неистёкшим сроком подачи. Архив и другие статусы не загружены полностью.'
        if filters.get('delivery_address_id__in'):
            supported = False
            coverage['scope_supported'] = False
            coverage['scope_note'] += ' Для MED/FMS адреса поставки доступны текстом; без подтверждённого кода КАТО лот не проходит фильтр области/города. Для полного поиска по медицинским площадкам снимите этот фильтр.'
        job.source_counts = {'__index__': coverage, '__local_candidates__': sum(rows.count() for rows in sources)}
        accepted = set()
        for record in chain.from_iterable(rows.order_by('source_id').iterator(chunk_size=200) for rows in sources):
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
