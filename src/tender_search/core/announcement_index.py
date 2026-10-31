"""Normalize announcement-card lots into the shared search representation."""
from tender_search.client import SchemaError, utc_datetime


def announcement_lot_row(announcement, detail):
    if not isinstance(detail.get('id'), int) or not isinstance(detail.get('status'), dict):
        raise SchemaError('В карточке лота отсутствует идентификатор или статус.')
    plans = [p for p in detail.get('plan_items') or [] if isinstance(p, dict)]
    customers, addresses, subjects = {}, {}, set()
    for plan in plans:
        customer = plan.get('organization') or {}
        if customer.get('id'):
            customers[customer['id']] = {
                'id': customer['id'], 'bin': customer.get('iin_bin') or customer.get('bin'),
                'name_ru': customer.get('name_ru') or customer.get('name'),
                'name_kk': customer.get('name_kk')}
        subject = plan.get('purchase_subject') or {}
        if subject.get('id'):
            subjects.add(subject['id'])
        locations = [plan.get('delivery_address')] + [d.get('kato') for d in plan.get('deliveries') or []]
        for location in locations:
            if isinstance(location, dict) and location.get('id'):
                addresses[location['id']] = location
    def date(key):
        value = utc_datetime(announcement.get(key))
        return value.isoformat() if value else None
    system = detail.get('system') or {}
    return {
        'id': detail['id'], 'external_id': detail.get('external_id'),
        'lot_number': detail.get('lot_number'), 'name_ru': detail.get('name_ru') or detail.get('name'),
        'name_kk': detail.get('name_kk'), 'description_ru': detail.get('description'),
        'announcement_id': announcement['id'], 'announcement_number': announcement.get('announcement_number'),
        'announcement_external_id': announcement.get('external_id'),
        'announcement_name_ru': announcement.get('name_ru') or announcement.get('name'),
        'announcement_publish_date': date('publish_date'),
        'offer_start_date': date('offer_start_date'), 'offer_end_date': date('offer_end_date'),
        'status': detail['status'], 'status_name': detail['status'].get('name'),
        'purchase_method_id': announcement.get('purchase_method_id'),
        'purchase_method_name': announcement.get('purchase_method_name'),
        'purchase_subject_id': sorted(subjects), 'system': system, 'system_id': system.get('id'),
        'total_price': detail.get('total_price'), 'customers': list(customers.values()),
        'delivery_addresses': list(addresses.values()), '_from_announcement': True,
    }
