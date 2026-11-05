"""Fast active registry sweeps: list HTTP only; detail work is durable and separate."""
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from tender_search.client import SourceError, utc_datetime
from .models import IndexedLot, ProcurementIndex, AnnouncementRefresh, AnnouncementLotCache
from .detail_index import observe


def store_registry_lot(row, cycle):
    from .index_service import registry_fingerprint
    fingerprint = registry_fingerprint(row)
    lot = IndexedLot.objects.filter(pk=row['id']).first()
    changed = lot is None or lot.registry_fingerprint != fingerprint
    observe(row['announcement_id'], utc_datetime(row['offer_end_date']), cycle, changed=changed)
    if not changed:
        # Do not replace enrichment or revive a card-confirmed cancellation.
        IndexedLot.objects.filter(pk=row['id']).update(seen_cycle=cycle)
        return
    IndexedLot.objects.update_or_create(pk=row['id'], defaults={
        'announcement_source_id': row['announcement_id'], 'registry_fingerprint': fingerprint,
        'row': dict(row, _details_pending=True, _extended_description=False),
        'deadline': utc_datetime(row['offer_end_date']), 'status_id': row['status']['id'],
        'method_id': row.get('purchase_method_id'), 'indexed_at': timezone.now(),
        'seen_cycle': cycle, 'current': utc_datetime(row['offer_end_date']) > timezone.now(),
        'details_skipped': False})


def store_registry_announcement(row, cycle):
    from .index_service import registry_fingerprint
    old = AnnouncementRefresh.objects.filter(pk=row['id']).first()
    prior = old or AnnouncementLotCache.objects.filter(pk=row['id']).first()
    changed = prior is None or prior.registry_fingerprint != registry_fingerprint(row)
    observe(row['id'], utc_datetime(row['offer_end_date']), cycle, announcement=row)
    if not changed:
        return
    # Dates/method are shared by the announcement. Its status is NOT the lot
    # status; membership and per-lot status are confirmed by the detail worker.
    for lot in IndexedLot.objects.filter(announcement_source_id=row['id'], current=True):
        data = dict(lot.row, _details_pending=True)
        for key in ('offer_start_date', 'offer_end_date', 'purchase_method_id', 'purchase_method_name'):
            if key in row:
                data[key] = row[key]
        data['announcement_name_ru'] = row.get('name_ru') or row.get('name')
        data['announcement_publish_date'] = row.get('publish_date')
        lot.row = data
        lot.deadline = utc_datetime(row['offer_end_date'])
        lot.method_id = data.get('purchase_method_id')
        lot.save(update_fields=['row', 'deadline', 'method_id'])


def collect_registry_page(api):
    from .index_service import claim_index, date_value, ACTIVE_STATUSES, PAGE_SIZE, PAGE_OVERLAP
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
        for row in page.results:
            end = utc_datetime(row.get('offer_end_date'))
            status = row.get('status_id') if resource == 'announcements' else (row.get('status') or {}).get('id')
            if not end or end < lo or (hi and end > hi) or status not in ACTIVE_STATUSES:
                raise SourceError('Источник не подтвердил фильтры активной базы. Повторим страницу.')
        with transaction.atomic():
            locked = ProcurementIndex.objects.select_for_update().get(pk=1)
            if locked.paused or locked.lease != state.lease:
                return False
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
                for row in page.results:
                    if resource == 'lots':
                        store_registry_lot(row, state.cycle)
                    else:
                        store_registry_announcement(row, state.cycle)
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
                    # Union of BOTH registries; announcement-only lots survive
                    # until their card confirms removal. Cancel stale queue work.
                    AnnouncementRefresh.objects.filter(active=True).exclude(seen_cycle=state.cycle).update(
                        active=False, revision=F('revision')+1)
                    active_announcements = AnnouncementRefresh.objects.filter(active=True).values('source_id')
                    IndexedLot.objects.filter(current=True).exclude(seen_cycle=state.cycle).exclude(
                        announcement_source_id__in=active_announcements).update(current=False)
                    IndexedLot.objects.filter(current=True, deadline__lte=timezone.now()).update(current=False)
                    state.completed_at = timezone.now()
                    state.next_run_at = timezone.now()+timedelta(minutes=15)
            ProcurementIndex.objects.filter(pk=1, lease=state.lease).update(
                pending=state.pending, processed=state.processed, source_count=state.source_count,
                completed_at=state.completed_at, next_run_at=state.next_run_at, error='', heartbeat_at=timezone.now())
        return True
    except Exception as exc:
        ProcurementIndex.objects.filter(pk=1, lease=state.lease).update(
            error=str(exc)[:300] if isinstance(exc, SourceError) else 'Ошибка обновления реестров. Повторим страницу.',
            heartbeat_at=timezone.now())
        return False
    finally:
        ProcurementIndex.objects.filter(pk=1, lease=state.lease).update(lease='', lease_until=None)
