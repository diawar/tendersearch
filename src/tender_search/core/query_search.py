"""Conservative SQL candidates; PreparedMatcher remains the final authority."""
from datetime import timedelta
from decimal import Decimal
import hashlib
import json

from django.conf import settings
from django.db import connection, models
from django.db.models import F, Q, Value
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.utils import timezone

from tender_search.matching import normalize
from .models import IndexedLot, MedicalIndexedLot, ReferenceEntry, SearchCandidateCache, SearchIndexEpoch


class NumericAmount(models.Func):
    template = 'CAST(%(expressions)s AS NUMERIC)'
    output_field = models.DecimalField(max_digits=1000, decimal_places=500)


class CodePrefix(models.Func):
    output_field = models.BooleanField()

    def as_sql(self, compiler, connection, **kwargs):
        codes, codes_params = compiler.compile(self.source_expressions[0])
        prefix, prefix_params = compiler.compile(self.source_expressions[1])
        return (f'EXISTS (SELECT 1 FROM jsonb_array_elements_text({codes}) AS code '
                f'WHERE starts_with(code, {prefix}))', [*codes_params, *prefix_params])


def text_candidates(matcher):
    """Necessary conditions only; NULL projections always reach the exact matcher."""
    from tender_search.morphology import phrase_candidate_prefix
    conditions = []
    for _, term, _ in matcher.included:
        condition = Q(pk__isnull=False)
        words = term.split() if matcher.kind == 'phrase' else [term]
        for word in words:
            prefix = phrase_candidate_prefix(word) if matcher.kind == 'phrase' else word
            if prefix:
                condition &= Q(search_text__contains=prefix)
        conditions.append(condition)
    combined = Q(pk__isnull=False) if matcher.require_all else Q(pk__in=[])
    for condition in conditions:
        combined = combined & condition if matcher.require_all else combined | condition
    return Q(search_text__isnull=True) | combined


def document_sources(sources, spec, prefixes, document_matches):
    """Union metadata candidates with confirmed document identities, never intersect."""
    if connection.vendor != 'postgresql' or not settings.SEARCH_DB_PREFILTERS:
        return sources
    # A very broad document query is cheaper to stream than to turn into an
    # unbounded SQL parameter list. This fallback retains every candidate.
    if len(document_matches) > 5000:
        return sources
    groups = {}
    for source, announcement_id, lot_id in document_matches:
        lots, announcements = groups.setdefault(source, (set(), set()))
        if lot_id is None:
            announcements.add(announcement_id)
        else:
            lots.add(lot_id)
    filtered = []
    for rows in sources:
        extra = Q(pk__in=[])
        medical = rows.model is MedicalIndexedLot
        for source, (lots, announcements) in groups.items():
            if not medical and source != 'zakup':
                continue
            identity = Q(source_id__in=lots) | Q(row__announcement_id__in=announcements)
            if medical:
                identity &= Q(source=source)
            extra |= identity
        # Materialize only bounded IDs before fetching large registry JSON.
        # Combining text/JSON predicates with the payload cursor can otherwise
        # make PostgreSQL choose a full heap scan despite a tiny result set.
        limit = settings.SEARCH_CACHE_MAX_CANDIDATES
        identifiers = set(prefilter(rows, spec, prefixes).values_list('pk', flat=True)[:limit+1])
        if len(identifiers) <= limit:
            identifiers.update(rows.filter(extra).values_list('pk', flat=True)[:limit+1])
        filtered.append(rows.filter(pk__in=identifiers) if len(identifiers) <= limit else rows)
    return filtered


def prefilter(rows, spec, prefixes):
    filters = spec.get('filters', {})
    unknown = Q(search_meta__isnull=True)
    if filters.get('only_advance'):
        rows = rows.filter(unknown | Q(search_meta__contains={'advance': True}))
    if filters.get('subject'):
        rows = rows.filter(unknown | Q(search_meta__subjects=[]) |
                           Q(search_meta__contains={'subjects': [str(filters['subject'])]}))
    if spec.get('customer_bin'):
        rows = rows.filter(unknown | Q(search_meta__contains={'bins': [spec['customer_bin']]}))
    if filters.get('delivery_address_id__in'):
        address = Q(pk__in=[])
        for identifier in str(filters['delivery_address_id__in']).split('__'):
            address |= Q(search_meta__contains={'delivery_ids': [identifier]})
        for prefix in prefixes:
            address |= Q(CodePrefix(KeyTransform('delivery_codes', F('search_meta')), Value(prefix)))
        rows = rows.filter(unknown | address)
    for name in ('lot_number', 'announcement_number'):
        if filters.get(name):
            alias = f'_search_{name}'
            rows = rows.alias(**{alias: KeyTextTransform(name, F('search_meta'))}).filter(
                unknown | Q(**{f'{alias}__contains': str(filters[name]).strip().casefold()}))
    if any(filters.get(key) is not None for key in ('total_price__gte', 'total_price__lte')):
        rows = rows.alias(_search_amount=NumericAmount(KeyTextTransform('amount', F('search_meta'))))
        for name, comparison in [('total_price__gte', 'gte'), ('total_price__lte', 'lte')]:
            if filters.get(name) is not None:
                rows = rows.filter(unknown | Q(search_meta__amount_known=False) |
                                   Q(**{f'_search_amount__{comparison}': Decimal(str(filters[name]))}))
    # Use substring candidates for every mode. Word boundaries, phrases,
    # whole-field matching and exclusions are still checked exactly in Python.
    terms = [normalize(term) for term in spec['terms']]
    if not terms and any(filters.get(key) for key in ('lot_number', 'announcement_number')):
        return rows
    def text_condition(term):
        from tender_search.morphology import phrase_candidate_prefix
        # Phrase mode matches adjacent word prefixes: "беспроводн мыш"
        # matches "беспроводная мышка". Requiring the literal whole phrase
        # in SQL would lose that match. Each prefix is a necessary condition;
        # adjacency and ordering remain the Python matcher's responsibility.
        words = term.split() if spec.get('kind') == 'phrase' else [term]
        condition = Q()
        for word in words:
            if spec.get('kind')=='phrase':word=phrase_candidate_prefix(word)
            if not word:continue
            condition &= Q(search_text__contains=word)
        return condition if condition.children else Q(pk__isnull=False)
    text_unknown = Q(search_text__isnull=True)
    if any(filters.get(key) is not None for key in ('total_price__gte', 'total_price__lte')):
        text_unknown |= Q(search_meta__amount_known=False)
    if spec.get('mode', 'any') == 'all':
        for term in terms:
            if not term:
                return rows.filter(text_unknown)
            rows = rows.filter(text_unknown | text_condition(term))
    else:
        text = Q(pk__in=[])
        for term in terms:
            if term:
                text |= text_condition(term)
        rows = rows.filter(text_unknown | text)
    return rows


