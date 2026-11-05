from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from tender_search.client import SourceError
from .models import MedicalIndex, MedicalIndexedLot, ProcurementIndex, WebSearch, Lot, SearchProfile, ReferenceEntry, TelegramDelivery
from .medical_index import collect_one, coverage
from .index_service import execute_local_search
from .search_service import store_lot, lot_identity
from .telegram_service import message_for, announcement_key
from .forms import SearchForm
from .test_index import row, put


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class MedicalTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('medical-owner')
        ReferenceEntry.objects.create(kind='purchase-methods', source_id=12, name='Запрос ценовых предложений')
        ReferenceEntry.objects.create(kind='lot-statuses', source_id=7, name='Опубликован')
        self.data = row(_source='med_ecc', status={'id':7, 'name':'Опубликован'},
            purchase_method={'name':'Запрос ценовых предложений'})
        self.spec = {'terms':['flash'], 'exclude':[], 'mode':'any', 'kind':'contains',
                     'filters':{}, 'status_selection':['open:7','open:9']}

    def put_medical(self, data=None):
        data = deepcopy(data or self.data)
        return MedicalIndexedLot.objects.create(source=data['_source'], source_id=data['id'], row=data,
            deadline=data['offer_end_date'], status_id=7, method_id=12, seen_cycle='old')

    def search(self, filters):
        job = WebSearch.objects.create(owner=self.owner, specification=dict(self.spec, filters=filters))
        execute_local_search(job.pk); job.refresh_from_db()
        return job

    def test_filter_exclusions_identity_and_source_links(self):
        put(row())
        self.put_medical()
        self.put_medical(dict(self.data, _source='fms_ecc'))
        job = self.search({})
        self.assertEqual(job.hits.count(), 3)
        self.assertEqual(self.search({'only_medical': True}).hits.count(), 2)
        self.assertEqual(self.search({'only_medical':True,'purchase_method_id__in':'32'}).hits.count(), 0)
        lot = Lot.objects.get(announcement__source='med_ecc')
        self.assertEqual(lot.portal_url, 'https://med.ecc.kz/ru/announce/index/100?tab=lots')
        self.assertNotEqual(announcement_key(lot), announcement_key(Lot.objects.get(announcement__source='fms_ecc')))
        self.spec['exclude'] = ['USB']
        self.assertEqual(self.search({'only_medical': True}).hits.count(), 0)

    def test_unified_fms_dedup_requires_platform_and_external_id(self):
        direct = dict(self.data, _source='fms_ecc')
        unified = row(system_id=7, announcement_external_id=100)
        self.assertEqual(lot_identity(direct), lot_identity(unified))
        self.assertNotEqual(lot_identity(direct), lot_identity(row(system_id=3)))

    def test_profile_checkbox_roundtrip_and_off_by_default(self):
        from .models import TelegramSubscription
        TelegramSubscription.objects.create(owner=self.owner, enabled=True, chat_id=self.owner.pk)
        data = {'terms':'flash','mode':'any','kind':'contains','number_kind':'lot_number','action':'save'}
        self.assertFalse(SearchForm()['only_medical'].value())
        self.client.force_login(self.owner)
        response = self.client.post('/search/', dict(data, only_medical='on'))
        profile = SearchProfile.objects.get(owner=self.owner)
        self.assertTrue(profile.filters['specification']['filters']['only_medical'])
        self.assertTrue(self.client.get(response.url).context['form']['only_medical'].value())
        job = self.search({'only_medical':True})
        self.assertTrue(self.client.get(f'/search/{job.pk}/').context['form']['only_medical'].value())

    def test_cursor_retries_card_without_losing_pending_ids(self):
        api = Mock()
        api.listing.return_value = ([{'id':100,'end':self.data['offer_end_date']}], None)
        api.announcement.side_effect = SourceError('Temporary')
        self.assertFalse(collect_one('med_ecc', api))
        state = MedicalIndex.objects.get()
        self.assertEqual(state.cursor['ids'], [100]); self.assertIsNone(state.completed_at)
        state.next_run_at = None;state.save()
        api.announcement.side_effect = None
        api.announcement.return_value = {'offer_end_date':self.data['offer_end_date'], 'status':{'id':7}}
        api.announcement_lots.return_value = [self.data]
        self.assertTrue(collect_one('med_ecc', api))
        self.assertEqual(api.listing.call_count, 1)
        self.assertEqual(MedicalIndexedLot.objects.count(), 1)
        state.refresh_from_db();self.assertEqual(state.cursor, {'status':1,'page':1})
        api.listing.return_value = ([], None)
        self.assertTrue(collect_one('med_ecc', api))
        state.refresh_from_db();self.assertTrue(state.completed_at);self.assertEqual(state.error,'')
        self.assertFalse(coverage()['ready'])  # second source is not complete

    def test_medical_only_completeness_independent_of_government_index(self):
        self.put_medical()
        for source in ['med_ecc','fms_ecc']:
            MedicalIndex.objects.create(source=source, completed_at=timezone.now())
        self.assertTrue(self.search({'only_medical':True}).complete)
        self.assertFalse(self.search({}).complete)

    def test_telegram_and_background_clients_use_original_platform(self):
        from .source import lot_client
        lot = store_lot(self.data)
        with patch('tender_search.core.source.source_client') as factory:
            lot_client(lot.pk);factory.assert_called_once_with('med_ecc')
        profile = SearchProfile.objects.create(owner=self.owner,name='Медицина',include_terms=['flash'])
        delivery = TelegramDelivery.objects.create(owner=self.owner,profile=profile,lot=lot,announcement_key='medical')
        button = message_for(delivery)['reply_markup']['inline_keyboard'][0][0]
        self.assertEqual(button['url'], lot.portal_url)
        self.assertIn('med.ecc.kz',button['text'])
