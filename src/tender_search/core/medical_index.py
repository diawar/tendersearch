"""Resumable, independently paced medical index. Cursor commits after each card."""
from datetime import timedelta
import logging
import uuid
import zlib

from django.db import transaction
from django.utils import timezone

from tender_search.client import SourceError, utc_datetime
from tender_search.medical import ACTIVE, HOSTS
from .models import MedicalIndex, MedicalIndexedLot, ReferenceEntry

logger = logging.getLogger(__name__)


def method_id(name):
    # Match names, never assume that the portals' numeric codes are equivalent.
    ref = ReferenceEntry.objects.filter(kind='purchase-methods', name=name).order_by('source_id').first()
    if ref:
        return ref.source_id
    identifier = 1000000 + zlib.crc32(name.encode('utf-8')) % 1000000000
    ref, _ = ReferenceEntry.objects.get_or_create(kind='purchase-methods', source_id=identifier,
        defaults={'name': name, 'raw': {'source': 'medical', 'name': name}})
    if ref.name != name:
        raise SourceError('Конфликт справочника медицинских способов закупки')
    return identifier


def coverage():
    now = timezone.now()
    states = list(MedicalIndex.objects.all())
    ready = len(states) == len(HOSTS) and all(s.completed_at and s.completed_at >= now-timedelta(hours=6) and not s.error for s in states)
    return {'loaded': MedicalIndexedLot.objects.filter(current=True, deadline__gt=now).count(),
            'ready': bool(ready), 'available': bool(states),
            'building': any(s.cursor for s in states),
            'error': '; '.join(f'{HOSTS[s.source]}: {s.error}' for s in states if s.error),
            'completed_at': min(s.completed_at for s in states).isoformat() if len(states)==len(HOSTS) and all(s.completed_at for s in states) else None,
            'source_count': 0, 'missing_details': 0, 'paused': False}


def collect_one(source, api):
    now = timezone.now()
    with transaction.atomic():
        MedicalIndex.objects.get_or_create(source=source)
        state = MedicalIndex.objects.select_for_update().get(source=source)
        if (state.lease_until and state.lease_until > now) or (state.next_run_at and state.next_run_at > now):
            return False
        if not state.cursor:
            state.cycle = uuid.uuid4().hex
            state.cursor = {'status': 0, 'page': 1}
        state.lease = uuid.uuid4().hex
        state.lease_until = now+timedelta(minutes=30)
        state.heartbeat_at = now
        state.save()
    try:
        def heartbeat():
            if not MedicalIndex.objects.filter(source=source, lease=state.lease).update(
                    heartbeat_at=timezone.now(), lease_until=timezone.now()+timedelta(minutes=30)):
                raise SourceError('Обход продолжен другим процессом')
        api.heartbeat = heartbeat
        cursor = state.cursor
        if 'ids' not in cursor:
            rows, next_page = api.listing(cursor['page'], ACTIVE[cursor['status']])
            cursor['ids'] = [r['id'] for r in rows if r['end'] and utc_datetime(r['end']) > now]
            cursor['next'] = next_page
            # Persist the pending IDs before fetching cards. Restart resumes the same page.
            MedicalIndex.objects.filter(source=source, lease=state.lease).update(cursor=cursor)
        if cursor['ids']:
            aid = cursor['ids'][0]
            card = api.announcement(aid)
            deadline = utc_datetime(card['offer_end_date'])
            if deadline and deadline > now and card['status']['id'] in (6, 7):
                rows = api.announcement_lots(aid)
                with transaction.atomic():
                    for row in rows:
                        if row['status']['id'] not in (6, 7):
                            continue
                        mid = method_id(row['purchase_method']['name'])
                        row['purchase_method']['id'] = mid
                        MedicalIndexedLot.objects.update_or_create(source=source, source_id=row['id'], defaults={
                            'row': row, 'deadline': deadline, 'status_id': row['status']['id'], 'method_id': mid,
                            'indexed_at': timezone.now(), 'seen_cycle': state.cycle, 'current': True})
            cursor['ids'].pop(0)
        if not cursor.get('ids'):
            if cursor['next']:
                cursor = {'status': cursor['status'], 'page': cursor['next']}
            elif cursor['status']+1 < len(ACTIVE):
                cursor = {'status': cursor['status']+1, 'page': 1}
            else:
                cursor = {}
                MedicalIndexedLot.objects.filter(source=source, current=True).exclude(seen_cycle=state.cycle).update(current=False)
                state.completed_at = timezone.now()
                state.next_run_at = timezone.now()+timedelta(minutes=15)
        MedicalIndex.objects.filter(source=source, lease=state.lease).update(cursor=cursor,
            completed_at=state.completed_at, next_run_at=state.next_run_at, error='', heartbeat_at=timezone.now())
        return True
    except Exception as exc:
        logger.exception('Medical index failed: %s', source)
        MedicalIndex.objects.filter(source=source, lease=state.lease).update(
            error=str(exc)[:300] if isinstance(exc, SourceError) else 'Не удалось обновить медицинскую базу; повторим объявление.',
            next_run_at=timezone.now()+timedelta(minutes=5), heartbeat_at=timezone.now())
        return False
    finally:
        api.heartbeat = None
        MedicalIndex.objects.filter(source=source, lease=state.lease).update(lease='', lease_until=None)