def candidate_sources(sources, spec, prefixes, owner_id, now):
    info = {'enabled': False, 'cache_hit': False}
    if spec.get('filters', {}).get('in_documents'):
        return sources, info
    if connection.vendor != 'postgresql' or not settings.SEARCH_DB_PREFILTERS:
        return sources, info
    info['enabled'] = True
    if not settings.SEARCH_CANDIDATE_CACHE or spec.get('filters', {}).get('remaining_hours'):
        candidates = []
        for rows in sources:
            filtered = prefilter(rows, spec, prefixes)
            identifiers = list(filtered.values_list('pk', flat=True)[:settings.SEARCH_CACHE_MAX_CANDIDATES+1])
            candidates.append(rows.filter(pk__in=identifiers)
                if len(identifiers) <= settings.SEARCH_CACHE_MAX_CANDIDATES else filtered)
        return candidates, info
    epoch, _ = SearchIndexEpoch.objects.get_or_create(pk=1)
    revision = epoch.revision
    # Public IDs only, scoped by owner. Runtime-only profile IDs do not alter
    # matching, and must not create a new cache entry for every monitor run.
    semantic = {key: value for key, value in spec.items() if key not in {'profile_id', 'monitor_profile_id'}}
    key = hashlib.sha256(json.dumps(['v2-phrase-morphology', owner_id, semantic, prefixes], sort_keys=True,
                                   ensure_ascii=False).encode()).hexdigest()
    cache = None
    if settings.SEARCH_CANDIDATE_CACHE:
        cache = SearchCandidateCache.objects.filter(key=key, revision=revision, expires_at__gt=now).first()
    if cache and len(cache.candidates) == len(sources):
        if SearchIndexEpoch.objects.values_list('revision', flat=True).get(pk=1) == revision:
            info['cache_hit'] = True
            return [rows.filter(pk__in=ids) for rows, ids in zip(sources, cache.candidates)], info
    filtered = [prefilter(rows, spec, prefixes) for rows in sources]
    # Bound memory and cache size. Large searches continue using SQL directly.
    candidates = [list(rows.values_list('pk', flat=True)[:settings.SEARCH_CACHE_MAX_CANDIDATES+1]) for rows in filtered]
    if sum(map(len, candidates)) > settings.SEARCH_CACHE_MAX_CANDIDATES:
        return filtered, info
    if SearchIndexEpoch.objects.values_list('revision', flat=True).get(pk=1) == revision:
        SearchCandidateCache.objects.update_or_create(key=key, defaults={
            'revision': revision, 'candidates': candidates,
            'expires_at': now+timedelta(seconds=settings.SEARCH_CACHE_TTL)})
        SearchCandidateCache.objects.filter(expires_at__lte=now).delete()
        old = list(SearchCandidateCache.objects.order_by('-expires_at').values_list('key', flat=True)[256:])
        if old:
            SearchCandidateCache.objects.filter(key__in=old).delete()
    # Reapply the base queryset (including current deadlines) on cache reads.
    return [rows.filter(pk__in=ids) for rows, ids in zip(sources, candidates)], info


def search_sources(spec, now, owner_id):
    from .index_service import date_value
    filters = spec.get('filters', {})
    sources = [MedicalIndexedLot.objects.filter(current=True, deadline__gt=now)]
    if not filters.get('only_medical'):
        sources.append(IndexedLot.objects.filter(current=True, details_skipped=False, deadline__gt=now))
    for key, field in [('status_id', 'status_id'), ('purchase_method_id', 'method_id')]:
        raw = filters.get(key+'__in', filters.get(key))
        if raw:
            sources = [rows.filter(**{field+'__in': [int(value) for value in str(raw).split('__')]}) for rows in sources]
    for key, comparison in [('offer_end_date__gte', 'gte'), ('offer_end_date__lte', 'lte')]:
        if filters.get(key):
            sources = [rows.filter(**{f'deadline__{comparison}': date_value(filters[key])}) for rows in sources]
    if str(filters.get('remaining_hours')) in {'24', '48'}:
        sources = [rows.filter(deadline__lte=now+timedelta(hours=int(filters['remaining_hours']))) for rows in sources]
    ids = str(filters.get('delivery_address_id__in') or '').split('__')
    prefixes = [str(ref.raw['code']).rstrip('0') for ref in ReferenceEntry.objects.filter(
        kind__in=['katos', 'cities'], source_id__in=[int(i) for i in ids if i.isdigit()]) if ref.raw.get('code')]
    sources, info = candidate_sources(sources, spec, prefixes, owner_id, now)
    return sources, prefixes, info
