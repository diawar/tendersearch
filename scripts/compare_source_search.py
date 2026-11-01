"""Read-only comparison with the public zakup.gov.kz lot registry.

The portal's q search and TenderSearch's extended-description search differ.
This diagnostic reports both sets without treating their raw counts as equal.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, '/app/src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_search.web.settings')
import django
django.setup()

from django.utils import timezone
from tender_search.client import ZakupClient, utc_datetime
from tender_search.matching import PreparedMatcher, match_lot
from tender_search.core.index_service import local_matches
from tender_search.core.models import IndexedLot, WebSearch
from tender_search.core.query_search import search_sources
from tender_search.core.announcement_index import announcement_lot_row
from tender_search.core.search_service import extended_description


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-announcements', type=int, default=5,
                        help='Check additional results against this many live announcement cards.')
    args = parser.parse_args()
    job = WebSearch.objects.get(pk=args.search)
    spec, now = job.specification, timezone.now()
    filters = dict(spec.get('filters', {}))
    unsupported = set(filters) - {'subject', 'status_id__in', 'purchase_method_id__in'}
    if unsupported or spec.get('customer_bin') or spec.get('mode', 'any') != 'any':
        parser.error('This diagnostic currently supports OR terms, status, method and subject filters only.')
    filters.pop('subject', None)
    method_ids = {int(value) for value in str(filters['purchase_method_id__in']).split('__')} if filters.get('purchase_method_id__in') else None
    matcher = PreparedMatcher(spec)
    sources, prefixes, _ = search_sources(spec, now, job.owner_id)
    local = {}
    for rows in sources:
        if rows.model is not IndexedLot:
            continue  # This comparison covers the primary public registry only.
        for row in rows.values_list('row', flat=True).iterator(chunk_size=200):
            if local_matches(row, spec, now, prefixes) and match_lot(row.get('name_ru') or row.get('name'), row.get('description_ru'), matcher):
                local[row['id']] = row
    remote, counts, complete = {}, {}, True
    with ZakupClient() as api:
        for term in spec['terms']:
            for page in api.pages(q=term, limit=100, max_pages=100, ordering='-publish_date', **filters):
                counts[term] = page.count
                for row in page.results:
                    remote[row['id']] = row
            complete &= page.next_url is None
            print(f'{term}: {page.count}', flush=True)
    matched = {}
    for identifier, row in remote.items():
        deadline = utc_datetime(row.get('offer_end_date'))
        if deadline and deadline > now and local_matches(row, spec, now, prefixes) and match_lot(row.get('name_ru') or row.get('name'), row.get('description_ru'), matcher):
            matched[identifier] = row
    def brief(identifier, row):
        return {'id': identifier, 'number': row.get('lot_number'), 'name': row.get('name_ru'),
                'announcement_id': row.get('announcement_id'), 'deadline': row.get('offer_end_date')}
    missing = [brief(i, matched[i]) for i in sorted(matched.keys()-local.keys())]
    extra = []
    for identifier in sorted(local.keys()-matched.keys()):
        row = local[identifier]
        item = brief(identifier, row)
        item['in_portal_keyword_candidates'] = identifier in remote
        item['matches_name_without_extended_description'] = bool(match_lot(row.get('name_ru') or row.get('name'), '', matcher))
        extra.append(item)
    verified = []
    announcement_ids = list(dict.fromkeys(item['announcement_id'] for item in extra))[:max(0, args.verify_announcements)]
    with ZakupClient() as api:
        for announcement_id in announcement_ids:
            announcement = api.announcement(announcement_id)
            # Detail cards nest the method; registry rows expose a scalar ID.
            announcement['purchase_method_id'] = (announcement.get('purchase_method') or {}).get('id')
            details = {item['id']: item for item in api.announcement_lots(announcement_id)}
            for item in extra:
                if item['announcement_id'] != announcement_id:
                    continue
                detail = details.get(item['id'])
                valid, terms = False, []
                if detail:
                    fresh = announcement_lot_row(announcement, detail)
                    fresh['description_ru'] = extended_description(fresh, detail)
                    deadline = utc_datetime(fresh.get('offer_end_date'))
                    terms = match_lot(fresh.get('name_ru'), fresh['description_ru'], matcher)
                    valid = bool(not detail.get('is_externally_deleted') and deadline and deadline > now
                                 and (method_ids is None or fresh.get('purchase_method_id') in method_ids)
                                 and local_matches(fresh, spec, now, prefixes) and terms)
                verified.append({'id': item['id'], 'found_in_live_card': detail is not None,
                                 'matches_live_card': valid, 'terms': terms})
    report = {'started_at': now.isoformat(), 'finished_at': timezone.now().isoformat(),
              'search_id': str(job.pk), 'terms': spec['terms'], 'filters': spec['filters'],
              'portal_query_counts': counts, 'portal_pages_exhausted': complete,
              'portal_unique_candidates': len(remote), 'portal_matches_same_rules_without_extended_description': len(matched),
              'local_matches_with_extended_description': len(local),
              'intersection': len(local.keys() & matched.keys()),
              'portal_only': missing, 'local_only': extra,
              'additional_results_verified_from_live_cards': verified,
              'limitations': ['Live registry can change during pagination.',
                              'Portal q candidates do not guarantee coverage of extended descriptions.',
                              'Medical registries are excluded from this comparison.']}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'portal_only', 'local_only'}}, ensure_ascii=False))
    print(f'portal_only={len(missing)}, local_only={len(extra)}')


if __name__ == '__main__':
    main()
