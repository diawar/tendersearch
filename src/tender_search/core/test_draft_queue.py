import json
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from . import test_extension_transfer
from .models import DraftQueueItem, ApplicationPreparation, UserLotWorkflow


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DraftQueueTests(TestCase):
    def setUp(self):
        test_extension_transfer.ExtensionTransferTests.setUp(self)
        self.lot.raw={'system_id':1,'purchase_method_id':12,'_pricing_detail':{
            'id':self.lot.source_id,'is_combined':False,'plan_items':[{'id':1,'purchase_method':{'id':12}}]}}
        self.lot.save()
        self.client.post(f'/lots/{self.lot.pk}/preparation/',self.data)
        self.client.post('/workspace/drafts/add/',{'lots':[str(self.lot.pk)]})
        self.item=DraftQueueItem.objects.get(owner=self.owner,lot=self.lot)
        self.base=f'/workspace/drafts/{self.item.pk}/'

    def post_progress(self,**changes):
        snapshot={'status':'complete','lotConfirmed':True,'description_saved':True,'number':'123',
            'observed_at':timezone.now().isoformat(),'company_bin':'241040001415',
            'announcement_number':self.lot.announcement.number,'lot_number':self.lot.number,'message':'Готово',**changes}
        return self.client.post(self.base+'progress/',json.dumps({'generation':str(self.item.generation),'snapshot':snapshot}),content_type='application/json')

    def test_enqueue_is_durable_unique_and_does_not_create_portal_draft(self):
        self.client.post('/workspace/drafts/add/',{'lots':[str(self.lot.pk),str(self.lot.pk)]})
        self.assertEqual(DraftQueueItem.objects.filter(owner=self.owner).count(),1)
        p=ApplicationPreparation.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(p.portal_state,'unconfirmed')
        self.assertContains(self.client.get('/workspace/drafts/'),'Ожидает')
        self.assertContains(self.client.get('/workspace/?stage=interesting'),'form="queue-add"')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'interesting')

    def test_packet_uses_latest_preparation_and_requires_per_lot_consent(self):
        response=self.client.post(self.base+'packet/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['queue_context']['generation'],str(self.item.generation))
        self.assertFalse(response.json()['packet']['repeat_product'])
        self.lot.raw['_pricing_detail']['plan_items'].append({'id':2,'purchase_method':{'id':12}});self.lot.save()
        self.assertEqual(self.client.post(self.base+'packet/').status_code,400)
        self.assertEqual(self.client.post(self.base+'packet/',{'repeat_product':'on'}).status_code,200)
        UserLotWorkflow.objects.filter(owner=self.owner,lot=self.lot).update(stage='applied')
        self.assertEqual(self.client.post(self.base+'packet/',{'repeat_product':'on'}).status_code,400)

    def test_progress_saves_description_without_submitting_and_is_idempotent(self):
        observed=timezone.now().isoformat()
        self.assertEqual(self.post_progress(observed_at=observed).status_code,200)
        self.assertEqual(self.post_progress(observed_at=observed).status_code,200)
        self.item.refresh_from_db();self.assertEqual(self.item.state,'complete')
        p=ApplicationPreparation.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(p.portal_number,'123');self.assertIsNotNone(p.portal_description_saved_at)
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'interesting')
        self.assertEqual(self.post_progress(status='running').status_code,400)

    def test_progress_cannot_confirm_wrong_lot_or_partial_description(self):
        self.assertEqual(self.post_progress(lot_number='999-ЗЦП1').status_code,400)
        self.assertEqual(self.post_progress(description_saved=False).status_code,400)
        self.assertEqual(self.post_progress(lotConfirmed=False).status_code,400)
        self.item.refresh_from_db();self.assertEqual(self.item.state,'queued')
        self.assertEqual(self.post_progress(status='blocked',message='Сайт недоступен').status_code,200)
        self.assertContains(self.client.get('/workspace/drafts/'),'Сайт недоступен')

    def test_private_queue_and_csrf(self):
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.owner)
        self.assertEqual(strict.post(self.base+'packet/').status_code,403)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get('/workspace/drafts/'),self.lot.number)
        self.assertEqual(self.client.post(self.base+'packet/').status_code,404)
        self.assertEqual(self.post_progress().status_code,400)

    def test_cancelled_generation_cannot_receive_old_browser_reports(self):
        old=str(self.item.generation)
        self.client.post(self.base+'cancel/')
        self.assertEqual(self.post_progress().status_code,400)
        self.client.post('/workspace/drafts/add/',{'lots':[str(self.lot.pk)]})
        self.item.refresh_from_db();self.assertNotEqual(str(self.item.generation),old)
        self.assertEqual(self.item.state,'queued')

    def test_running_item_cannot_be_removed_as_if_stopped(self):
        self.assertEqual(self.post_progress(status='running').status_code,200)
        self.client.post(self.base+'cancel/')
        self.item.refresh_from_db();self.assertEqual(self.item.state,'running')

    def test_partial_draft_keeps_number_without_claiming_description_complete(self):
        self.assertEqual(self.post_progress(status='blocked',description_saved=False,message='Выбор страны не подтверждён').status_code,200)
        p=ApplicationPreparation.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(p.portal_number,'123');self.assertIsNone(p.portal_description_saved_at)
        self.item.refresh_from_db();self.assertEqual(self.item.state,'blocked')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'interesting')

    def test_malformed_progress_and_stale_generation_are_rejected(self):
        for payload in ([],{'snapshot':None},{'snapshot':{},'generation':'old'}):
            self.assertEqual(self.client.post(self.base+'progress/',json.dumps(payload),content_type='application/json').status_code,400)

    def test_changed_company_cannot_reuse_previous_description_confirmation(self):
        from .models import CompanySettings
        observed=timezone.now().isoformat()
        self.assertEqual(self.post_progress(observed_at=observed).status_code,200)
        CompanySettings.objects.filter(owner=self.owner).update(company_bin='123456789012')
        self.assertEqual(self.post_progress(company_bin='123456789012',observed_at=observed).status_code,400)

