from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from tender_search.client import Page
from .forms import SearchForm
from .models import ReferenceEntry, WebSearch, SearchProfile
from .procurement_groups import METHOD_GROUPS, grouped_choices, status_matches, runtime_status_filters
from .search_service import execute_search
from .tests import sample_lot

@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ProcurementGroupingTests(TestCase):
    def setUp(self):
        for identifier,name in [(6,'Опубликован'),(7,'Опубликован (прием заявок)'),(9,'Опубликован (прием ценовых предложений)'),(20,'Отменен')]:
            ReferenceEntry.objects.create(kind='lot-statuses',source_id=identifier,name=name)
        for identifier,name in [(12,'Запрос ценовых предложений'),(33,'Запрос ценовых предложений (не ГЗ new)'),(11,'Открытый конкурс')]:
            ReferenceEntry.objects.create(kind='purchase-methods',source_id=identifier,name=name)
        self.owner=get_user_model().objects.create_user('groups-owner')
        self.client.force_login(self.owner)
    def data(self,status=None):
        return {'terms':'клавиатур мыш','mode':'any','kind':'contains','depth':'100','number_kind':'lot_number','status':status or ['open:6','open:7','open:9'],'method':['12']}
    def test_defaults_and_initial_profile_are_distinct(self):
        self.assertEqual(SearchForm()['status'].value(),['open:6','open:7','open:9'])
        self.assertEqual(SearchForm(initial={'status':['6'],'_defaults_version':1})['status'].value(),['6'])
        self.assertEqual(SearchForm()['method'].value(),['12'])
    def test_live_dictionary_snapshot_has_no_missing_or_duplicate_methods(self):
        rows=json.loads((Path(__file__).parent/'fixtures/purchase_methods_20261029.json').read_text(encoding='utf-8'))
        known={row['id'] for row in rows}
        configured=[identifier for _, identifiers in METHOD_GROUPS for identifier in identifiers]
        self.assertEqual(set(configured),known)
        self.assertEqual(len(configured),len(set(configured)))
        options=[SimpleNamespace(source_id=row['id'],name=row['name']) for row in rows]
        options.append(SimpleNamespace(source_id=99999,name='Новый способ источника'))
        groups=grouped_choices(options,'method')
        flattened=[item for _,items in groups for item in items]
        self.assertEqual(len(flattened),len(rows)+1)
        self.assertIn(('Прочие',[('99999','Новый способ источника')]),groups)
        self.assertEqual(dict(flattened)['40'],'Тендер с применением особого порядка')
    def test_special_procedures_are_not_selected_with_ordinary_methods(self):
        groups=dict(METHOD_GROUPS)
        self.assertEqual(groups['Запрос ценовых предложений'],{12})
        self.assertTrue({33,62,63}.isdisjoint(groups['Запрос ценовых предложений']))
        self.assertNotIn(40,groups['Тендеры'])
        self.assertIn(40,groups['Особый и специальный порядок'])
        self.assertTrue({8,15,64}.isdisjoint(groups['Аукционы']))
        self.assertEqual(groups['Товарные биржи'],{8})
        self.assertEqual(groups['Организованные электронные торги'],{15})
        self.assertEqual(groups['Централизованные торги электрической энергией'],{64})
    def test_method_profile_restores_exact_ids_after_regrouping(self):
        data=dict(self.data(),method=['12','33','11'],action='save')
        self.client.post('/search/',data)
        profile=SearchProfile.objects.get(owner=self.owner)
        response=self.client.get('/search/',{'profile':profile.pk})
        for identifier in data['method']:
            self.assertContains(response,f'name="method" value="{identifier}" checked')
        self.assertEqual(profile.filters['specification']['filters']['purchase_method_id__in'],'12__33__11')
    def test_tokens_expand_and_profile_remains_stable(self):
        form=SearchForm(self.data())
        self.assertTrue(form.is_valid(),form.errors)
        spec=form.specification()
        self.assertEqual(spec['filters']['status_id__in'],'6__7__9')
        self.assertEqual(spec['status_selection'],self.data()['status'])
        self.assertNotIn('offer_end_date__gte',spec['filters'])
        self.assertEqual(spec,form.specification())
        self.client.post('/search/',dict(self.data(),action='save'))
        profile=SearchProfile.objects.get(owner=self.owner)
        self.assertEqual(profile.filters['form']['status'],self.data()['status'])
        response=self.client.get('/search/',{'profile':profile.pk})
        self.assertContains(response,'value="open:9" checked')
    def test_time_window_and_mixed_selections(self):
        now=timezone.now()
        row={'status':{'id':9},'offer_start_date':(now-timedelta(hours=1)).isoformat(),'offer_end_date':(now+timedelta(hours=1)).isoformat()}
        self.assertTrue(status_matches(row,['open:9'],now))
        self.assertFalse(status_matches(dict(row,offer_end_date=now.isoformat()),['open:9'],now))
        future=dict(row,offer_start_date=(now+timedelta(minutes=10)).isoformat())
        self.assertFalse(status_matches(future,['open:9'],now))
        self.assertTrue(status_matches(future,['open:9','upcoming:9'],now))
        self.assertTrue(status_matches(dict(row,status={'id':20}),['open:9','20'],now))
        self.assertFalse(status_matches(dict(row,offer_end_date=None),['open:9'],now))
    def test_runtime_cutoff_and_archived_selection(self):
        now=timezone.now()
        self.assertEqual(runtime_status_filters({},['open:9'],now)['offer_end_date__gte'],now.isoformat())
        future=(now+timedelta(days=1)).isoformat()
        self.assertEqual(runtime_status_filters({'offer_end_date__gte':future},['open:9'],now)['offer_end_date__gte'],future)
        self.assertNotIn('offer_end_date__gte',runtime_status_filters({},['open:9','20'],now))
    def test_expired_source_rows_are_locally_excluded(self):
        now=timezone.now()
        fresh=dict(sample_lot(51,'Клавиатура'),status={'id':9},offer_start_date=(now-timedelta(hours=1)).isoformat(),offer_end_date=(now+timedelta(days=1)).isoformat())
        expired=dict(fresh,id=52,lot_number='52-ЗЦП1',offer_end_date=(now-timedelta(days=1)).isoformat())
        api=Mock();api.pages.side_effect=lambda **kw:iter([Page(2,[fresh,expired],None)])
        api.announcement_lots.return_value=[{'id':51,'plan_items':[]},{'id':52,'plan_items':[]}]
        form=SearchForm(self.data());self.assertTrue(form.is_valid(),form.errors)
        job=WebSearch.objects.create(owner=self.owner,specification=form.specification())
        execute_search(job.pk,api)
        self.assertEqual(list(job.hits.values_list('lot__source_id',flat=True)),[51])
        self.assertTrue(all('offer_end_date__gte' in call.kwargs for call in api.pages.call_args_list))
