"""Read-only Tenderbot comparison for primary government price-offer searches.

Uses the API key from a local env file, never persists credentials or headers.
Reports keyword candidate differences separately from exact local matching.
"""
import argparse
from collections import Counter
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, '/app/src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_search.web.settings')
import django
django.setup()

import httpx
from django.test import override_settings
from django.utils import timezone
from tender_search.client import ZakupClient, utc_datetime
from tender_search.matching import PreparedMatcher, match_lot
from tender_search.core.index_service import local_matches
from tender_search.core.models import IndexedLot, WebSearch, AnnouncementLotCache
from tender_search.core.procurement_groups import status_matches
from tender_search.core.query_search import search_sources

API = 'https://api.tenderbot.kz/api/external/v1'


def number(value):
    return re.sub(r'\s+', '', str(value or '')).replace('–', '-').replace('—', '-').upper()


def token_from_file(path):
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        key, separator, value = line.partition('=')
        if separator and key.strip() == 'TENDERBOT_API_KEY':
            token = value.strip().strip('"').strip("'")
            if token:
                return token
    raise ValueError('TENDERBOT_API_KEY is missing or empty')


def explain_missing(items, local, spec, now):
    """Separate status, grouping and genuine indexing gaps using source data."""
    numbers = [item['number'] for item in items]
    exact = {number(row.row.get('lot_number')): row for row in
             IndexedLot.objects.filter(row__lot_number__in=numbers)}
    external_ids = {}
    for item in items:
        match = re.search(r'/announce/index/(\d+)', item.get('portal_url') or '')
        if match:
            external_ids[item['id']] = int(match.group(1))
    announcements = {}
    for record in IndexedLot.objects.filter(row__announcement_external_id__in=list(set(external_ids.values()))):
        announcements.setdefault(record.row.get('announcement_external_id'), []).append(record)
    matcher = PreparedMatcher(spec)
    with ZakupClient() as api:
        for item in items:
            record = exact.get(number(item['number']))
            if record:
                row = record.row
                start, end = utc_datetime(row.get('offer_start_date')), utc_datetime(row.get('offer_end_date'))
                item.update(local_source_id=record.source_id, local_start=row.get('offer_start_date'),
                            local_method_id=record.method_id)
                if record.method_id != 12:
                    reason = 'different_method'
                elif start and start > now:
                    reason = 'applications_not_started'
                elif end and end <= now:
                    reason = 'expired'
                elif not record.current or record.details_skipped:
                    reason = 'not_current_or_skipped'
                elif not status_matches(row, spec['status_selection'], now):
                    reason = 'different_status'
                elif not match_lot(row.get('name_ru') or row.get('name'), row.get('description_ru'), matcher):
                    reason = 'different_description'
                else:
                    reason = 'needs_investigation'
                item['reason'] = reason
                continue
            external = external_ids.get(item['id'])
            bundled = [r for r in announcements.get(external, [])
                       if '-ОЛ-' in str(r.row.get('lot_number')) and number(r.row.get('lot_number')) in local]
            if bundled:
                item['reason'] = 'bundled_in_local_announcement'
                item['local_bundle_numbers'] = sorted({r.row['lot_number'] for r in bundled})
                cache = AnnouncementLotCache.objects.filter(source_id=bundled[0].announcement_source_id).first()
                item['plan_external_id_confirmed'] = bool(cache and any(
                    number(plan.get('external_id')) == number(item['number'])
                    for detail in cache.rows for plan in detail.get('plan_items') or []))
                continue
            item['reason'] = 'absent_from_index'
            if external:
                page = api.search('announcements', q=str(external), limit=100)
                live = next((r for r in page.results if r.get('external_id') == external), None)
                if live:
                    item['source_announcement_id'] = live['id']
                    item['source_start'] = live.get('offer_start_date')
                    start = utc_datetime(live.get('offer_start_date'))
                    if start and start > now:
                        item['reason'] = 'applications_not_started_not_indexed_yet'
                    elif live.get('purchase_method_id') != 12:
                        item['reason'] = 'different_method_live_source'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search', required=True)
    parser.add_argument('--env-file', type=Path, default=Path('/run/tendersearch.env'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-pages', type=int, default=30)
    parser.add_argument('--request-interval', type=float, default=0.4,
                        help='Pause between pages in seconds; use zero for a latency benchmark.')
    parser.add_argument('--skip-diagnosis', action='store_true', help='Skip source lookups during timing probes.')
    args = parser.parse_args()
    # Disable HTTP logging before any authenticated request. Never print errors
    # with request headers or remote error bodies, which may echo credentials.
    logging.disable(logging.CRITICAL)
    token = token_from_file(args.env_file)
    job = WebSearch.objects.get(pk=args.search)
    spec, now = job.specification, timezone.now()
    filters = spec.get('filters', {})
    if (set(filters) - {'status_id__in', 'purchase_method_id__in', 'subject'}
            or filters.get('purchase_method_id__in') != '12'
            or spec.get('mode', 'any') != 'any' or spec.get('customer_bin')
            or not spec.get('status_selection')
            or any(not str(status).startswith('open:') for status in spec['status_selection'])):
        parser.error('Supports open primary government price offers, OR terms and optional goods subject only.')
    if filters.get('subject') not in (None, '', '1'):
        parser.error('Only goods subject mapping is verified against the live Tenderbot dictionary.')
    matcher = PreparedMatcher(spec)
    local, outside_scope = {}, Counter()
    with override_settings(SEARCH_CANDIDATE_CACHE=False):
        sources, prefixes, _ = search_sources(spec, now, job.owner_id)
        for rows in sources:
            if rows.model is not IndexedLot:
                continue
            for row in rows.values_list('row', flat=True).iterator(chunk_size=200):
                terms = match_lot(row.get('name_ru') or row.get('name'), row.get('description_ru'), matcher)
                if local_matches(row, spec, now, prefixes) and terms:
                    system = row.get('system') or {}
                    if system.get('id') != 1:
                        outside_scope[system.get('name') or str(system.get('id'))] += 1
                        continue
                    identifier = number(row.get('lot_number'))
                    if identifier:
                        local[identifier] = {'id': row['id'], 'number': row.get('lot_number'),
                                             'name': row.get('name_ru'), 'terms': terms,
                                             'announcement_id': row.get('announcement_id'),
                                             'announcement_external_id': row.get('announcement_external_id'),
                                             'deadline': row.get('offer_end_date')}
    params = [('query[]', term) for term in spec['terms']]
    # The reference ID is a grouped value. Keep it as one array item rather
    # than letting the API split it into two individually invalid method IDs.
    params += [('source_ids', '1'), ('method_ids[]', '493,501'), ('status', 'active'), ('limit', '100')]
    if filters.get('subject'):
        params.append(('subject_types', '1'))
    initial_params = list(params)
    # Do not exclude words in the external engine: apply the exact local rules
    # below, since Tenderbot also searches announcement titles/descriptions.
    remote, pages, complete, seen_urls = {}, 0, False, set()
    url = API+'/lots'
    fetch_started = time.perf_counter()
    with httpx.Client(headers={'Authorization': 'Bearer '+token, 'Accept': 'application/json'},
                      timeout=45, follow_redirects=False) as client:
        for _ in range(args.max_pages):
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or parsed.netloc != 'api.tenderbot.kz'
                    or parsed.path != '/api/external/v1/lots' or url in seen_urls):
                raise ValueError('Unsafe or repeating pagination URL')
            seen_urls.add(url)
            response = client.get(url, params=params)
            if response.status_code != 200:
                if response.status_code == 422:
                    errors = response.json().get('errors', {})
                    print(json.dumps(errors, ensure_ascii=False).replace(token, '[redacted]'), file=sys.stderr)
                raise ValueError(f'Tenderbot HTTP {response.status_code}')
            payload = response.json()
            rows = payload.get('data')
            if not isinstance(rows, list):
                raise ValueError('Unexpected Tenderbot data format')
            for row in rows:
                remote[row['id']] = row
            pages += 1
            print(f'page={pages}, unique_lots={len(remote)}', flush=True)
            url = payload.get('links', {}).get('next')
            if not url:
                complete = True
                break
            if not rows:
                raise ValueError('Empty page with continuation')
            params = None
            time.sleep(max(0, args.request_interval))
    fetch_seconds = time.perf_counter()-fetch_started
    raw, matched, unnumbered, sources = {}, {}, [], {}
    for row in remote.values():
        identifier = number(row.get('lot_number'))
        if not identifier:
            unnumbered.append(row['id'])
            continue
        deadline = utc_datetime((row.get('dates') or {}).get('deadline'))
        tender = row.get('tender') or {}
        host = urlsplit(tender.get('link') or '').hostname
        sources[host or 'unknown'] = sources.get(host or 'unknown', 0)+1
        item = {'id': row['id'], 'number': row.get('lot_number'), 'name': row.get('name'),
                'description': row.get('description'), 'url': row.get('href'),
                'portal_url': tender.get('link'), 'method_id': tender.get('method_id'),
                'deadline': (row.get('dates') or {}).get('deadline')}
        raw[identifier] = item
        terms = match_lot(row.get('name'), row.get('description'), matcher)
        if terms and deadline and deadline > now and row.get('status') == 'active':
            matched[identifier] = dict(item, terms=terms)
    report = {'started_at': now.isoformat(), 'finished_at': timezone.now().isoformat(),
              'search_id': str(job.pk), 'terms': spec['terms'], 'exclude': spec.get('exclude', []),
              'local_filters': filters, 'tenderbot_parameters': initial_params,
              'tenderbot_filter_mapping': {'source_ids': '1', 'method_ids': '493,501', 'status': 'active'},
              'pages': pages, 'pagination_complete': complete, 'portal_hosts': sources,
              'tenderbot_fetch_seconds': fetch_seconds,
              'tenderbot_pacing_seconds': max(0, pages-1)*max(0, args.request_interval),
              'local_count': len(local), 'tenderbot_raw_count': len(remote),
              'local_other_systems_excluded': dict(outside_scope),
              'tenderbot_same_text_rules_count': len(matched),
              'overlap_raw': len(local.keys() & raw.keys()),
              'overlap_same_text_rules': len(local.keys() & matched.keys()),
              'local_only_raw': [local[key] for key in sorted(local.keys()-raw.keys())],
              'local_results': list(local.values()),
              'tenderbot_only_raw': [raw[key] for key in sorted(raw.keys()-local.keys())],
              'tenderbot_only_same_text_rules': [matched[key] for key in sorted(matched.keys()-local.keys())],
              'unnumbered_tenderbot_ids': unnumbered,
              'limitations': ['Live data may change during comparison.',
                              'Tenderbot searches announcement fields and word forms; local matching uses lot name and extended lot descriptions.',
                              'This comparison covers primary government price offers only; MED/FMS excluded.',
                              'API active status may group application dates differently.']}
    if not args.skip_diagnosis:
        explain_missing(report['tenderbot_only_same_text_rules'], local, spec, now)
        report['difference_reasons'] = dict(Counter(item['reason'] for item in report['tenderbot_only_same_text_rules']))
    report['local_only_grouped_lots'] = sum('-ОЛ-' in item['number'] for item in report['local_only_raw'])
    report['finished_at'] = timezone.now().isoformat()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key not in {
        'local_only_raw', 'tenderbot_only_raw', 'tenderbot_only_same_text_rules', 'tenderbot_parameters', 'local_results'}}, ensure_ascii=False))
    print(f'local_only={len(report["local_only_raw"])}, tenderbot_only_same_rules={len(report["tenderbot_only_same_text_rules"])}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, httpx.HTTPError) as exc:
        # Only locally generated validation messages are printed verbatim.
        print(str(exc) if isinstance(exc, ValueError) else type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
