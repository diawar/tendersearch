"""Resumable lead collection, entirely outside interactive search workers."""
from datetime import timedelta
import uuid

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from tender_search.client import SourceError, utc_datetime
from .lead_source import LeadPortal, ProtocolFormatError
from .models import LeadCollectionState, LeadCompany, LeadEmail, LeadParticipation, LeadProcurement


def set_collection_enabled(enabled):
    state, _ = LeadCollectionState.objects.get_or_create(pk=1)
    LeadCollectionState.objects.filter(pk=1).update(enabled=enabled)
    return state


def _message(exc):
    return str(exc)[:300] if isinstance(exc, SourceError) else 'Ошибка фонового сбора. Подробности требуют проверки.'


def claim_collection():
    now = timezone.now()
    with transaction.atomic():
        state, _ = LeadCollectionState.objects.get_or_create(pk=1)
        state = LeadCollectionState.objects.select_for_update().get(pk=state.pk)
        if not state.enabled or (state.lease_until and state.lease_until > now):
            return None
        state.lease = uuid.uuid4().hex
        state.lease_until = now+timedelta(minutes=10)
        state.heartbeat_at = now
        state.save(update_fields=['lease', 'lease_until', 'heartbeat_at'])
    # A killed collector can leave a source in processing; no result is lost.
    LeadProcurement.objects.filter(state='processing', updated_at__lt=now-timedelta(minutes=10), attempts__gte=3).update(
        state='review', error='Разбор протокола несколько раз прерван. Требуется ручная проверка.')
    LeadProcurement.objects.filter(state='processing', updated_at__lt=now-timedelta(minutes=10)).update(
        state='retry', next_attempt_at=now, error='Предыдущий сбор прерван. Повторяем протокол.')
    return state


def discover_page(api, state):
    now = timezone.now()
    if not state.windows:
        if state.next_scan_at and state.next_scan_at > now:
            return False
        days = max(1, min(state.lookback_days, 365))
        state.windows = [{'lo': (now-timedelta(days=i+1)).isoformat(),
                          'hi': (now-timedelta(days=i)).isoformat(), 'offset': 0} for i in range(days)]
        state.started_at = now
    window = state.windows[0]
    lo, hi = utc_datetime(window['lo']), utc_datetime(window['hi'])
    page = api.search('announcements', system_id__in='1__4', results_publication_date__gte=window['lo'],
                      results_publication_date__lte=window['hi'], ordering='-publish_date',
                      limit=100, offset=window['offset'])
    for row in page.results:
        published = utc_datetime(row.get('results_publication_date'))
        if (not published or not lo <= published <= hi or row.get('system_id') not in {1, 4}
                or not isinstance(row.get('id'), int) or not isinstance(row.get('external_id'), int)):
            raise SourceError('Источник не подтвердил фильтр итогов по дате и площадке. Курсор сохранён.')
    if page.count > 9000:
        if hi-lo <= timedelta(seconds=1):
            raise SourceError('Слишком много итогов с одной датой; требуется проверка окна источника.')
        middle = lo+(hi-lo)/2
        state.windows[:1] = [dict(lo=middle.isoformat(), hi=window['hi'], offset=0),
                             dict(lo=window['lo'], hi=middle.isoformat(), offset=0)]
    else:
        with transaction.atomic():
            for row in page.results:
                published = utc_datetime(row['results_publication_date'])
                item, created = LeadProcurement.objects.get_or_create(source_id=row['id'], defaults={
                    'external_id': row['external_id'], 'number': row.get('announcement_number') or str(row['external_id']),
                    'name': row.get('name') or row.get('name_ru') or '',
                    'method_name': row.get('purchase_method_name') or '', 'results_published_at': published})
                if not created and item.results_published_at != published:
                    LeadProcurement.objects.filter(pk=item.pk).update(results_published_at=published,
                        name=row.get('name') or row.get('name_ru') or '', state='queued',
                        next_attempt_at=now, error='', updated_at=now)
            if page.next_url:
                if not page.results:
                    raise SourceError('Пустая страница итогов с продолжением')
                window['offset'] += max(1, len(page.results)-5)
                if window['offset']+100 > 10000:
                    raise SourceError('Выдача итогов изменилась во время обхода; требуется повтор окна.')
            else:
                state.windows.pop(0)
    if not state.windows:
        state.completed_at = now
        state.next_scan_at = now+timedelta(hours=6)
    LeadCollectionState.objects.filter(pk=1, lease=state.lease).update(windows=state.windows,
        started_at=state.started_at, completed_at=state.completed_at, next_scan_at=state.next_scan_at,
        error='', heartbeat_at=timezone.now())
    return True


