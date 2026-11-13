from decimal import Decimal
from django.test import TestCase, override_settings
from django.template.loader import render_to_string
from .lot_pricing import attach_lot_budgets, lot_budget_display
from .models import Lot, SearchHit, WebSearch, Favorite


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class BudgetDisplayTests(TestCase):
    def setUp(self):
        from .test_lot_pricing import LotPricingTests
        LotPricingTests.setUp(self)
        self.lot.amount=Decimal('112110');self.lot.save()
        self.detail['plan_items'][0].update(count=30,unit_price=3737)
        self.cache.rows=[self.detail];self.cache.save()

    def test_cached_cards_are_batched_and_template_never_queries(self):
        lots=list(Lot.objects.select_related('announcement'))
        with self.assertNumQueries(1):attach_lot_budgets(lots)
        with self.assertNumQueries(0):
            html=render_to_string('core/_budget_unit_price.html',{'lot':lots[0]})
        self.assertIn('30 шт.',html)
        self.assertIn('3 737,00',html)
        self.assertIn('без НДС',html)

    def test_snapshot_needs_no_cache_query_and_bad_counts_are_not_guessed(self):
        self.lot.raw['_pricing_detail']=self.detail
        with self.assertNumQueries(0):attach_lot_budgets([self.lot])
        self.assertEqual(lot_budget_display(self.lot)['quantity'],30)
        del self.lot._budget_display
        self.detail['plan_items'][0]['count']=0
        self.assertEqual(lot_budget_display(self.lot),{})
        self.detail['plan_items']*=2
        self.assertTrue(lot_budget_display(self.lot)['multiple'])

    def test_search_favorites_workspace_and_open_lot_show_same_numbers(self):
        from .workflow_state import add_to_preparation
        add_to_preparation(self.owner.pk,self.lot.pk)
        job=WebSearch.objects.create(owner=self.owner,status='done',specification={'terms':['мыш']})
        SearchHit.objects.create(search=job,lot=self.lot,terms=['мыш'])
        for url in [f'/search/{job.pk}/','/favorites/','/workspace/?profile=all&stage=preparing',f'/lots/{self.lot.pk}/']:
            response=self.client.get(url,follow=True)
            self.assertContains(response,'Количество × бюджет за единицу')
            self.assertContains(response,'30 шт.')
            self.assertContains(response,'3 737,00')
