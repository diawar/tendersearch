from datetime import timedelta
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from tender_search.client import Page, SourceError
from .models import IndexedLot, ProcurementIndex, WebSearch, SearchProfile, TelegramSubscription, TelegramDelivery, ReferenceEntry
from .index_service import collect_index_page, execute_local_search, index_coverage, claim_index, retry_missing_details
from .forms import SearchForm
from .telegram_service import scan_profile


def row(identifier=1, **changes):
    now=timezone.now()
    return dict({'id':identifier,'announcement_id':100,'announcement_number':'100-1',
        'lot_number':str(identifier)+'-ЗЦП1','name_ru':'Накопитель','description_ru':'USB flash память',
        'status':{'id':9,'name':'Опубликован'},'purchase_method_id':12,'total_price':5000,
        'offer_start_date':(now-timedelta(days=5)).isoformat(),
        'offer_end_date':(now+timedelta(days=5)).isoformat(),
        'announcement_publish_date':(now-timedelta(days=5)).isoformat(),'customers':[]}, **changes)


def put(data):
    return IndexedLot.objects.update_or_create(source_id=data['id'],defaults={
        'row':data,'deadline':data['offer_end_date'],'status_id':data['status']['id'],
        'method_id':data['purchase_method_id'],'indexed_at':timezone.now()})[0]


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class IndexTests(TestCase):
    def test_missing_method_does_not_stop_collection(self):
        api = Mock()
        no_method = row(purchase_method_id=None)
        absent_method = row(2)
        absent_method.pop('purchase_method_id')
        api.search.return_value = Page(2, [no_method, absent_method], None)
        api.announcement_lots.return_value = [no_method, absent_method]
        self.assertTrue(collect_index_page(api))
        self.assertEqual(IndexedLot.objects.filter(method_id__isnull=True).count(), 2)
        self.assertEqual(self.search().hits.count(), 0)
        self.assertEqual(self.search(filters={'status_id__in': '6__7__9'}).hits.count(), 2)

    def test_advance_filter_and_cached_detail_fields(self):
        from .advances import has_advance
        from .index_service import save_index_row
        cases = [({'plan_items': [{'prepayment': 30}]}, True),
                 ({'plan_items': [{'prepayment': 0, 'specifics': [{'prepayment': 30}]}]}, True),
                 ({'plan_items': [{'prepayment': '0'}]}, False),
                 ({'plan_items': [{'prepayment': None}]}, None),
                 ({'plan_items': [{'prepayment': 'NaN'}]}, None), (None, None)]
        for identifier, (detail, expected) in enumerate(cases, 1):
            self.assertIs(has_advance(detail), expected)
            save_index_row(row(identifier), detail, 'test')
        self.assertEqual(self.search().hits.count(), 6)
        with patch('tender_search.client.ZakupClient.search', side_effect=AssertionError('Network forbidden')):
            job = self.search(filters=dict(self.spec['filters'], only_advance=True))
        self.assertEqual(set(job.hits.values_list('lot__source_id', flat=True)), {1, 2})

    def test_advance_checkbox_profile_and_result_roundtrip(self):
        data = {'terms': 'flash', 'mode': 'any', 'kind': 'contains', 'number_kind': 'lot_number'}
        form = SearchForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn('only_advance', form.specification()['filters'])
        self.assertFalse(SearchForm()['only_advance'].value())
        self.client.force_login(self.owner)
        response = self.client.post('/search/', dict(data, only_advance='on', action='save'))
        profile = SearchProfile.objects.get(owner=self.owner)
        self.assertIs(profile.filters['specification']['filters']['only_advance'], True)
        response = self.client.get(response.url)
        self.assertTrue(response.context['form']['only_advance'].value())
        job = self.search(filters=dict(self.spec['filters'], only_advance=True))
        response = self.client.get(f'/search/{job.pk}/')
        self.assertTrue(response.context['form']['only_advance'].value())
        self.client.post('/search/', dict(data, action='save', profile_id=profile.pk))
        profile.refresh_from_db()
        self.assertNotIn('only_advance', profile.filters['specification']['filters'])

    def test_collector_runs_outside_telegram_hours(self):
        from django.core.management import call_command
        from io import StringIO
        module = 'tender_search.core.management.commands.index_procurements'
        with patch('tender_search.core.telegram_service.in_work_hours', return_value=False), \
             patch(module+'.source_client') as source, patch(module+'.collect_index_page', return_value=True) as collect:
            call_command('index_procurements', once=True, stdout=StringIO())
        source.assert_called_once_with()
        collect.assert_called_once_with(source.return_value.__enter__.return_value)

    def setUp(self):
        self.owner=get_user_model().objects.create_user('index-owner')
        self.spec={'terms':['flash'],'exclude':[],'kind':'contains','mode':'any',
            'filters':{'status_id__in':'6__7__9','purchase_method_id__in':'12'},
            'status_selection':['open:6','open:7','open:9'],'backend':'local_index'}

    def search(self, **changes):
        job=WebSearch.objects.create(owner=self.owner,specification=dict(self.spec,**changes))
        execute_local_search(job.pk)
        job.refresh_from_db()
        return job

    def test_local_search_finds_old_hidden_match_without_network_or_depth_limit(self):
        put(row(50000))
        with patch('tender_search.client.ZakupClient.search',side_effect=AssertionError('Network forbidden')):
            job=self.search(depth=1)
        self.assertEqual(job.hits.get().lot.source_id,50000)
        self.assertEqual(job.status,'partial')
        self.assertFalse(job.complete)
        self.assertEqual(job.source_counts['__index__']['loaded'],1)

    def test_completed_and_stale_coverage_are_distinct(self):
        put(row())
        state=ProcurementIndex.objects.create(completed_at=timezone.now(),heartbeat_at=timezone.now())
        self.assertTrue(self.search().complete)
        state.completed_at=timezone.now()-timedelta(hours=7);state.save()
        self.assertFalse(self.search().complete)
        self.assertFalse(self.search(status_selection=['20']).complete)

    def test_filters_and_exclusions_apply_locally(self):
        put(row())
        put(row(2,description_ru='flash услуги'))
        put(row(3,purchase_method_id=33))
        put(row(4,offer_end_date=(timezone.now()-timedelta(hours=1)).isoformat()))
        put(row(5,total_price=99999))
        spec=dict(self.spec['filters'],total_price__lte='6000')
        job=self.search(exclude=['услуг'],filters=spec)
        self.assertEqual(list(job.hits.values_list('lot__source_id',flat=True)),[1])
        self.assertEqual(self.search(mode='all',terms=['flash','клавиатур']).hits.count(),0)
        self.assertEqual(self.search(customer_bin='123456789012').hits.count(),0)

    def test_geography_and_number(self):
        ReferenceEntry.objects.create(kind='katos',source_id=10,name='Область',raw={'code':'190000000'})
        put(row(1,delivery_addresses=[{'id':100,'code':'191010100'}]))
        put(row(2,delivery_addresses=[{'id':200,'code':'390000000'}]))
        filters=dict(self.spec['filters'],delivery_address_id__in='10',lot_number='1-ЗЦП1')
        self.assertEqual(self.search(filters=filters).hits.count(),1)

    def test_cancelled_job_is_never_run(self):
        job=WebSearch.objects.create(owner=self.owner,specification=self.spec,status='cancelled')
        execute_local_search(job.pk);job.refresh_from_db()
        self.assertEqual(job.status,'cancelled')

    def test_form_removes_depth_and_keeps_saved_conditions(self):
        form=SearchForm({'terms':'flash','exclude':'услуг','mode':'any','kind':'contains','number_kind':'lot_number','depth':'10000'})
        self.assertTrue(form.is_valid(),form.errors)
        self.assertNotIn('depth',form.fields)
        self.assertNotIn('depth',form.specification())
        self.assertEqual(form.specification()['backend'],'local_index')
        self.client.force_login(self.owner)
        response=self.client.get('/search/')
        self.assertContains(response,'База активных закупок')
        self.assertNotContains(response,'Глубина поиска на слово')

    def test_page_resume_cache_reuse_and_final_reconciliation(self):
        stale=put(row(99))
        api=Mock()
        api.search.side_effect=[Page(2,[row(1)],'next'),SourceError('Temporary failure'),Page(2,[row(2)],None)]
        api.announcement_lots.return_value=[dict(row(1),plan_items=[{'extra_description_ru':'флешка'}]),row(2)]
        self.assertTrue(collect_index_page(api))
        state=ProcurementIndex.objects.get()
        self.assertEqual(state.pending[0]['offset'],1)
        self.assertFalse(collect_index_page(api))
        state.refresh_from_db();self.assertEqual(state.pending[0]['offset'],1)
        self.assertTrue(collect_index_page(api))
        api.announcement_lots.assert_called_once_with(100)
        state.refresh_from_db();self.assertTrue(state.completed_at)
        stale.refresh_from_db();self.assertFalse(stale.current)
        self.assertIn('флешка',IndexedLot.objects.get(pk=1).row['description_ru'])

    def test_large_window_splits_before_source_limit(self):
        api=Mock();api.search.return_value=Page(32000,[], 'next')
        self.assertTrue(collect_index_page(api))
        state=ProcurementIndex.objects.get()
        self.assertEqual(len(state.pending),2)
        self.assertEqual(state.pending[0]['hi'],state.pending[1]['lo'])
        self.assertIsNone(state.completed_at)
        api.announcement_lots.assert_not_called()

    def test_ignored_filters_and_missing_details_cannot_claim_completion(self):
        api=Mock();api.search.return_value=Page(1,[row(status={'id':20})],None)
        self.assertFalse(collect_index_page(api))
        self.assertIsNone(ProcurementIndex.objects.get().completed_at)
        api.search.return_value=Page(1,[row()],None);api.announcement_lots.return_value=[]
        self.assertTrue(collect_index_page(api))
        self.assertEqual(IndexedLot.objects.count(),1)
        self.assertEqual(index_coverage()['missing_details'],1)
        self.assertFalse(index_coverage()['ready'])
        self.assertIsNone(ProcurementIndex.objects.get().completed_at)

    def test_empty_card_does_not_block_other_rows_and_is_retried(self):
        api=Mock();api.search.return_value=Page(2,[row(1),row(2,announcement_id=101)],None)
        api.announcement_lots.side_effect=[[],[row(2)]]
        self.assertTrue(collect_index_page(api))
        self.assertEqual(IndexedLot.objects.count(),2)
        self.assertEqual(index_coverage()['missing_details'],1)
        self.assertTrue(IndexedLot.objects.get(pk=2).row['_extended_description'])
        state=ProcurementIndex.objects.get()
        state.next_run_at=timezone.now()-timedelta(seconds=1);state.save()
        state=claim_index()
        IndexedLot.objects.filter(pk=1).update(indexed_at=timezone.now()-timedelta(minutes=16))
        api.announcement_lots.side_effect=[SourceError('temporary'),[dict(row(1),plan_items=[{'extra_description_ru':'клавиатура'}])]]
        retry_missing_details(api,state)
        self.assertEqual(index_coverage()['missing_details'],1)
        IndexedLot.objects.filter(pk=1).update(indexed_at=timezone.now()-timedelta(minutes=16))
        retry_missing_details(api,state)
        self.assertEqual(index_coverage()['missing_details'],0)
        self.assertIn('клавиатура',IndexedLot.objects.get(pk=1).row['description_ru'])

    def test_expired_incomplete_row_does_not_block_coverage(self):
        put(row(1,_details_pending=True,offer_end_date=(timezone.now()-timedelta(seconds=1)).isoformat()))
        ProcurementIndex.objects.create(completed_at=timezone.now())
        self.assertEqual(index_coverage()['missing_details'],0)
        self.assertTrue(index_coverage()['ready'])

    def test_lease_and_pause_prevent_parallel_collectors(self):
        state=claim_index()
        self.assertIsNone(claim_index())
        state.lease_until=timezone.now()-timedelta(seconds=1);state.paused=True;state.save()
        self.assertIsNone(claim_index())
        state.paused=False;state.save()
        resumed=claim_index()
        self.assertEqual(resumed.pending,state.pending)
        self.assertNotEqual(resumed.lease,state.lease)

    @override_settings(USE_LOCAL_INDEX=True)
    @patch('tender_search.core.telegram_service.in_work_hours',return_value=True)
    def test_initial_backfill_does_not_send_archive_and_later_new_publication_does(self, working):
        values={'terms':'flash','mode':'any','kind':'contains','number_kind':'lot_number'}
        profile=SearchProfile.objects.create(owner=self.owner,name='Flash',enabled=True,filters={'form':values},monitor_lease='first')
        TelegramSubscription.objects.create(owner=self.owner,enabled=True,chat_id=123)
        put(row(1))
        scan_profile(profile.pk,'first')
        profile.refresh_from_db();self.assertIsNone(profile.index_baseline_at)
        ProcurementIndex.objects.create(completed_at=timezone.now())
        SearchProfile.objects.filter(pk=profile.pk).update(monitor_lease='second')
        scan_profile(profile.pk,'second')
        profile.refresh_from_db();self.assertIsNotNone(profile.index_baseline_at)
        self.assertFalse(TelegramDelivery.objects.exists())
        put(row(2,announcement_id=102,announcement_number='102',announcement_publish_date=(profile.index_baseline_at-timedelta(days=1)).isoformat()))
        put(row(3,announcement_id=103,announcement_number='103',announcement_publish_date=(profile.index_baseline_at+timedelta(seconds=1)).isoformat()))
        SearchProfile.objects.filter(pk=profile.pk).update(monitor_lease='third')
        scan_profile(profile.pk,'third')
        self.assertEqual(list(TelegramDelivery.objects.values_list('lot__source_id',flat=True)),[3])
