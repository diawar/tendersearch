"""Profile an existing search against local data, rolling back every probe.

Run inside the application container:
    python /tmp/profile_search.py --search UUID --runs 3
No source HTTP requests or Telegram tasks are dispatched.
"""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime
import json
import hashlib
import os
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tender_search.web.settings')
import django
django.setup()

from django.db import connection, transaction
from django.test import override_settings
from django.utils import timezone
from django.db.models.query import QuerySet
from tender_search.core import index_service
from tender_search.core.models import IndexedLot, MedicalIndexedLot, SearchHit, WebSearch


class Measurements:
    def __init__(self):
        self.stages = {}
        self.sql = {}

    @contextmanager
    def stage(self, name):
        start = perf_counter()
        try:
            yield
        finally:
            self.stages[name] = self.stages.get(name, 0) + perf_counter() - start

    def timed(self, name, fn):
        def call(*args, **kwargs):
            with self.stage(name):
                return fn(*args, **kwargs)
        return call

    def execute(self, execute, sql, params, many, context):
        # Aggregate counts and durations only; never capture query parameters.
        verb = sql.lstrip().split(None, 1)[0].upper()
        start = perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            value = self.sql.setdefault(verb, {'count': 0, 'seconds': 0})
            value['count'] += 1
            value['seconds'] += perf_counter() - start


def probe(original):
    measurements = Measurements()
    iterator = QuerySet.iterator
    hit_manager = SearchHit.objects
    batch_store = getattr(index_service, 'store_search_hits', None)

    def timed_iterator(queryset, *args, **kwargs):
        stream = iterator(queryset, *args, **kwargs)
        if queryset.model not in (IndexedLot, MedicalIndexedLot):
            yield from stream
            return
        while True:
            with measurements.stage('read_candidates'):
                try:
                    item = next(stream)
                except StopIteration:
                    return
            yield item

    with transaction.atomic():
        job = WebSearch.objects.create(owner_id=original.owner_id,
                                       specification=original.specification)
        start = perf_counter()
        with ExitStack() as stack, connection.execute_wrapper(measurements.execute), \
                patch.object(QuerySet, 'iterator', timed_iterator), \
                patch.object(index_service, 'local_matches', measurements.timed('filters', index_service.local_matches)), \
                patch.object(index_service, 'match_lot', measurements.timed('text_matching', index_service.match_lot)), \
                patch.object(index_service, 'store_lot', measurements.timed('save_lots', index_service.store_lot)), \
                patch.object(hit_manager, 'get_or_create', measurements.timed('save_hits', hit_manager.get_or_create)):
            if batch_store:
                stack.enter_context(patch.object(index_service, 'store_search_hits', measurements.timed('save_batches', batch_store)))
            index_service.execute_local_search(job.pk)
        elapsed = perf_counter() - start
        job.refresh_from_db()
        result = {'elapsed_seconds': elapsed, 'status': job.status, 'scanned': job.scanned,
                  'hits': job.hits.count(), 'error': job.error,
                  'query': job.source_counts.get('__query__', {}),
                  'stages_seconds': measurements.stages, 'sql': measurements.sql}
        result['stages_seconds']['other'] = max(0, elapsed-sum(measurements.stages.values()))
        # Also verifies identity, not just equal hit counts across repetitions.
        identities = {(source, identifier, tuple(terms)) for source, identifier, terms in
                      job.hits.values_list('lot__announcement__source', 'lot__source_id', 'terms')}
        result['result_digest'] = hashlib.sha256(json.dumps(sorted(identities), ensure_ascii=False).encode()).hexdigest()
        transaction.set_rollback(True)
    return result, identities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search', required=True)
    parser.add_argument('--runs', type=int, default=3, choices=range(1, 11))
    parser.add_argument('--output', type=Path, help='Optional local JSON report path (keep in ignored data/).')
    parser.add_argument('--reference', action='store_true', help='Disable new SQL candidates and cache for comparison.')
    parser.add_argument('--no-cache', action='store_true', help='Measure SQL candidates without cache.')
    parser.add_argument('--warm-cache', action='store_true', help='Prime only the candidate cache before rollback probes.')
    parser.add_argument('--at', help='Freeze aware ISO time for comparable deadline/status checks.')
    args = parser.parse_args()
    original = WebSearch.objects.get(pk=args.search)
    baseline = {(source, identifier, tuple(terms)) for source, identifier, terms in
                original.hits.values_list('lot__announcement__source', 'lot__source_id', 'terms')}
    previous = None
    report = {'search_id': str(original.pk), 'at': args.at, 'original_elapsed_seconds':
              (original.finished_at-original.created_at).total_seconds() if original.finished_at else None,
              'note': 'Synchronous rollback probes; excludes queue wait. SQL timings overlap stage timings. Live index may change between probes.',
              'runs': []}
    with override_settings(SEARCH_DB_PREFILTERS=not args.reference,
                           SEARCH_CANDIDATE_CACHE=not (args.reference or args.no_cache)), ExitStack() as stack:
        if args.at:
            at = datetime.fromisoformat(args.at.replace('Z', '+00:00'))
            if at.tzinfo is None:
                parser.error('--at must include a timezone')
            stack.enter_context(patch('django.utils.timezone.now', return_value=at))
        if args.warm_cache:
            from tender_search.core.query_search import search_sources
            search_sources(original.specification, timezone.now(), original.owner_id)
        for _ in range(args.runs):
            result, identities = probe(original)
            result['same_hits_as_original'] = identities == baseline
            result['same_hits_as_previous_probe'] = None if previous is None else identities == previous
            previous = identities
            report['runs'].append(result)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered+'\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
