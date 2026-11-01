from datetime import timedelta
from decimal import Decimal
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from tender_search.client import SourceError, SchemaError, utc_datetime
from tender_search.matching import match_lot
from .procurement_groups import runtime_status_filters, status_matches
from .models import Announcement, Customer, Lot, SearchHit, WebSearch, AnnouncementLotCache

logger = logging.getLogger(__name__)


def lot_identity(row):
    source = row.get('_source', 'zakup')
    system = row.get('system_id') or (row.get('system') or {}).get('id')
    if source == 'fms_ecc' or (str(system) == '7' and row.get('announcement_external_id')):
        return ('fms_ecc', str(row['announcement_id'] if source == 'fms_ecc' else row['announcement_external_id']), str(row.get('lot_number')))
    if source != 'zakup':
        return (source, row['id'])
    number = str(row.get("lot_number") or "").strip()
    announcement = str(row.get("announcement_number") or "").strip()
    bins = tuple(sorted(str(c.get("bin")) for c in (row.get("customers") or []) if c.get("bin")))
    if number and announcement and bins:
        return ("procurement", number, announcement, bins)
    return ("source", row["id"])


def store_lot(row):
    """Keep public list snapshots separate from verified announcement cards."""
    aid = row.get("announcement_id")
    if not isinstance(aid, int) or not isinstance(row.get("id"), int):
        raise SchemaError("У лота отсутствуют идентификаторы источника")
    with transaction.atomic():
        source = row.get('_source', 'zakup')
        announcement, created = Announcement.objects.get_or_create(source=source, source_id=aid,
            defaults={"name": row.get("announcement_name_ru") or "", "number": row.get("announcement_number") or "",
                      "external_id": row.get("announcement_external_id"), "deadline": utc_datetime(row.get("offer_end_date"))})
        customer = None
        customers = row.get("customers") or []
        if customers:
            c = customers[0]
            customer, _ = Customer.objects.update_or_create(source=source, source_id=c["id"],
                defaults={"bin": c.get("bin") or "", "name": c.get("name_ru") or ""})
        if source != 'zakup' and row.get('_announcement'):
            card = row['_announcement']
            announcement.name = card['name_ru']
            announcement.raw = card
            announcement.status = card['status']['name']
            announcement.deadline = utc_datetime(card.get('offer_end_date'))
            announcement.save()
        defaults = {
            "external_id": row.get("external_id"), "number": row.get("lot_number") or "",
            "name": row.get("name_ru") or row.get("name") or "Без названия",
            "description": row.get("description_ru") or "", "customer": customer,
            "amount": Decimal(str(row["total_price"])) if row.get("total_price") is not None else None,
            "status": row.get("status_name") or (row.get("status") or {}).get("name", ""), "raw": row,
        }
        lot, created = Lot.objects.get_or_create(announcement=announcement, source_id=row["id"], defaults=defaults)
        if not created:
            for field, value in defaults.items():
                if lot.checked_at is None or field in {"raw", "customer", "external_id", "number"} or (field == "description" and row.get("_extended_description")):
                    setattr(lot, field, value)
            lot.save()
        return lot


def reusable_search_lot(lot, row):
    """Reuse a snapshot only when store_lot would preserve the same values.

    Verified card fields keep their existing precedence. Customer and medical
    announcement data are checked too, rather than assuming equal raw means
    all related objects are still current.
    """
    if lot.raw != row:
        return False
    customers = row.get('customers') or []
    customer = lot.customer
    if customers:
        value = customers[0]
        if (customer is None or customer.source != row.get('_source', 'zakup')
                or customer.source_id != value['id'] or customer.bin != (value.get('bin') or '')
                or customer.name != (value.get('name_ru') or '')):
            return False
    elif customer is not None:
        return False
    values = {'external_id': row.get('external_id'), 'number': row.get('lot_number') or ''}
    if lot.checked_at is None:
        values.update(name=row.get('name_ru') or row.get('name') or 'Без названия',
                      amount=Decimal(str(row['total_price'])) if row.get('total_price') is not None else None,
                      status=row.get('status_name') or (row.get('status') or {}).get('name', ''))
    if lot.checked_at is None or row.get('_extended_description'):
        values['description'] = row.get('description_ru') or ''
    if any(getattr(lot, key) != value for key, value in values.items()):
        return False
    if row.get('_source', 'zakup') != 'zakup' and row.get('_announcement'):
        card, announcement = row['_announcement'], lot.announcement
        if (announcement.raw != card or announcement.name != card['name_ru']
                or announcement.status != card['status']['name']
                or announcement.deadline != utc_datetime(card.get('offer_end_date'))):
            return False
    return True


