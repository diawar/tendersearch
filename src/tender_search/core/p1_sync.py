"""Atomic shared catalog snapshot; private costs never go to AI or public views."""
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Func, JSONField, Value
from django.utils import timezone

from .models import P1CatalogSnapshot
from .p1_catalog import fetch_page, P1CatalogError

REFRESH_INTERVAL = timedelta(hours=1)
MAX_AGE = timedelta(hours=2)


def claim():
    if not getattr(settings, 'P1_API_TOKEN', ''):
        return None
    with transaction.atomic():
        P1CatalogSnapshot.objects.get_or_create(pk=1)
        state = P1CatalogSnapshot.objects.select_for_update().defer('products').get(pk=1)
        now = timezone.now()
        if ((state.lease_until and state.lease_until > now)
                or (state.retry_at and state.retry_at > now)
                or (state.completed_at and state.completed_at > now - REFRESH_INTERVAL)):
            return None
        state.lease = secrets.token_hex(16)
        state.lease_until = now + timedelta(minutes=20)
        state.save(update_fields=['lease', 'lease_until'])
        return state.lease


def fail(lease):
    P1CatalogSnapshot.objects.filter(pk=1, lease=lease).update(
        lease='', lease_until=None, retry_at=timezone.now()+timedelta(minutes=5),
        error='Не удалось обновить p1. Предыдущий полный снимок сохранён.')


def schedule():
    lease = claim()
    if lease:
        from .tasks import sync_p1_catalog
        try:
            sync_p1_catalog.delay(lease)
        except Exception:
            fail(lease)


def process(lease):
    if not P1CatalogSnapshot.objects.filter(pk=1, lease=lease).exists():
        return
    try:
        first = fetch_page(page_size=100)
        total = first.get('totalPages')
        expected = first.get('totalElements')
        if type(total) is not int or not 1 <= total <= 5000 or type(expected) is not int:
            raise P1CatalogError('Изменился формат страниц p1.')
        products = {}
        count = 0
        for number in range(total):
            page = first if number == 0 else fetch_page(page_number=number, page_size=100)
            if (page.get('currentPage') != number or page.get('totalPages') != total
                    or page.get('totalElements') != expected or not isinstance(page.get('pageItems'), list)
                    or bool(page.get('hasNext')) != (number < total - 1)):
                raise P1CatalogError('Каталог изменился во время обхода.')
            for item in page['pageItems']:
                count += 1
                sku = str(item.get('skuOver') or '').strip()
                if not sku:
                    continue
                if sku in products:
                    raise P1CatalogError('Неоднозначный SKU в каталоге p1.')
                products[sku] = {key: item.get(key) for key in ('skuOver', 'name', 'dealersData', 'isArchived')}
            if number % 20 == 0:
                if not P1CatalogSnapshot.objects.filter(pk=1, lease=lease).update(
                        lease_until=timezone.now()+timedelta(minutes=20)):
                    return
        if count != expected or not products:
            raise P1CatalogError('Каталог получен не полностью.')
        P1CatalogSnapshot.objects.filter(pk=1, lease=lease).update(products=products,
            completed_at=timezone.now(), lease='', lease_until=None, retry_at=None, error='')
    except Exception:
        fail(lease)
        raise P1CatalogError('Не удалось обновить p1. Предыдущий полный снимок сохранён.') from None


def product(sku):
    import re
    sku = str(sku or '').strip()
    if not re.fullmatch(r'[\w.-]{1,100}', sku):
        raise P1CatalogError('Укажите точный SKU товара Over.')
    # KeyTransform coerces numeric SKUs to JSON array indexes; force a text path.
    state = P1CatalogSnapshot.objects.filter(pk=1).annotate(item=Func(
        F('products'), Value(sku), function='jsonb_extract_path', output_field=JSONField()
    )).values('item', 'completed_at').first()
    if not state or not state['completed_at'] or state['completed_at'] < timezone.now()-MAX_AGE:
        raise P1CatalogError('Справочник p1 ещё загружается или устарел. Повторите расчёт после обновления.')
    item = state['item']
    if not isinstance(item, dict) or item.get('isArchived'):
        raise P1CatalogError('Активный товар с таким SKU не найден в p1.')
    return item, state['completed_at']
