from urllib.parse import urlsplit,parse_qs
from bs4 import BeautifulSoup
from django.test import TestCase,override_settings
from .models import CollectionLot,SearchProfile,DraftQueueItem
from .search_service import store_lot
from .test_index import row


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WorkspaceNumberSearchTests(TestCase):
    def setUp(self):
        from .test_collections import CollectionsTests
        CollectionsTests.setUp(self)
        self.lot=store_lot(row(50));self.lot.number='88512862-ЗЦП2';self.lot.save(update_fields=['number'])
        self.item=CollectionLot.objects.create(profile=self.profile,lot=self.lot,stage='new')
        another=store_lot(row(51));another.number='99999999-ЗЦП1';another.save(update_fields=['number'])
        CollectionLot.objects.create(profile=self.profile,lot=another,stage='new')

    def test_partial_and_full_number_filter_only_current_owner_profile_and_stage(self):
        other_profile=SearchProfile.objects.create(owner=self.other,name='Чужая подборка')
        other_lot=store_lot(row(52));other_lot.number='88512862-ЗЦП2';other_lot.save(update_fields=['number'])
        CollectionLot.objects.create(profile=other_profile,lot=other_lot,stage='new')
        for query in ('88512862','88512862-ЗЦП2','Лот № 88512862 – ЗЦП2'):
            response=self.client.get('/workspace/',{'stage':'all','lot_number':query})
            self.assertEqual([item.lot_id for item in response.context['page']],[self.lot.pk])
            # Search must not change the global funnel or sidebar counts.
            self.assertEqual(next(s['count'] for s in response.context['steps'] if s['key']=='all'),2)
        self.assertEqual(self.client.get('/workspace/',{'stage':'not_participated','lot_number':'88512862'}).context['page'].paginator.count,0)
        self.assertContains(self.client.get('/workspace/',{'stage':'all','lot_number':'missing'}),'Лоты не найдены')
        own_empty=SearchProfile.objects.create(owner=self.owner,name='Пустая')
        self.assertEqual(self.client.get('/workspace/',{'profile':own_empty.pk,'stage':'all','lot_number':'88512862'}).context['page'].paginator.count,0)

    def test_number_survives_sort_stage_collection_page_and_action_navigation(self):
        for n in range(11):
            lot=store_lot(row(100+n));lot.number=f'88512862-{n}';lot.save(update_fields=['number'])
            CollectionLot.objects.create(profile=self.profile,lot=lot,stage='new')
        response=self.client.get('/workspace/',{'stage':'all','lot_number':'88512862','per_page':10,'sort':'amount'})
        self.assertEqual(len(response.context['page']),10)
        html=BeautifulSoup(response.content,'html.parser')
        for selector in ('.sort-heading[href]','.funnel-step[href]','.collection-tab[href], .collection-tab a[href]','.pagination a[href]'):
            for link in html.select(selector):
                self.assertEqual(parse_qs(urlsplit(link['href']).query)['lot_number'],['88512862'])
        self.assertIsNotNone(html.select_one('#workspace-lot-search[hx-preserve]'))
        self.assertEqual(html.select_one('.page-size-controls input[name=lot_number]')['value'],'88512862')
        self.assertIn('lot_number=88512862',html.select_one('form[action$="/stage/"] input[name=return_to]')['value'])
        reset=html.select_one('#workspace-lot-search a')['href']
        self.assertNotIn('lot_number',parse_qs(urlsplit(reset).query))

    def test_preparation_redirect_and_queue_are_filtered_by_number(self):
        from .workflow_state import update_workflow
        update_workflow(self.owner.pk,self.lot.pk,stage='preparing')
        DraftQueueItem.objects.create(owner=self.owner,lot=self.lot)
        response=self.client.get('/workspace/',{'stage':'preparing','lot_number':'88512862','per_page':50})
        self.assertEqual(response.status_code,302)
        self.assertEqual(parse_qs(urlsplit(response.url).query)['lot_number'],['88512862'])
        response=self.client.get(response.url)
        self.assertEqual([item.lot_id for item in response.context['items']],[self.lot.pk])
        self.assertFalse(self.client.get('/workspace/drafts/',{'lot_number':'99999999'}).context['items'])

    def test_number_is_escaped_and_treated_as_literal_text(self):
        response=self.client.get('/workspace/',{'stage':'all','lot_number':'<script>alert(1)</script>'})
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'<script>alert(1)</script>')
        self.assertContains(response,'&lt;script&gt;')