def store_search_hits(search, matches):
    """Load existing snapshots together and insert hits in bounded batches."""
    if not matches:
        return
    groups = {}
    for row, _ in matches:
        groups.setdefault(row.get('_source', 'zakup'), []).append(row)
    query = Q(pk__in=[])
    for source, rows in groups.items():
        query |= Q(announcement__source=source,
                   announcement__source_id__in={row['announcement_id'] for row in rows},
                   source_id__in={row['id'] for row in rows})
    existing = {(lot.announcement.source, lot.announcement.source_id, lot.source_id): lot
                for lot in Lot.objects.filter(query).select_related('announcement', 'customer')}
    hits = []
    try:
        for row, terms in matches:
            key = (row.get('_source', 'zakup'), row['announcement_id'], row['id'])
            lot = existing.get(key)
            if lot is None or not reusable_search_lot(lot, row):
                lot = store_lot(row)
                existing[key] = lot
                # Different lot snapshots may share a customer/announcement.
                # Keep prefetched objects consistent with earlier writes in
                # this batch so the last snapshot retains the same precedence.
                for cached in existing.values():
                    if lot.customer_id and cached.customer_id == lot.customer_id:
                        cached.customer = lot.customer
                    if cached.announcement_id == lot.announcement_id:
                        cached.announcement = lot.announcement
            hits.append(SearchHit(search=search, lot=lot, terms=terms))
    finally:
        # Preserve preceding successful rows even if a later row is malformed.
        SearchHit.objects.bulk_create(hits, batch_size=50, ignore_conflicts=True)


def resolve_customer(api, bin_value):
    data = api.get_json("/api/core/api/public/organizations/", {"q": bin_value, "limit": 20})
    rows = data if isinstance(data, list) else data.get("results", data.get("result")) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise SchemaError("Изменился формат справочника заказчиков")
    found = [r["id"] for r in rows if str(r.get("iin_bin") or r.get("bin") or "") == bin_value]
    if not found:
        raise SourceError("Заказчик с таким БИН не найден в доступной выдаче справочника. Уточните БИН или уберите фильтр.")
    return found


def extended_description(row, detail=None):
    detail = detail or {}
    parts = [row.get('description_ru'), row.get('description_kk'), detail.get('description_ru'), detail.get('description_kk'), detail.get('description')]
    for plan in detail.get('plan_items') or []:
        if isinstance(plan, dict):
            parts.extend(plan.get(key) for key in ('name_ru', 'name_kk', 'name',
                'description_ru', 'description_kk', 'description',
                'extra_description_ru', 'extra_description_kk', 'extra_description'))
    return '\n'.join(dict.fromkeys(value.strip() for value in parts if isinstance(value, str) and value.strip()))


def announcement_details(api, announcement_id, local_cache, expected_id, max_age=timedelta(hours=6)):
    if announcement_id not in local_cache or expected_id not in local_cache[announcement_id]:
        cached = AnnouncementLotCache.objects.filter(source_id=announcement_id,checked_at__gte=timezone.now()-max_age).first()
        if cached and any(isinstance(item, dict) and item.get('id') == expected_id for item in cached.rows):
            rows = cached.rows
        else:
            rows = api.announcement_lots(announcement_id)
            if not isinstance(rows, list):
                raise SchemaError('Изменился формат расширенных описаний лотов.')
            AnnouncementLotCache.objects.update_or_create(source_id=announcement_id,defaults={
                'rows': rows, 'checked_at': timezone.now(), 'registry_fingerprint': ''})
        local_cache[announcement_id] = {item['id']:item for item in rows if isinstance(item, dict) and isinstance(item.get('id'), int)}
    return local_cache[announcement_id]


