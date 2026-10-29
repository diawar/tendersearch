"""Advance-payment facts from the portal's plan items, without network calls."""
from decimal import Decimal, InvalidOperation


def has_advance(detail):
    """True for a positive advance; None means no usable payment data."""
    known = False
    for item in (detail or {}).get('plan_items') or []:
        for entry in [item, *(item.get('specifics') or [])]:
            value = entry.get('prepayment')
            if value is None or isinstance(value, bool):
                continue
            try:
                amount = Decimal(str(value).replace(',', '.'))
            except InvalidOperation:
                continue
            if not amount.is_finite() or amount < 0:
                continue
            known = True
            if amount > 0:
                return True
    return False if known else None
