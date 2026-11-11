from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from .forms import SearchForm
from .index_service import local_matches
from .procurement_groups import runtime_status_filters

class DeadlineConditionTests(TestCase):
    def test_form_persists_relative_condition_and_validates_choices(self):
        form = SearchForm({'terms': 'мыш', 'mode': 'any', 'kind': 'contains',
                           'number_kind': 'lot_number', 'remaining_hours': '24'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.specification()['filters']['remaining_hours'], 24)
        form = SearchForm({'terms': 'мыш', 'mode': 'any', 'remaining_hours': '25'})
        self.assertFalse(form.is_valid())
        self.assertIn('remaining_hours', form.errors)

    def test_exact_window_boundaries_and_relative_refresh(self):
        now = timezone.now()
        spec = {'filters': {'remaining_hours': 24}}
        for hours, expected in [(-1, False), (0, False), (1, True), (24, True), (25, False)]:
            row = {'offer_end_date': (now+timedelta(hours=hours)).isoformat()}
            self.assertEqual(local_matches(row, spec, now, []), expected)
        row = {'offer_end_date': (now+timedelta(hours=25)).isoformat()}
        self.assertTrue(local_matches(row, spec, now+timedelta(hours=2), []))
        self.assertFalse(local_matches({}, spec, now, []))

    def test_runtime_dates_intersect_calendar_conditions_without_mutation(self):
        now = timezone.now()
        filters = {'remaining_hours': 48, 'offer_end_date__lte': (now+timedelta(hours=12)).isoformat()}
        result = runtime_status_filters(filters, [], now)
        self.assertEqual(result['offer_end_date__lte'], filters['offer_end_date__lte'])
        self.assertEqual(result['offer_end_date__gte'], now.isoformat())
        self.assertNotIn('remaining_hours', result)
        self.assertEqual(filters['remaining_hours'], 48)

    def test_sql_sources_advance_window_without_cached_candidate_loss(self):
        from .test_index import row, put
        from .query_search import search_sources
        now = timezone.now()
        put(row(1, offer_end_date=(now+timedelta(hours=25)).isoformat()))
        spec = {'terms': ['flash'], 'filters': {'remaining_hours': 24}}
        sources, _, _ = search_sources(spec, now, 1)
        self.assertFalse(any(source.exists() for source in sources))
        sources, _, _ = search_sources(spec, now+timedelta(hours=2), 1)
        self.assertEqual(sum(source.count() for source in sources), 1)
