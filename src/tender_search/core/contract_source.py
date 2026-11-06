"""Read-only contract evidence. Public API IDs, not legacy OWS status codes."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from tender_search.client import SourceError

ROOT = '/api/core/api/public/'


class ContractReview(SourceError):
    pass


def identifier(value):
    return str(value.get('id') if isinstance(value, dict) else value or '')


def money(value):
    try:
        value = Decimal(str(value))
        if value.is_finite() and value >= 0:
            return value
    except (InvalidOperation, ValueError):
        pass
    raise ContractReview('Не удалось подтвердить суммы исполнения договора.')


def occurred(value, now):
    try:
        date = (datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float)) and not isinstance(value, bool)
                else datetime.fromisoformat(str(value).replace('Z', '+00:00')))
        return date.tzinfo is not None and datetime(2000, 1, 1, tzinfo=timezone.utc) <= date <= now
    except (ValueError, TypeError, OverflowError, OSError):
        return False


def rows_all(api, path, params):
    rows, seen = [], set()
    for page in range(10):
        data = api.get_json(path, dict(params, limit=100, offset=page * 100))
        if isinstance(data, list):
            if page:
                raise ContractReview('Неполная выгрузка договоров.')
            return data
        batch = data.get('results')
        count = data.get('count')
        if not isinstance(batch, list) or not isinstance(count, int):
            raise ContractReview('Неизвестный формат реестра договоров.')
        for row in batch:
            key = identifier(row)
            if not key or key in seen:
                raise ContractReview('Повтор страницы реестра договоров.')
            seen.add(key); rows.append(row)
        if not data.get('next') and len(rows) == count:
            return rows
        if not batch:
            break
    raise ContractReview('Реестр договоров получен не полностью.')


def contract_evidence(api, lot, company_bin, now):
    organizations = rows_all(api, ROOT + 'organizations/', {'q': company_bin})
    organizations = [row for row in organizations if str(row.get('iin_bin') or '') == company_bin]
    if len(organizations) != 1:
        raise ContractReview('Не удалось однозначно определить организацию по БИН.')
    supplier = organizations[0]['id']
    # Read current lot status as well as the protocol; never advance during appeals.
    current_lots = api.announcement_lots(lot.announcement.source_id)
    current = [row for row in current_lots if str(row.get('lot_number')) == lot.number and str(row.get('id')) == str(lot.source_id)]
    if len(current) != 1 or identifier(current[0].get('status') or current[0].get('status_id')) != '16':
        raise ContractReview('Текущий статус лота не подтверждает состоявшуюся закупку. Требуется проверка.')
    contracts = rows_all(api, ROOT + 'contracts/', {'supplier_organization_id': supplier,
                                                  'announcement_number': lot.announcement.number})
    matches = []
    for summary in contracts:
        if (str(summary.get('supplier_organization_id')) != str(supplier) or
                summary.get('announcement_number') != lot.announcement.number or
                str(summary.get('system_id')) != '1'):
            raise ContractReview('Реестр договоров вернул противоречащие фильтрам данные.')
        pk = summary.get('id')
        if not str(pk).isdigit():
            raise ContractReview('Неизвестный идентификатор договора.')
        detail = api.get_json(ROOT + f'contracts/{pk}/')
        if (str(detail.get('id')) != str(pk) or identifier(detail.get('system')) != '1' or
                str((detail.get('supplier_organization') or {}).get('iin_bin')) != company_bin or
                (detail.get('announcement') or {}).get('announcement_number') != lot.announcement.number):
            raise ContractReview('Данные карточки договора не подтверждают поставщика и объявление.')
        subjects = rows_all(api, ROOT + f'contracts/{pk}/subjects/', {})
        relevant = []
        for subject in subjects:
            linked = subject.get('lot') or {}
            if linked.get('lot_number') != lot.number:
                continue
            if str(linked.get('id')) != str(lot.source_id) or identifier(subject.get('contract')) != str(pk):
                raise ContractReview('Противоречивая связь предмета договора с лотом.')
            relevant.append(subject)
        if relevant:
            matches.append((detail, relevant))
    if not matches:
        return {'state': 'waiting', 'message': 'Связанный договор пока не опубликован.'}
    if len(matches) != 1:
        raise ContractReview('Найдено несколько договоров или допсоглашений по лоту. Требуется проверка.')
    detail, subjects = matches[0]
    if len({s.get('id') for s in subjects}) != len(subjects):
        raise ContractReview('Повторяющиеся предметы договора требуют проверки.')
    if identifier(detail.get('contract_type')) != '1' or identifier(detail.get('root_contract')) != str(detail['id']):
        raise ContractReview('Допсоглашение требует проверки действующей версии договора.')
    status = identifier(detail.get('status'))
    evidence = {'contract_id': detail['id'], 'number': detail.get('contract_number_sys') or '',
                'status': (detail.get('status') or {}).get('name') or '',
                'subject_ids': [s['id'] for s in subjects], 'company_bin': company_bin}
    if status == '18':
        return dict(evidence, state='contract')
    if status not in ('6', '31', '11', '30'):
        raise ContractReview('Статус договора требует ручной проверки: ' + evidence['status'])
    if not occurred(detail.get('sign_date'), now):
        raise ContractReview('Дата подписания договора не подтверждена.')
    if not all(occurred(subject.get('actual_execution_date'), now) for subject in subjects):
        return dict(evidence, state='delivery')
    currency = detail.get('currency') or {}
    if currency.get('egz_code') != 'KZT':
        raise ContractReview('Валюта расчётов по договору не подтверждена.')
    # Require payment for every exact lot subject; a paid advance is not execution.
    totals = [(money(s.get('contract_price_with_vat')), money(s.get('actual_payment_sum_with_vat'))) for s in subjects]
    if any(total <= 0 for total, paid in totals):
        raise ContractReview('Не подтверждена стоимость предмета договора.')
    paid = all(payment >= total for total, payment in totals)
    return dict(evidence, state='paid' if paid and status == '11' else 'payment',
                total=str(sum(t for t, p in totals)), paid=str(sum(p for t, p in totals)))
