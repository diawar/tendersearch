from unittest.mock import Mock
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from tender_search.client import Page, SourceError
from .models import WebSearch, AnnouncementLotCache
from .search_service import execute_search, store_lot
from .tests import sample_lot

class ExtendedDescriptionSearchTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('extended-owner')
        self.computer=sample_lot(21,'Компьютер','Офисный')
        self.mouse=sample_lot(22,'Мышь','Оптическая')
        self.folder=sample_lot(23,'Папка','А4')
        self.rows=[self.computer,self.mouse,self.folder]
        self.api=Mock()
        self.api.announcement_lots.return_value=[
            {'id':21,'plan_items':[{'extra_description_ru':'Комплект: клавиатура, мышь','extra_description_kk':'Пернетақта'}]},
            {'id':22,'plan_items':[{'extra_description_ru':'Мышь оптическая'}]},
            {'id':23,'plan_items':[{'extra_description_ru':'Папка синяя'}]}]
        def pages(**kwargs):
            rows=self.rows if kwargs.get('q') is None else [self.mouse] if kwargs['q']=='мыш' else []
            return iter([Page(len(rows),rows,None)])
        self.api.pages.side_effect=pages
    def run_search(self,**extra):
        spec={'terms':['клавиатур','мыш'],'mode':'any','kind':'contains','exclude':[],'pages':1,'extended_descriptions':True}
        spec.update(extra)
        job=WebSearch.objects.create(owner=self.owner,specification=spec)
        execute_search(job.pk,self.api);job.refresh_from_db()
        return job
    def test_hidden_match_and_no_duplicates_or_sibling_leak(self):
        job=self.run_search()
        self.assertEqual(job.status,'done')
        self.assertEqual(set(job.hits.values_list('lot__source_id',flat=True)),{21,22})
        self.api.announcement_lots.assert_called_once_with(100)
        self.assertIn('клавиатура',job.hits.get(lot__source_id=21).lot.description)
    def test_all_and_exclusions_use_extended_text(self):
        job=self.run_search(mode='all')
        self.assertEqual(list(job.hits.values_list('lot__source_id',flat=True)),[21])
        job=self.run_search(exclude=['комплект'])
        self.assertEqual(list(job.hits.values_list('lot__source_id',flat=True)),[22])
    def test_cache_reused_and_kazakh_description_searchable(self):
        self.run_search()
        self.api.announcement_lots.reset_mock()
        job=self.run_search(terms=['пернетақта'])
        self.assertEqual(list(job.hits.values_list('lot__source_id',flat=True)),[21])
        self.api.announcement_lots.assert_not_called()
    def test_exact_name_mode_does_not_expand_scope(self):
        self.run_search(kind='field',terms=['мышь'])
        self.api.announcement_lots.assert_not_called()
        self.assertTrue(all(call.kwargs['q'] is not None for call in self.api.pages.call_args_list))
    def test_checked_lot_keeps_extended_match_text(self):
        lot=store_lot(self.computer);lot.checked_at=timezone.now();lot.save()
        job=self.run_search()
        self.assertIn('клавиатура',job.hits.get(lot__source_id=21).lot.description)
    def test_lookup_failure_is_visible(self):
        self.api.announcement_lots.side_effect=SourceError('Карточка недоступна')
        job=self.run_search()
        self.assertFalse(job.complete)
        self.assertEqual(job.error,'Карточка недоступна')
