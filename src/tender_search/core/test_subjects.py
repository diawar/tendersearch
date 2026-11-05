from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from .forms import SearchForm
from .models import WebSearch, SearchProfile
from .index_service import execute_local_search
from .subjects import subject_ids, subject_matches
from .test_index import row, put


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SubjectTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('subjects-owner')

    def test_legacy_modes_use_keywords_in_form_and_saved_profile(self):
        from .models import TelegramSubscription
        TelegramSubscription.objects.create(owner=self.owner, enabled=True, chat_id=self.owner.pk)
        from unittest.mock import patch
        self.client.force_login(self.owner)
        expected = ['беспроводная', 'мышь', 'игровая', 'клавиатура']
        for old_kind in ('contains', 'word', 'phrase', 'field'):
            data = {'terms': 'беспроводная мышь; игровая клавиатура', 'kind': old_kind,
                    'mode': 'any', 'number_kind': 'lot_number', 'action': 'search'}
            form = SearchForm(data, initial={'kind': old_kind})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.specification()['kind'], 'contains')
            self.assertEqual(form.specification()['terms'], expected)
            self.assertEqual(list(form.fields['kind'].choices), [('contains', 'Ключевое слово')])
            with patch('tender_search.core.views.tasks.web_search.delay'):
                response = self.client.post('/search/', data)
            WebSearch.objects.filter(owner=self.owner).update(status='done')
            self.assertEqual(self.client.get(response.url).context['form']['kind'].value(), 'contains')
        self.client.post('/search/', dict(data, action='save'))
        profile = SearchProfile.objects.get(owner=self.owner)
        self.assertEqual(profile.filters['form']['kind'], 'contains')
        self.assertEqual(profile.filters['specification']['terms'], expected)

    def test_unknowns_pass_each_radio_but_respect_other_filters(self):
        for identifier, subjects in enumerate([[1], [2], [3], [], [0], None, [None], [1,3], [0,2]], 1):
            put(row(identifier, purchase_subject_id=subjects))
        put(row(10, purchase_subject_id=[], name_ru='Ремонт flash'))
        put(row(11, purchase_subject_id=[], total_price=100))
        put(row(12, purchase_subject_id=[], status={'id':18,'name':'Отменён'}))
        for selected, expected in [('',set(range(1,10))),('1',{1,4,5,6,7,8}),('2',{2,4,5,6,7,9}),('3',{3,4,5,6,7,8})]:
            spec={'terms':['flash'], 'exclude':['ремонт'], 'mode':'any', 'kind':'contains',
                  'filters':{'subject':selected, 'status_id__in':'6__7__9','total_price__gte':'1000'},
                  'status_selection':['open:6','open:7','open:9']}
            job=WebSearch.objects.create(owner=self.owner,specification=spec)
            execute_local_search(job.pk);job.refresh_from_db()
            self.assertFalse(job.error)
            self.assertEqual(set(job.hits.values_list('lot__source_id',flat=True)),expected)

    def test_source_types_and_plan_fallback(self):
        self.assertEqual(subject_ids({'purchase_subject_id':['1',0,None,False]}), {'1'})
        self.assertEqual(subject_ids({'purchase_subject_id': [], 'plan_items':[1,{'purchase_subject':{'id':3}}]}), {'3'})
        self.assertEqual(subject_ids({'purchase_subject_id':[2], 'plan_items':[{'purchase_subject':{'id':1}}]}), {'2'})
        for selected in ('1','2','3'):
            self.assertTrue(subject_matches({},selected))
            self.assertTrue(subject_matches({'purchase_subject_id':[0]},selected))
        self.assertFalse(subject_matches({'purchase_subject_id':[3]},'1'))

    def test_profile_and_results_restore_radio_and_old_profiles_default_to_all(self):
        from .models import TelegramSubscription
        TelegramSubscription.objects.create(owner=self.owner, enabled=True, chat_id=self.owner.pk)
        self.client.force_login(self.owner)
        data={'terms':'flash','exclude':'','mode':'any','kind':'contains','number_kind':'lot_number','action':'save'}
        self.assertEqual(SearchForm()['subject'].value(), '')
        form=SearchForm(data);self.assertTrue(form.is_valid(),form.errors)
        self.assertNotIn('subject', form.specification()['filters'])
        response=self.client.post('/search/',dict(data,subject='2'))
        profile=SearchProfile.objects.get(owner=self.owner)
        self.assertEqual(profile.filters['specification']['filters']['subject'],'2')
        response=self.client.get(response.url)
        self.assertEqual(response.context['form']['subject'].value(),'2')
        self.assertContains(response,'type="radio" name="subject" value="2"')
        job=WebSearch.objects.create(owner=self.owner,specification=profile.filters['specification'],status='done')
        self.assertEqual(self.client.get(f'/search/{job.pk}/').context['form']['subject'].value(),'2')
        self.client.post('/search/',dict(data,profile_id=profile.pk,subject=''))
        profile.refresh_from_db();self.assertNotIn('subject',profile.filters['specification']['filters'])
        bad=SearchForm(dict(data,subject='100'))
        self.assertFalse(bad.is_valid())
