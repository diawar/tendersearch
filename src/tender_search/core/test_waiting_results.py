from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.utils import timezone
from .test_automatic_workflow import AutomaticWorkflowTests,BIN,OTHER
from .models import (UserLotWorkflow,CollectionLot,LotOutcomeWatch,Favorite,AnnouncementWatch,
                     DraftQueueItem,ApplicationPreparation,SearchProfile)
from .workflow_state import update_workflow
from .outcome_service import enroll_participation_checks,move_closed_unsubmitted,check_outcome
from .outcome_source import Outcome
from .draft_queue import expire_draft_queue


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
    'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WaitingResultsTests(TestCase):
    def setUp(self):
        AutomaticWorkflowTests.setUp(self)
        self.lot.announcement.deadline=timezone.now()-timedelta(minutes=1)
        self.lot.announcement.save()

    def state(self):return UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot)

    def result(self,outcome):
        watch=LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot)
        LotOutcomeWatch.objects.filter(pk=watch.pk).update(lease='waiting-test')
        with patch('tender_search.core.outcome_service.source_client'),patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.return_value=outcome
            check_outcome(watch.pk,'waiting-test')
        return self.state()

    def test_deadline_moves_all_three_stages_and_preserves_watches_and_memberships(self):
        second=SearchProfile.objects.create(owner=self.owner,name='Вторая')
        CollectionLot.objects.create(profile=second,lot=self.lot)
        for stage in ('preparing','applied'):
            update_workflow(self.owner.pk,self.lot.pk,stage=stage,result_tracking=stage=='applied')
            enroll_participation_checks()
            state=self.state()
            self.assertEqual((state.stage,state.expired_from_stage,state.result_tracking),('waiting_results',stage,True))
            self.assertEqual(set(CollectionLot.objects.filter(profile__owner=self.owner,lot=self.lot).values_list('stage',flat=True)),{'waiting_results'})
            self.assertTrue(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
            self.assertTrue(AnnouncementWatch.objects.filter(owner=self.owner,announcement=self.lot.announcement,enabled=True).exists())
            self.assertTrue(LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot).enabled)

    def test_expired_queue_stops_but_preparation_survives(self):
        update_workflow(self.owner.pk,self.lot.pk,stage='preparing')
        queue=DraftQueueItem.objects.create(owner=self.owner,lot=self.lot,state='running')
        old_generation=queue.generation
        ApplicationPreparation.objects.create(owner=self.owner,lot=self.lot,company_bin=BIN)
        expire_draft_queue();move_closed_unsubmitted()
        queue.refresh_from_db()
        self.assertEqual(queue.state,'cancelled');self.assertNotEqual(queue.generation,old_generation)
        self.assertEqual(self.state().stage,'waiting_results')
        self.assertTrue(ApplicationPreparation.objects.filter(owner=self.owner,lot=self.lot).exists())

    def test_pending_review_and_incomplete_absence_wait_without_reenrollment(self):
        enroll_participation_checks()
        for outcome in (Outcome('pending'),Outcome('review'),Outcome('not_listed',OTHER)):
            self.assertEqual(self.result(outcome).stage,'waiting_results')
        watch=LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot)
        due=watch.next_check_at
        enroll_participation_checks();watch.refresh_from_db()
        self.assertEqual(watch.next_check_at,due)

    def test_verified_results_move_out_of_waiting_and_absence_does_not_reenroll(self):
        for outcome,expected in [(Outcome('won',BIN),'contract'),(Outcome('lost',OTHER),'lost'),
                                 (Outcome('not_listed',OTHER,participants_complete=True),'not_participated')]:
            update_workflow(self.owner.pk,self.lot.pk,stage='preparing',result_tracking=False)
            enroll_participation_checks()
            state=self.result(outcome)
            self.assertEqual(state.stage,expected);self.assertEqual(state.expired_from_stage,'')
        enroll_participation_checks()
        self.assertEqual(self.state().stage,'not_participated')
        self.assertFalse(LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot).enabled)

    def test_extension_restores_original_stage(self):
        for previous in ('preparing','applied'):
            self.lot.announcement.deadline=timezone.now()-timedelta(minutes=1);self.lot.announcement.save()
            update_workflow(self.owner.pk,self.lot.pk,stage=previous,result_tracking=False)
            enroll_participation_checks()
            self.lot.announcement.deadline=timezone.now()+timedelta(days=1);self.lot.announcement.save()
            enroll_participation_checks()
            self.assertEqual(self.state().stage,previous)
            self.assertEqual(self.state().result_tracking,previous=='applied')

    def test_extension_during_result_request_cannot_apply_stale_result(self):
        enroll_participation_checks()
        watch=LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot)
        LotOutcomeWatch.objects.filter(pk=watch.pk).update(lease='race')
        def reopen(*args):
            self.lot.announcement.deadline=timezone.now()+timedelta(days=1);self.lot.announcement.save()
            return Outcome('won',BIN)
        with patch('tender_search.core.outcome_service.source_client'),patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.side_effect=reopen
            check_outcome(watch.pk,'race')
        self.assertEqual(self.state().stage,'waiting_results')
        enroll_participation_checks();self.assertEqual(self.state().stage,'preparing')

    def test_new_and_final_stages_are_not_moved_to_waiting(self):
        for stage in ('new','contract','delivery','payment','won','lost','not_participated','archived'):
            update_workflow(self.owner.pk,self.lot.pk,stage=stage,result_tracking=False,expired_from_stage='')
            move_closed_unsubmitted()
            self.assertEqual(self.state().stage,'not_participated' if stage=='new' else stage)

    def test_workspace_displays_stage_and_no_preparation_actions(self):
        enroll_participation_checks()
        response=self.client.get('/workspace/',{'profile':'all','stage':'waiting_results'})
        self.assertContains(response,'Ожидаем итоги')
        self.assertContains(response,self.lot.name)
        self.assertNotContains(response,'>Подготовить заявку</button>')

    def test_late_confirmed_submission_keeps_waiting_for_results(self):
        enroll_participation_checks()
        AutomaticWorkflowTests.submitted_report(self)
        self.assertEqual(self.state().stage,'waiting_results')
        self.assertEqual(self.state().expired_from_stage,'applied')
        self.assertTrue(self.state().result_tracking)
        self.assertTrue(LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot).enabled)

    def test_scheduler_keeps_checking_waiting_stage(self):
        from .outcome_service import schedule_outcomes
        enroll_participation_checks()
        with patch('tender_search.core.tasks.check_lot_outcome.delay') as task:
            schedule_outcomes()
        task.assert_called_once()

    def test_waiting_stage_rejects_manual_transition(self):
        enroll_participation_checks()
        response=self.client.post(f'/workspace/items/{self.item.pk}/stage/',{'stage':'preparing'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.state().stage,'waiting_results')

    def test_source_failure_preserves_waiting_and_retries_in_five_minutes(self):
        from tender_search.client import SourceError
        enroll_participation_checks()
        watch=LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot)
        LotOutcomeWatch.objects.filter(pk=watch.pk).update(lease='source-failure')
        now=timezone.now()
        with patch('tender_search.core.outcome_service.source_client'),patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.side_effect=SourceError('Источник временно недоступен')
            check_outcome(watch.pk,'source-failure')
        watch.refresh_from_db()
        self.assertEqual(self.state().stage,'waiting_results')
        self.assertTrue(watch.enabled)
        self.assertEqual(watch.lease,'')
        self.assertGreaterEqual(watch.next_check_at,now+timedelta(minutes=5))
        self.assertLess(watch.next_check_at,now+timedelta(minutes=6))
