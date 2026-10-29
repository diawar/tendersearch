from datetime import datetime
from django.utils import timezone
from tender_search.client import utc_datetime, SourceError

STATUS_GROUPS = [
    ('Опубликованы — все сроки', {6,7,9}),
    ('Предварительное обсуждение', {66}),
    ('Дополнение заявок', {8}),
    ('Торги и аукционы', {21,22,30,60,63}),
    ('Рассмотрение и подведение итогов', {5,10,11,12,13,14,15,29,31,33,34,36,58,59,62,67,71,74,80}),
    ('Завершены', {16,42}),
    ('Не состоялись', {17,41}),
    ('Отменены / приостановлены', {18,19,20,23,32,40,65}),
]
# Navigation families verified against the public dictionary on 2026-10-29.
# Membership is not a claim of equivalent participation rules. Persist source IDs,
# never group labels, so regrouping cannot change an existing saved selection.
METHOD_GROUPS = [
    ('Запрос ценовых предложений', {12}),
    ('Запрос ценовых предложений — не ГЗ', {33}),
    ('Запрос ценовых предложений на понижение', {63}),
    ('Запрос ценовых предложений с ограниченным участием', {62}),
    ('Конкурсы', {11,18,23,31,36,41,47,48,76,81}),
    ('Тендеры', {32,61,65,66,67,75,77}),
    ('Конкурентные переговоры', {68}),
    ('Аукционы', {14,35,38,71,74}),
    ('Электронный магазин', {30,39}),
    ('Из одного источника — прямое заключение договора', {10,37,43}),
    ('Из одного источника — по несостоявшимся закупкам', {13,44}),
    ('Из одного источника — другие варианты', {2,80,82}),
    ('Особый и специальный порядок', {16,29,34,40}),
    ('Конкурсы по питанию обучающихся', {26,27}),
    ('Государственный социальный заказ', {42}),
    ('Государственный образовательный заказ', {46}),
    ('Закуп медицинских услуг', {79}),
    ('Специальные процедуры закупки лекарств и медицинских изделий', {83,84,85,86,87}),
    ('Закупка жилища', {24,72}),
    ('Товарные биржи', {8}),
    ('Организованные электронные торги', {15}),
    ('Централизованные торги электрической энергией', {64}),
    ('Внутрихолдинговая кооперация', {60}),
    ('Гарантированный заказ', {28}),
    ('Маркетинговые исследования', {50}),
]


def grouped_choices(rows, field):
    result = []
    if field == 'status':
        for prefix, label in [('open','Приём открыт'),('upcoming','Ожидается начало приёма')]:
            options=[(f'{prefix}:{row.source_id}',f'{row.name} — {label.lower()}') for row in rows if row.source_id in {6,7,9}]
            if options: result.append((label, options))
    used=set()
    for label, identifiers in STATUS_GROUPS if field == 'status' else METHOD_GROUPS:
        options=[(str(row.source_id),row.name) for row in rows if row.source_id in identifiers]
        if options:
            result.append((label,options));used.update(identifiers)
    other=[(str(row.source_id),row.name) for row in rows if row.source_id not in used]
    if other: result.append(('Прочие',other))
    return result


def status_matches(row, selected, now=None):
    if not selected or all(':' not in value for value in selected):
        return True
    now=now or timezone.now()
    status=row.get('status') or {}
    identifier=str(status.get('id') if isinstance(status,dict) else status)
    if identifier in selected:
        return True
    def date(value):
        try: return utc_datetime(value)
        except (ValueError,TypeError,SourceError): return None
    start,end=date(row.get('offer_start_date')),date(row.get('offer_end_date'))
    if not end or end <= now:
        return False
    for value in selected:
        if value == f'open:{identifier}' and (start <= now if start else identifier in {'7','9'}):
            return True
        if value == f'upcoming:{identifier}' and start and start > now:
            return True
    return False


def runtime_status_filters(filters, selected, now=None):
    filters=dict(filters)
    if selected and all(value.startswith(('open:','upcoming:')) for value in selected):
        now=now or timezone.now()
        bound=filters.get('offer_end_date__gte')
        if bound:
            parsed=datetime.fromisoformat(str(bound).replace('Z','+00:00'))
            if parsed.tzinfo is None: parsed=timezone.make_aware(parsed)
            now=max(now,parsed)
        filters['offer_end_date__gte']=now.isoformat()
    return filters
