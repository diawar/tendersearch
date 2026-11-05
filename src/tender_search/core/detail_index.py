"""Persistent detail work, independent of the fast registry cursor.

All writers lock ProcurementIndex then AnnouncementRefresh. HTTP runs outside
transactions; a revision/lease fence rejects responses superseded meanwhile.
"""
from datetime import timedelta
import uuid

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tender_search.client import NotFound, SourceError, SchemaError, utc_datetime
from .models import AnnouncementRefresh, AnnouncementLotCache, IndexedLot, ProcurementIndex, SearchHit
from .announcement_index import announcement_lot_row

DETAIL_MAX_AGE = timedelta(hours=6)


def observe(identifier, deadline, cycle, *, changed=False, announcement=None):
    """Called inside the registry page transaction, with the state lock held."""
    from .index_service import registry_fingerprint
    now = timezone.now()
    task, created = AnnouncementRefresh.objects.get_or_create(source_id=identifier,
        defaults={'deadline': deadline, 'seen_cycle': cycle})
    if created:
        cache = AnnouncementLotCache.objects.filter(pk=identifier).first()
        if cache:
            # Bootstrap from the existing collector without turning every old
            # card into urgent work ahead of genuinely new procurements.
            task.registry_fingerprint = cache.registry_fingerprint
            task.checked_at = cache.checked_at
            task.due_at = cache.checked_at + DETAIL_MAX_AGE
            task.urgent = False
    fingerprint = registry_fingerprint(announcement) if announcement is not None else None
    changed = changed or not task.active or (fingerprint is not None and fingerprint != task.registry_fingerprint)
    if not created and changed:
        task.revision += 1
    if changed:
        task.urgent = True
        task.due_at = now
        task.attempts = 0
        task.error = ''
    if announcement is not None:
        task.announcement = announcement
        task.registry_fingerprint = fingerprint
    task.active = True
    task.deadline = deadline
    task.seen_cycle = cycle
    task.save()
    return task


def claim_detail():
    now = timezone.now()
    with transaction.atomic():
        state = ProcurementIndex.objects.select_for_update().filter(pk=1).first()
        if not state or state.paused:
            return None
        task = (AnnouncementRefresh.objects.select_for_update().filter(
            active=True, deadline__gt=now, due_at__lte=now)
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
            .order_by('-urgent', 'due_at', 'source_id').first())
        if task is None:
            return None
        task.lease = uuid.uuid4().hex
        task.lease_until = now + timedelta(minutes=5)
        task.save(update_fields=['lease', 'lease_until'])
        return task


def refresh_details(api):
    """Process one announcement; failure never rewinds the registry cursor."""
    from .index_service import ACTIVE_STATUSES, save_index_row
    task = claim_detail()
    if task is None:
        return False
    unavailable = False
    try:
        try:
            announcement = task.announcement or api.announcement(task.source_id)
            if not isinstance(announcement, dict) or announcement.get('id') != task.source_id:
                raise SchemaError('Карточка не соответствует объявлению.')
            details = api.announcement_lots(task.source_id)
            # Validate the entire response before changing any membership.
            rows = [announcement_lot_row(announcement, detail) for detail in details]
            if any(not utc_datetime(row.get('offer_end_date')) for row in rows):
                raise SchemaError('В карточке отсутствует срок подачи.')
        except NotFound:
            unavailable, details, rows = True, [], []
        with transaction.atomic():
            state = ProcurementIndex.objects.select_for_update().get(pk=1)
            current = AnnouncementRefresh.objects.select_for_update().get(pk=task.pk)
            if (state.paused or not current.active or current.revision != task.revision
                    or current.lease != task.lease):
                return False
            now = timezone.now()
            existing = IndexedLot.objects.filter(announcement_source_id=task.source_id)
            absent = existing.exclude(source_id__in=[r['id'] for r in rows])
            absent_ids = list(absent.values_list('source_id', flat=True))
            absent.update(current=False, details_skipped=True)
            SearchHit.objects.filter(lot__announcement__source='zakup', lot__source_id__in=absent_ids).delete()
            for row, detail in zip(rows, details):
                if (detail.get('is_externally_deleted') is True or row['status'].get('id') not in ACTIVE_STATUSES
                        or utc_datetime(row['offer_end_date']) <= now):
                    IndexedLot.objects.filter(pk=row['id']).update(current=False, seen_cycle=current.seen_cycle)
                    continue
                # A fresh card is authoritative. Preserve the raw registry hash
                # separately, so enrichment never causes a spurious change.
                save_index_row(row, detail, current.seen_cycle)
            if not unavailable:
                AnnouncementLotCache.objects.update_or_create(pk=task.pk, defaults={
                    'rows': details, 'checked_at': now, 'registry_fingerprint': current.registry_fingerprint})
            current.checked_at = now
            current.due_at = now + (timedelta(days=1) if unavailable else DETAIL_MAX_AGE)
            current.urgent = False
            current.attempts = 0
            current.error = ''
            current.save()
        return True
    except Exception as exc:
        # Bound retries, without treating 403/429/5xx/schema errors as missing.
        attempts = task.attempts + 1
        AnnouncementRefresh.objects.filter(pk=task.pk, lease=task.lease, revision=task.revision).update(
            attempts=attempts, due_at=timezone.now()+timedelta(seconds=min(3600, 30 * 2**min(attempts, 7))),
            error=str(exc)[:300] if isinstance(exc, SourceError) else 'Ошибка обновления подробностей; повторим отдельно.')
        return False
    finally:
        AnnouncementRefresh.objects.filter(pk=task.pk, lease=task.lease).update(lease='', lease_until=None)
