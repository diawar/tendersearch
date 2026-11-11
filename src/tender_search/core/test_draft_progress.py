from types import SimpleNamespace
from django.test import SimpleTestCase
from .draft_progress import steps_for,snapshot_phase

class DraftProgressTests(SimpleTestCase):
    def item(self,state,phase=''):
        return SimpleNamespace(state=state,progress_phase=phase,lot=SimpleNamespace(preparation=SimpleNamespace(data={'product_name':'Товар','manufacturer':'Завод'})))

    def test_pending_list_names_all_steps_and_does_not_claim_portal_creation(self):
        steps=steps_for(self.item('queued'))
        self.assertEqual(len(steps),6)
        self.assertEqual([s['status'] for s in steps],['done','waiting','pending','pending','pending','pending'])

    def test_live_waiting_and_blocked_highlight_same_checkpoint(self):
        for state,color in [('running','active'),('waiting','waiting'),('blocked','error')]:
            steps=steps_for(self.item(state,'lot'))
            self.assertEqual([s['status'] for s in steps],['done','done','done',color,'pending','pending'])
            self.assertTrue(steps[3]['current'])
        self.assertTrue(all(s['status']=='done' for s in steps_for(self.item('complete'))))

    def test_legacy_and_explicit_extension_progress(self):
        self.assertEqual(snapshot_phase({'intent':'fill_account'}),'account')
        self.assertEqual(snapshot_phase({'phase':'description','status':'waiting'}),'description')
        self.assertEqual(snapshot_phase({'lotConfirmed':True}),'description')
        self.assertEqual(snapshot_phase({'number':'123'}),'lot')
        self.assertEqual(snapshot_phase({'phase':'complete','status':'running'},'account'),'account')
        self.assertEqual(snapshot_phase({'status':'complete'}),'complete')


from django.test import TestCase,override_settings
from . import test_draft_queue
from .models import DraftQueueItem

@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DraftProgressPersistenceTests(TestCase):
    def setUp(self):test_draft_queue.DraftQueueTests.setUp(self)

    def test_checkpoint_survives_reload_and_colors_follow_state(self):
        response=test_draft_queue.DraftQueueTests.post_progress(self,status='running',phase='account',lotConfirmed=False,number='',description_saved=False)
        self.assertEqual(response.status_code,200)
        self.item.refresh_from_db();self.assertEqual(self.item.progress_phase,'account')
        self.assertIn('draft-step--active',response.json()['steps_html'])
        self.assertContains(self.client.get('/workspace/drafts/'),'draft-step--active')
        response=test_draft_queue.DraftQueueTests.post_progress(self,status='waiting',phase='account',lotConfirmed=False,number='',description_saved=False)
        self.assertIn('draft-step--waiting',response.json()['steps_html'])
        response=test_draft_queue.DraftQueueTests.post_progress(self,status='blocked',phase='account',lotConfirmed=False,number='',description_saved=False)
        self.assertIn('draft-step--error',response.json()['steps_html'])