def execute_search(job_id, api):
    if not WebSearch.objects.filter(pk=job_id, status="queued").update(status="running", updated_at=timezone.now()):
        return
    job = WebSearch.objects.get(pk=job_id)
    specification = job.specification
    try:
        search_time = timezone.now()
        filters = runtime_status_filters(specification.get("filters", {}), specification.get("status_selection", []), search_time)
        subject = filters.pop('subject', '')
        if specification.get("customer_bin"):
            customers = resolve_customer(api, specification["customer_bin"])
            filters["customer_id" if len(customers) == 1 else "customer_id__in"] = "__".join(map(str, customers))
        seen, accepted, exhausted = set(), set(), []
        expanded = specification.get('extended_descriptions') and specification.get('kind') != 'field'
        details_cache = {}
        queries = list(specification['terms']) + ([None] if expanded else [])
        for term in queries:
            if not WebSearch.objects.filter(pk=job.pk,status="running").exists(): return
            for page in api.pages(q=term, limit=100 if specification.get("auto_pages") else 50, ordering="-publish_date", max_pages=100 if specification.get("auto_pages") else specification["pages"], **filters):
                if not WebSearch.objects.filter(pk=job.pk,status="running").exists(): return
                job.source_counts[term if term is not None else "__extended_candidates__"] = page.count
                for row in page.results:
                    job.scanned += 1
                    if row["id"] in seen:
                        continue
                    seen.add(row["id"])
                    if not status_matches(row,specification.get("status_selection", []),search_time): continue
                    from .subjects import subject_matches
                    if not subject_matches(row, subject): continue
                    if expanded:
                        if not WebSearch.objects.filter(pk=job.pk,status="running").exists(): return
                        details = announcement_details(api, row['announcement_id'], details_cache, row['id'])
                        detail = details.get(row['id'])
                        if detail is None:
                            raise SourceError('Лот отсутствует в расширенной карточке объявления. Повторите поиск позже.')
                        row = dict(row, description_ru=extended_description(row, detail), _extended_description=True)
                    matched = match_lot(row.get("name_ru") or row.get("name"), row.get("description_ru"), specification)
                    identity = lot_identity(row)
                    if matched and identity not in accepted:
                        accepted.add(identity)
                        lot = store_lot(row)
                        SearchHit.objects.update_or_create(search=job, lot=lot, defaults={"terms": matched})
                WebSearch.objects.filter(pk=job.pk,status="running").update(scanned=job.scanned, source_counts=job.source_counts, updated_at=timezone.now())
                if not WebSearch.objects.filter(pk=job.pk,status="running").exists(): return
            exhausted.append(page.next_url is None)
        job.complete = all(exhausted)
        job.status = "done" if job.complete else "partial"
    except Exception as exc:
        logger.exception("Search failed: %s", job.pk)
        job.error = str(exc) if isinstance(exc, SourceError) else "Не удалось завершить поиск. Повторите попытку позже."
        job.status = "partial" if job.hits.exists() else "error"
        job.complete = False
    job.finished_at = timezone.now()
    WebSearch.objects.filter(pk=job.pk,status="running").update(status=job.status,complete=job.complete,error=job.error,finished_at=job.finished_at,updated_at=timezone.now())


def refresh_lot_data(lot_id, api):
    lot = Lot.objects.select_related("announcement").get(pk=lot_id)
    try:
        card = api.announcement(lot.announcement.source_id)
        rows = api.announcement_lots(lot.announcement.source_id)
        row = next((r for r in rows if r["id"] == lot.source_id), None)
        if row is None:
            raise SourceError("Лот больше не найден в карточке объявления")
        deadline = utc_datetime(card.get("offer_end_date"))
        with transaction.atomic():
            Announcement.objects.filter(pk=lot.announcement_id).update(
                name=card.get("name_ru") or card.get("name") or lot.announcement.name,
                status=(card.get("status") or {}).get("name", ""), deadline=deadline,
                raw=card, checked_at=timezone.now())
            lot.name = row.get("name") or row.get("name_ru") or lot.name
            lot.description = extended_description(lot.raw, row) or lot.description
            lot.status = (row.get("status") or {}).get("name", lot.status)
            lot.amount = Decimal(str(row["total_price"])) if row.get("total_price") is not None else lot.amount
            lot.checked_at = timezone.now()
            lot.refresh_state, lot.refresh_error = "done", ""
            lot.save(update_fields=["name", "description", "status", "amount", "checked_at", "refresh_state", "refresh_error", "updated_at"])
    except Exception as exc:
        logger.exception("Lot refresh failed: %s", lot_id)
        Lot.objects.filter(pk=lot_id).update(refresh_state="error", refresh_error=str(exc) if isinstance(exc, SourceError)
                                              else "Не удалось обновить карточку. Повторите позже.")