def collect_protocol(portal):
    now = timezone.now()
    item = LeadProcurement.objects.filter(state__in=['queued', 'retry'], next_attempt_at__lte=now).order_by(
        '-results_published_at', 'pk').first()
    if item is None:
        return False
    LeadProcurement.objects.filter(pk=item.pk).update(state='processing', attempts=F('attempts')+1, updated_at=now)
    try:
        participants, unresolved, protocol_url = portal.protocol(item.external_id)
        with transaction.atomic():
            for person in participants:
                company, created = LeadCompany.objects.get_or_create(identifier=person.identifier,
                    defaults={'name': person.name, 'last_seen_at': now})
                LeadCompany.objects.filter(pk=company.pk).update(last_seen_at=now)
                LeadParticipation.objects.update_or_create(company=company, procurement=item, defaults={
                    'supplier_name': person.name, 'lot_numbers': sorted(person.lots),
                    'winner': person.winner, 'protocol_url': protocol_url})
            LeadProcurement.objects.filter(pk=item.pk).update(state='review' if unresolved else 'done',
                protocol_url=protocol_url, participants_count=len(participants), completed_at=timezone.now(),
                error=f'Не распознано строк участников: {unresolved}. Требуется проверка.' if unresolved else '',
                updated_at=timezone.now())
    except Exception as exc:
        review = isinstance(exc, ProtocolFormatError)
        delay = timedelta(minutes=min(1440, 15*2**min(item.attempts, 6)))
        LeadProcurement.objects.filter(pk=item.pk).update(state='review' if review else 'retry',
            error=_message(exc), next_attempt_at=now+delay, updated_at=timezone.now())
    return True


def collect_contact(portal):
    now = timezone.now()
    company = LeadCompany.objects.filter(next_contact_check_at__lte=now).exclude(status='excluded').order_by(
        'next_contact_check_at', 'pk').first()
    if company is None:
        return False
    try:
        contact = portal.contacts(company.identifier)
        with transaction.atomic():
            for email in contact['emails']:
                LeadEmail.objects.update_or_create(company=company, email=email, defaults={
                    'source_url': contact['source_url'], 'verified_at': now, 'current': True})
            LeadEmail.objects.filter(company=company).exclude(email__in=contact['emails']).update(current=False)
            LeadCompany.objects.filter(pk=company.pk).update(
                name=contact['name'] or company.name, identifier_kind=contact['kind'],
                registry_id=contact['registry_id'], registry_url=contact['source_url'],
                contact_state='ready' if contact['emails'] else 'missing', contact_checked_at=now,
                next_contact_check_at=now+timedelta(days=30 if contact['emails'] else 7),
                contact_attempts=0, contact_error='', updated_at=now)
    except Exception as exc:
        delay = timedelta(minutes=min(1440, 5*2**min(company.contact_attempts, 8)))
        LeadCompany.objects.filter(pk=company.pk).update(contact_state='error', contact_attempts=F('contact_attempts')+1,
            contact_error=_message(exc), next_contact_check_at=now+delay, updated_at=now)
    return True


def collect_lead_round(api):
    state = claim_collection()
    if state is None:
        return False
    changed = False
    try:
        # Keep discovery bounded; don't enqueue a month of documents before
        # checking the first contacts. A failed protocol never blocks others.
        if LeadProcurement.objects.filter(state__in=['queued', 'processing']).count() < 100:
            try:
                changed = discover_page(api, state) or changed
            except Exception as exc:
                LeadCollectionState.objects.filter(pk=1, lease=state.lease).update(error=_message(exc))
        portal = LeadPortal(api)
        if LeadCollectionState.objects.filter(pk=1, enabled=True, lease=state.lease).exists():
            changed = collect_protocol(portal) or changed
        if LeadCollectionState.objects.filter(pk=1, enabled=True, lease=state.lease).exists():
            changed = collect_contact(portal) or changed
        return changed
    finally:
        LeadCollectionState.objects.filter(pk=1, lease=state.lease).update(
            lease='', lease_until=None, heartbeat_at=timezone.now())
