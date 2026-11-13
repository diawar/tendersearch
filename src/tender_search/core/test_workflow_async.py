from django.test import TestCase, override_settings
from .models import CollectionLot, SearchProfile, Favorite, UserLotWorkflow
from . import test_collections
from .workflow_state import update_workflow


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WorkflowAsyncTests(TestCase):
    def setUp(self):
        test_collections.CollectionsTests.setUp(self)
        test_collections.CollectionsTests.run_refresh(self)
        self.item=CollectionLot.objects.get(profile=self.profile)
        self.url=f'/workspace/items/{self.item.pk}/stage/'
        self.scope=self.client.get('/account/session/').json()['scope']
        self.headers={'HTTP_X_WORKFLOW_ASYNC':'1','HTTP_X_ACCOUNT_SCOPE':self.scope}

    def test_move_returns_json_and_fresh_table_counts_without_redirect(self):
        response=self.client.post(self.url,{'stage':'preparing','return_to':'/workspace/?profile=all&stage=all&per_page=100'},**self.headers)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(response.json()['scope'],self.scope)
        self.assertEqual(response['Cache-Control'],'no-store, private')
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'preparing')
        self.assertTrue(Favorite.objects.filter(owner=self.owner,lot=self.item.lot).exists())
        page=self.client.get('/workspace/?profile=all&stage=all&per_page=100')
        self.assertNotContains(page,self.item.lot.number)
        interested=self.client.get('/workspace/drafts/?profile=all&per_page=100')
        self.assertContains(interested,self.item.lot.number)
        self.assertContains(interested,'data-workflow-action')

    def test_stale_account_cannot_mutate_even_with_valid_post(self):
        response=self.client.post(self.url,{'stage':'preparing'},HTTP_X_WORKFLOW_ASYNC='1',HTTP_X_ACCOUNT_SCOPE='old-session')
        self.assertEqual(response.status_code,409)
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'new')

    def test_automatic_transition_remains_forbidden(self):
        response=self.client.post(self.url,{'stage':'contract'},**self.headers)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['ok'])
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'new')

    def test_other_users_direct_link_is_still_forbidden(self):
        profile=SearchProfile.objects.create(owner=self.other,name='Other',filters={})
        other=CollectionLot.objects.create(profile=profile,lot=self.item.lot)
        response=self.client.post(f'/workspace/items/{other.pk}/stage/',{'stage':'preparing'},**self.headers)
        self.assertEqual(response.status_code,404)

    def test_archive_and_restore_use_the_same_async_transport(self):
        update_workflow(self.owner.pk,self.item.lot_id,stage='not_participated')
        endpoint=f'/workspace/lots/{self.item.lot_id}/action/'
        for action,stage in [('archive','archived'),('restore','not_participated')]:
            response=self.client.post(endpoint,{'action':action},**self.headers)
            self.assertEqual(response.status_code,200);self.assertTrue(response.json()['ok'])
            self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.item.lot).stage,stage)

    def test_no_script_fallback_still_redirects(self):
        response=self.client.post(self.url,{'stage':'preparing'})
        self.assertEqual(response.status_code,302)

    def test_star_updates_workflow_without_redirect_too(self):
        response=self.client.post(f'/lots/{self.item.lot_id}/favorite/',{'action':'add'},**self.headers)
        self.assertEqual(response.status_code,200);self.assertTrue(response.json()['ok'])
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'preparing')

    def test_fast_response_has_confirmed_counts_and_lot_stage(self):
        response=self.client.post(self.url,{'stage':'preparing'},HTTP_X_WORKSPACE_PROFILE='all',**self.headers)
        state=response.json()['workspace']
        self.assertEqual(state['counts']['preparing'],1)
        self.assertEqual(state['counts']['all'],0)
        self.assertEqual(state['favorite_count'],1)
        self.assertEqual(state['lots'],[{'lot_id':self.item.lot_id,'stage':'preparing'}])

    def test_inaccessible_counter_scope_is_rejected_before_mutation(self):
        other_profile=SearchProfile.objects.create(owner=self.other,name='Other',filters={})
        response=self.client.post(self.url,{'stage':'preparing'},HTTP_X_WORKSPACE_PROFILE=str(other_profile.pk),**self.headers)
        self.assertEqual(response.status_code,404)
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'new')

    def test_scoped_counts_deduplicate_same_lot_in_multiple_collections(self):
        second=SearchProfile.objects.create(owner=self.owner,name='Second',filters={})
        CollectionLot.objects.create(profile=second,lot_id=self.item.lot_id)
        response=self.client.post(self.url,{'stage':'preparing'},HTTP_X_WORKSPACE_PROFILE='all',**self.headers)
        self.assertEqual(response.json()['workspace']['counts']['preparing'],1)
        response=self.client.post(self.url,{'stage':'not_participated'},HTTP_X_WORKSPACE_PROFILE=str(second.pk),**self.headers)
        self.assertEqual(response.json()['workspace']['counts']['not_participated'],1)
        self.assertEqual(response.json()['workspace']['favorite_count'],0)

    def test_measure_confirmed_move_separately_from_rendering_100_lots(self):
        import time
        from .models import Lot
        source=self.item.lot
        lots=Lot.objects.bulk_create([Lot(announcement=source.announcement,source_id=50000+i,
            number=f'BENCH-{i}',name=source.name,customer=source.customer,amount=source.amount,
            status=source.status,raw=source.raw) for i in range(100)])
        CollectionLot.objects.bulk_create([CollectionLot(profile=self.profile,lot=lot,matches_filter=True) for lot in lots])
        target='/workspace/?profile=all&stage=all&per_page=100'
        self.client.get(target)  # Warm imports/templates; no absolute timing assertion.
        start=time.perf_counter()
        reply=self.client.post(self.url,{'stage':'preparing'},HTTP_X_WORKSPACE_PROFILE='all',**self.headers)
        post_ms=(time.perf_counter()-start)*1000
        start=time.perf_counter();page=self.client.get(target);table_ms=(time.perf_counter()-start)*1000
        self.assertEqual(reply.json()['workspace']['counts']['all'],100)
        self.assertEqual(page.status_code,200)
        self.assertLess(len(reply.content),len(page.content)//10)
        print(f'Workflow benchmark (test DB, 100 rows): POST {post_ms:.1f} ms / {len(reply.content)} bytes; table GET {table_ms:.1f} ms / {len(page.content)} bytes')


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AsyncPreparationTests(TestCase):
    def test_enqueue_changes_stage_but_never_submits_application(self):
        from .test_extension_transfer import ExtensionTransferTests
        ExtensionTransferTests.setUp(self)
        update_workflow(self.owner.pk,self.lot.pk,stage='preparing')
        self.lot.raw={'system_id':1,'purchase_method_id':12,'_pricing_detail':{'id':self.lot.source_id,'is_combined':False,'plan_items':[{'id':1,'purchase_method':{'id':12}}]}}
        self.lot.save()
        scope=self.client.get('/account/session/').json()['scope']
        response=self.client.post('/workspace/drafts/add/',{'lots':str(self.lot.pk)},HTTP_X_WORKFLOW_ASYNC='1',HTTP_X_ACCOUNT_SCOPE=scope)
        self.assertEqual(response.status_code,200);self.assertTrue(response.json()['ok'])
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'preparing')
