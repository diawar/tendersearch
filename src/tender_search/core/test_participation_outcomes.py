from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings, SimpleTestCase
from django.utils import timezone
from . import test_automatic_workflow as fixtures
protocol, BIN, OTHER = fixtures.protocol, fixtures.BIN, fixtures.OTHER
from .models import UserLotWorkflow, LotOutcomeWatch
from .workflow_state import update_workflow
from .outcome_service import enroll_participation_checks, check_outcome
from .outcome_source import Outcome, parse_lot_result


class CompletenessTests(SimpleTestCase):
    def test_absence_requires_explicit_matching_count(self):
        text=protocol(OTHER)
        self.assertFalse(parse_lot_result(text,'100-1','101-ЗЦП1','111111111111').participants_complete)
        self.assertTrue(parse_lot_result(text.replace('Лот № 101-ЗЦП1','Лот № 101-ЗЦП1\nКоличество поданных заявок: 2',1),'100-1','101-ЗЦП1','111111111111').participants_complete)
        self.assertFalse(parse_lot_result(text.replace('Лот № 101-ЗЦП1','Лот № 101-ЗЦП1\nКоличество поданных заявок: 3',1),'100-1','101-ЗЦП1','111111111111').participants_complete)


@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ParticipationTests(TestCase):
    def setUp(self):
        fixtures.AutomaticWorkflowTests.setUp(self)
        self.lot.announcement.deadline=timezone.now()-timedelta(minutes=1);self.lot.announcement.save()

    def run_result(self,result):
        enroll_participation_checks()
        w=LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot)
        LotOutcomeWatch.objects.filter(pk=w.pk).update(lease='auto')
        with patch('tender_search.core.outcome_service.source_client'),patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.return_value=result
            check_outcome(w.pk,'auto')
        return UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot)

    def test_no_manual_submission_needed_for_win_or_loss(self):
        state=self.run_result(Outcome('won',BIN,'Our company'))
        self.assertEqual((state.stage,state.outcome),('contract','won'))
        update_workflow(self.owner.pk,self.lot.pk,stage='interesting',outcome='',result_tracking=True)
        state=self.run_result(Outcome('lost',OTHER,'Other company'))
        self.assertEqual((state.stage,state.outcome),('lost','lost'))

    def test_unknown_absence_waits_full_absence_moves(self):
        self.assertEqual(self.run_result(Outcome('not_listed',OTHER)).stage,'interesting')
        result=Outcome('not_listed',OTHER,participants_complete=True)
        state=self.run_result(result)
        self.assertEqual(state.stage,'not_participated');self.assertFalse(state.result_tracking)
        enroll_participation_checks()
        state.refresh_from_db();self.assertEqual(state.stage,'not_participated')

    def test_manual_decline_is_not_reenrolled(self):
        update_workflow(self.owner.pk,self.lot.pk,stage='not_participated',expired_from_stage='',result_tracking=False)
        enroll_participation_checks()
        self.assertFalse(LotOutcomeWatch.objects.filter(owner=self.owner).exists())

    def test_deadline_extension_during_request_discards_result(self):
        enroll_participation_checks()
        w=LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot)
        LotOutcomeWatch.objects.filter(pk=w.pk).update(lease='auto')
        def reopen(*args):
            self.lot.announcement.deadline=timezone.now()+timedelta(days=1)
            self.lot.announcement.save()
            return Outcome('won',BIN)
        with patch('tender_search.core.outcome_service.source_client'),patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.side_effect=reopen
            check_outcome(w.pk,'auto')
        self.assertEqual(UserLotWorkflow.objects.get(owner=self.owner,lot=self.lot).stage,'interesting')

    def test_pending_and_extended_deadline_do_not_mark_nonparticipation(self):
        state=self.run_result(Outcome('pending'))
        self.assertEqual(state.stage,'interesting')
        self.lot.announcement.deadline=timezone.now()+timedelta(days=1);self.lot.announcement.save()
        enroll_participation_checks();state.refresh_from_db()
        self.assertFalse(state.result_tracking)
        self.assertFalse(LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot).enabled)
