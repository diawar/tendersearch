"""Small searchable projections; original JSON remains authoritative."""
from decimal import Decimal, InvalidOperation
import hashlib
import json

from tender_search.matching import normalize
from .subjects import subject_ids


def projection(row):
    text = normalize(f"{row.get('name_ru') or row.get('name') or ''}\n{row.get('description_ru') or ''}")
    value = row.get('total_price')
    amount, known = None, value is None
    if value is not None:
        try:
            number = Decimal(str(value))
            # Leave unusual numbers to the original Python comparison. This
            # also prevents numeric overflow in PostgreSQL's prefilter.
            if number.is_finite() and -1000 <= number.adjusted() <= 1000:
                amount, known = str(number), True
        except (InvalidOperation, ValueError):
            pass
    meta = {
        'amount': amount, 'amount_known': known,
        'advance': row.get('_has_advance') is True,
        'subjects': sorted(subject_ids(row)),
        'bins': list(dict.fromkeys(str(c.get('bin') or c.get('iin_bin') or '') for c in row.get('customers') or [])),
        'delivery_ids': list(dict.fromkeys(str(a.get('id')) for a in row.get('delivery_addresses') or [])),
        'delivery_codes': list(dict.fromkeys(str(a.get('code') or '') for a in row.get('delivery_addresses') or [])),
        'lot_number': str(row.get('lot_number') or '').strip().casefold(),
        'announcement_number': str(row.get('announcement_number') or '').casefold(),
    }
    fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                            separators=(',', ':')).encode()).hexdigest()
    return text, meta, fingerprint
