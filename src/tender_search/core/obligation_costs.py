"""Local evidence hints, never AI calls or an assertion that extraction is complete."""
import re
from decimal import Decimal
from .deal_economics import decimal_value, money

RULES = (
    ('Подъём и занос в помещение', r'под[ъь]?[её]м|занос|поднят|\d+\s*[-–]?\s*(?:й\s*)?этаж|қабат|көтер'),
    ('Разгрузка', r'разгруз|выгруз|түсіру'),
    ('Монтаж и установка', r'монтаж|установк|орнат'),
    ('Настройка и ввод в эксплуатацию', r'настройк|настроить|пусконалад|ввод.{0,25}эксплуатац|іске қос|баптау'),
    ('Обучение персонала', r'обучени|инструктаж|оқыту'),
    ('Вывоз упаковки и утилизация', r'утилиз|вывоз.{0,25}(?:упаков|мусор)|қаптам.{0,25}әкету'),
    ('Гарантийное обслуживание и замена', r'гарантийн.{0,35}(?:ремонт|обслуж|замен)|подмен|кепілдік.{0,35}(?:жөндеу|ауыст)'),
    ('Сертификаты и сопроводительные документы', r'сертификат|сертификац'),
)


def suggestions(documents):
    found = {}
    for doc in documents:
        text = str(doc.get('text') or '')[:160000]
        for label, pattern in RULES:
            if label in found:
                continue
            match = re.search(pattern, text, re.I)
            if match:
                # Keep a contiguous excerpt, without joining unrelated clauses.
                start = max(0, match.start()-100)
                end = min(len(text), match.end()+200)
                found[label] = {'label': label, 'amount': '',
                    'evidence': text[start:end].strip(), 'document': str(doc.get('name') or '')[:250]}
    return list(found.values())


def normalize_entries(entries, *, require_amounts=False):
    if not isinstance(entries, list) or len(entries) > 30:
        raise ValueError('Можно указать до 30 дополнительных обязательств.')
    result, total = [], Decimal(0)
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError('Проверьте список дополнительных обязательств.')
        label = str(row.get('label') or '').strip()
        if not label or len(label) > 250:
            raise ValueError('Укажите название обязательства длиной до 250 символов.')
        amount = row.get('amount')
        if amount is None or str(amount).strip() == '':
            if require_amounts:
                raise ValueError('Оцените все дополнительные обязательства. Укажите 0, если расход уже включён или не требуется.')
            value = ''
        else:
            amount_text = re.sub(r'\s+', '', str(amount)).replace(',', '.')
            if not re.fullmatch(r'\d+(?:\.\d{1,2})?', amount_text):
                raise ValueError('Стоимость обязательства укажите с точностью до тиына.')
            value = money(decimal_value(amount_text, maximum=Decimal('1000000000000')))
            total += value
            value = str(value)
        result.append({'label': label, 'amount': value,
            'evidence': str(row.get('evidence') or '')[:500], 'document': str(row.get('document') or '')[:250]})
    return result, total


def context(owner, lot, documents):
    from .models import LotObligationCosts
    saved = LotObligationCosts.objects.filter(owner=owner, lot=lot).first()
    entries = saved.entries if saved else []
    return {'obligation_entries': entries}
