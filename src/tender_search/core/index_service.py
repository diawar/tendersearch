"""Resumable shared index. No user query causes a fresh portal traversal."""
from datetime import datetime, timedelta
from decimal import Decimal
import uuid
import hashlib
import json

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from tender_search.client import SourceError, utc_datetime
from tender_search.matching import match_lot, PreparedMatcher
from .models import IndexedLot, ProcurementIndex, ReferenceEntry, WebSearch, SearchHit, AnnouncementRefresh
from .procurement_groups import status_matches
from .advances import has_advance
from .search_service import extended_description, lot_identity, store_lot, store_search_hits

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


def index_coverage(*, diagnostics=True):
    now = timezone.now()
    state = ProcurementIndex.objects.filter(pk=1).first()
    count = IndexedLot.objects.filter(current=True, deadline__gt=now).count() if diagnostics else None
    missing = incomplete_rows().count() if diagnostics else incomplete_rows().exists()
    detail_tasks = AnnouncementRefresh.objects.filter(active=True, deadline__gt=now)
    detail_counts = detail_tasks.aggregate(urgent=Count('pk', filter=Q(urgent=True)),
        due=Count('pk', filter=Q(due_at__lte=now)), errors=Count('pk', filter=~Q(error=''))) if diagnostics else {}
    urgent = detail_counts['urgent'] if diagnostics else detail_tasks.filter(urgent=True).exists()
    fresh = bool(state and state.completed_at and state.completed_at >= now-timedelta(hours=6))
    elapsed = None
    if state and state.started_at:
        end = now if state.pending else state.completed_at
        if end and end >= state.started_at:
            elapsed = int((end-state.started_at).total_seconds())
    return {'loaded': count, 'source_count': state.source_count if state else 0,
            'elapsed_seconds': elapsed,
            'elapsed_label': f'{elapsed//3600:02d}:{elapsed//60%60:02d}:{elapsed%60:02d}' if elapsed is not None else '',
            'skipped': IndexedLot.objects.filter(details_skipped=True, deadline__gt=now).count() if diagnostics else None,
            'completed_at': state.completed_at.isoformat() if state and state.completed_at else None,
            'heartbeat_at': state.heartbeat_at.isoformat() if state and state.heartbeat_at else None,
            'building': bool(state and state.pending), 'missing_details': missing if diagnostics else None,
            'has_missing_details': bool(missing), 'has_urgent_details': bool(urgent),
            'detail_urgent': urgent if diagnostics else None, 'detail_due': detail_counts.get('due'),
            'detail_errors': detail_counts.get('errors'), 'diagnostics_measured': diagnostics,
            'ready': fresh and not state.error and not state.paused and not missing and not urgent if state else False,
            'paused': bool(state and state.paused), 'error': state.error if state else '',
            'available': bool(state)}


def claim_index():
    now = timezone.now()
    with transaction.atomic():
        state, _ = ProcurementIndex.objects.get_or_create(pk=1)
        state = ProcurementIndex.objects.select_for_update().get(pk=1)
        if state.paused or (state.lease_until and state.lease_until > now):
            return None
        if state.pipeline_version < 2:
            # The old cursor skipped detail work implicitly: start a full fast sweep.
            state.pending = []
            state.next_run_at = None
            state.pipeline_version = 2
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
    from .registry_index import collect_registry_page
    return collect_registry_page(api)


def local_matches(row, spec, now, address_prefixes):
    from .subjects import subject_matches
    filters = spec.get('filters', {})
    if str(filters.get('remaining_hours')) in {'24', '48'}:
        deadline = utc_datetime(row.get('offer_end_date'))
        if not deadline or not now < deadline <= now+timedelta(hours=int(filters['remaining_hours'])):
            return False
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
    pending_hits = []
    try:
        now, spec = timezone.now(), job.specification
        filters = spec.get('filters', {})
        from itertools import chain
        from .models import MedicalIndexedLot
        from .medical_index import coverage as medical_coverage
        medical = medical_coverage(diagnostics=False)
        coverage = medical.copy() if filters.get('only_medical') else index_coverage(diagnostics=False)
        if not filters.get('only_medical'):
            coverage['ready'] = coverage['ready'] and medical['ready']
        coverage['medical'] = medical
        from .query_search import search_sources
        sources, prefixes, query_info = search_sources(spec, now, job.owner_id)
        supported = bool(spec.get('status_selection')) and all(str(s).startswith(('open:', 'upcoming:')) for s in spec['status_selection'])
        coverage['scope_supported'] = supported
        coverage['scope_note'] = '' if supported else 'Индекс охватывает только опубликованные закупки с неистёкшим сроком подачи. Архив и другие статусы не загружены полностью.'
        if filters.get('delivery_address_id__in'):
            supported = False
            coverage['scope_supported'] = False
            coverage['scope_note'] += ' Для MED/FMS адреса поставки доступны текстом; без подтверждённого кода КАТО лот не проходит фильтр области/города. Для полного поиска по медицинским площадкам снимите этот фильтр.'
        # The stream is consumed completely. A separate COUNT would run the
        # expensive keyword candidate query twice solely for diagnostics.
        job.source_counts = {'__index__': coverage, '__local_candidates__': None, '__query__': query_info}
        accepted = set()
        matcher = PreparedMatcher(spec)
        document_matches = {}
        if filters.get("in_documents"):
            from .technical_documents import matching_lots
            document_matches = matching_lots(matcher, now=now,
                is_active=lambda: WebSearch.objects.filter(pk=job.pk, status='running').exists())
            if document_matches is None:
                return
            from .query_search import document_sources
            sources = document_sources(sources, spec, prefixes, document_matches)
            query_info['document_prefilter'] = bool(settings.SEARCH_DB_PREFILTERS)
            query_info['enabled'] = bool(settings.SEARCH_DB_PREFILTERS and connection.vendor == 'postgresql')
            coverage["documents"] = "Поиск по извлечённому тексту техспецификаций; сканы и недоступные файлы не охвачены."
            supported = False
        for row in chain.from_iterable(rows.order_by('source_id').values_list('row', flat=True).iterator(chunk_size=200) for rows in sources):
            if job.scanned % 100 == 0:
                # The guarded update both checks cancellation and publishes
                # progress, avoiding a separate SELECT for every 100 rows.
                updated = WebSearch.objects.filter(pk=job.pk,status='running').update(
                    scanned=job.scanned,source_counts=job.source_counts,updated_at=timezone.now())
                if not updated:
                    store_search_hits(job, pending_hits)
                    return
            job.scanned += 1
            if not local_matches(row, spec, now, prefixes):
                continue
            amount = row.get('total_price')
            if filters.get('total_price__gte') is not None and (amount is None or Decimal(str(amount)) < Decimal(str(filters['total_price__gte']))):
                continue
            if filters.get('total_price__lte') is not None and (amount is None or Decimal(str(amount)) > Decimal(str(filters['total_price__lte']))):
                continue
            found = match_lot(row.get('name_ru') or row.get('name'), row.get('description_ru'), matcher)
            from tender_search.matching import normalize
            excluded_in_metadata = any(term in normalize(f"{row.get('name_ru') or row.get('name') or ''} {row.get('description_ru') or ''}") for term in matcher.excluded)
            if not found and document_matches and not excluded_in_metadata:
                key = (row.get('_source', 'zakup'), row.get('announcement_id'), row.get('id'))
                found = document_matches.get(key) or document_matches.get((*key[:2], None))
            identity = lot_identity(row)
            if found and identity not in accepted:
                accepted.add(identity)
                pending_hits.append((row, found))
                if len(pending_hits) >= 50:
                    batch, pending_hits = pending_hits, []
                    store_search_hits(job, batch)
        job.source_counts['__local_candidates__'] = job.scanned
        batch, pending_hits = pending_hits, []
        store_search_hits(job, batch)
        job.complete = coverage['ready'] and supported
        job.status = 'done' if job.complete else 'partial'
    except Exception as exc:
        if pending_hits:
            try:
                store_search_hits(job, pending_hits)
            except Exception:
                pass  # The original failure remains authoritative.
        job.status = 'partial' if job.hits.exists() else 'error'
        job.complete = False
        job.error = str(exc)[:300] if isinstance(exc, SourceError) else 'Не удалось проверить локальную базу. Повторите поиск.'
    WebSearch.objects.filter(pk=job.pk,status='running').update(status=job.status,complete=job.complete,error=job.error,
        scanned=job.scanned,source_counts=job.source_counts,finished_at=timezone.now(),updated_at=timezone.now())
