from .test_status_watch import set_watch
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch,Mock
from django.contrib.auth import get_user_model
from django.test import TestCase,SimpleTestCase,override_settings
from django.utils import timezone
from .models import CompanySettings,SearchProfile,CollectionLot,LotOutcomeWatch,OutcomeDelivery,UserLotPrice,AnnouncementLotCache,Favorite
from .search_service import store_lot
from .test_index import row
from .outcome_source import Outcome,parse_lot_result,select_current_protocol,ProtocolFormatError
from .outcome_service import register_watch,check_outcome,reset_company,schedule_outcomes,move_closed_unsubmitted,deliver_outcomes

BIN='241040001415'
OTHER='123456789012'

def protocol(winner=BIN,number='101-ЗЦП1',participants=True):
    return f'''Протокол об итогах 100-1
Лот № {number}
№   Наименование поставщика       БИН (ИИН)              Цена
1   ТОО Наша компания              {BIN}            1000
2   ТОО Другая                    {OTHER}            2000
1. Определить победителем по лоту № {number} : {winner} Компания
'''

class ResultParserTests(SimpleTestCase):
    def test_win_loss_are_scoped_to_exact_lot_not_any_company_win(self):
        text=protocol(OTHER)+'\n'+protocol(BIN,'102-ЗЦП1')
        self.assertEqual(parse_lot_result(text,'100-1','101-ЗЦП1',BIN).state,'lost')
        self.assertEqual(parse_lot_result(text,'100-1','102-ЗЦП1',BIN).state,'won')
        self.assertEqual(parse_lot_result(protocol(OTHER),'100-1','101-ЗЦП1','111111111111').state,'not_listed')
    def test_ambiguous_or_wrong_identifiers_never_produce_verdict(self):
        for text,ann,lot in [(protocol(),'999-1','101-ЗЦП1'),(protocol(),'100-1','102-ЗЦП1'),(protocol()+f'\nОпределить победителем по лоту № 101-ЗЦП1 : {OTHER}','100-1','101-ЗЦП1')]:
            with self.assertRaises(ProtocolFormatError):parse_lot_result(text,ann,lot,BIN)
    def test_current_protocol_uses_real_dates_and_exact_valid_status(self):
        html='''<h3>Протокол итогов</h3><table>
<tr><td>1</td><td>31.10.2026 12:00:00</td><td><a href="https://v3bl.goszakup.gov.kz/files/download_file/1/1">Просмотреть протокол</a></td><td>Действительный</td></tr>
<tr><td>2</td><td>02.11.2026 12:00:00</td><td><a href="https://v3bl.goszakup.gov.kz/files/download_file/2/2">Просмотреть протокол</a></td><td>Действительный</td></tr>
<tr><td>3</td><td>03.11.2026 12:00:00</td><td><a href="https://v3bl.goszakup.gov.kz/files/download_file/3/3">Просмотреть протокол</a></td><td>Не действительный</td></tr></table>'''
        selected,_=select_current_protocol(html,'https://old.goszakup.gov.kz/ru/announce/index/100')
        self.assertTrue(selected[1].endswith('/2/2'))

@override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AutomaticWorkflowTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('workflow-owner')
        self.other=get_user_model().objects.create_user('other-workflow-owner')
        self.client.force_login(self.owner)
        CompanySettings.objects.create(owner=self.owner,company_bin=BIN)
        self.profile=SearchProfile.objects.create(owner=self.owner,name='Мои закупки')
        self.lot=store_lot(row(1,announcement_number='100-1',system_id=1))
        self.lot.announcement.external_id=100;self.lot.announcement.save()
        self.lot.refresh_state='done';self.lot.checked_at=timezone.now();self.lot.save()
        self.item=CollectionLot.objects.create(profile=self.profile,lot=self.lot,stage='interesting')
        AnnouncementLotCache.objects.create(pk=self.lot.announcement.source_id,rows=[{'id':self.lot.source_id,'system':{'id':1},'is_combined':False,'plan_items':[{'count':2,'system':{'id':1},'purchase_method':{'id':12}}]}])
    def test_move_to_interesting_stars_own_lot_and_enables_status_watch(self):
        from .models import AnnouncementWatch
        self.item.stage='new';self.item.save()
        other_profile=SearchProfile.objects.create(owner=self.other,name='Чужая подборка')
        other_item=CollectionLot.objects.create(profile=other_profile,lot=self.lot,stage='new')
        with patch('tender_search.core.document_service.queue_documents') as documents, self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(f'/workspace/items/{self.item.pk}/stage/',{'stage':'interesting'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.url,f'/workspace/?profile={self.profile.pk}&stage=all')
        self.item.refresh_from_db();other_item.refresh_from_db()
        self.assertEqual(self.item.stage,'interesting')
        self.assertEqual(other_item.stage,'new')
        self.assertTrue(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertFalse(Favorite.objects.filter(owner=self.other,lot=self.lot).exists())
        self.assertTrue(AnnouncementWatch.objects.filter(owner=self.owner,enabled=True).exists())
        documents.assert_called_once_with(self.lot.pk)
        self.client.post(f'/workspace/items/{self.item.pk}/stage/',{'stage':'interesting'})
        self.assertEqual(Favorite.objects.filter(owner=self.owner,lot=self.lot).count(),1)

    def test_manual_decline_unprocessed_lot_stays_closed_without_star_or_watch(self):
        self.item.stage='new';self.item.save()
        url=f'/workspace/items/{self.item.pk}/stage/'
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url,{'stage':'not_participated'}).status_code,404)
        self.client.force_login(self.owner)
        response=self.client.post(url,{'stage':'not_participated','return_scope':'all','return_sort':'-amount'})
        self.assertEqual(response.url,'/workspace/?profile=all&stage=all&sort=-amount')
        self.item.refresh_from_db()
        self.assertEqual(self.item.stage,'not_participated')
        self.assertEqual(self.item.expired_from_stage,'')
        self.assertFalse(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertFalse(LotOutcomeWatch.objects.filter(owner=self.owner,lot=self.lot).exists())
        move_closed_unsubmitted()
        self.item.refresh_from_db()
        self.assertEqual(self.item.stage,'not_participated')
        page=self.client.get('/workspace/',{'profile':self.profile.pk,'stage':'not_participated'})
        self.assertContains(page,'>В интересные</button>')
        response=self.client.post(url,{'stage':'interesting','return_scope':'all','return_sort':'-amount'})
        self.assertEqual(response.url,'/workspace/?profile=all&stage=not_participated&sort=-amount')
        self.item.refresh_from_db()
        self.assertEqual(self.item.stage,'interesting')
        self.assertEqual(self.item.expired_from_stage,'')
        self.assertTrue(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())

    def test_decline_interesting_stays_in_category_and_keeps_applied_protected(self):
        from .models import AnnouncementWatch
        Favorite.objects.get_or_create(owner=self.owner,lot=self.lot)[0]
        Favorite.objects.get_or_create(owner=self.other,lot=self.lot)[0]
        watch=set_watch(owner=self.owner,lot=self.lot,announcement=self.lot.announcement,enabled=True)
        page=self.client.get('/workspace/',{'profile':self.profile.pk,'stage':'interesting'})
        self.assertContains(page,'>Не интересно</button>')
        url=f'/workspace/items/{self.item.pk}/stage/'
        response=self.client.post(url,{'stage':'not_participated','return_scope':'all','return_sort':'amount'})
        self.assertEqual(response.url,'/workspace/?profile=all&stage=interesting&sort=amount')
        self.item.refresh_from_db()
        self.assertEqual(self.item.stage,'not_participated')
        self.assertFalse(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertTrue(Favorite.objects.filter(owner=self.other,lot=self.lot).exists())
        watch.refresh_from_db()
        self.assertFalse(watch.enabled)
        self.item.stage='applied';self.item.save()
        self.client.post(url,{'stage':'not_participated'})
        self.item.refresh_from_db()
        self.assertEqual(self.item.stage,'applied')

    def test_decline_cancels_plane_even_when_favorite_is_already_missing(self):
        from .models import AnnouncementWatch, StatusDelivery
        watch=set_watch(owner=self.owner,lot=self.lot,announcement=self.lot.announcement,enabled=True,generation='active',lease='running')
        foreign=set_watch(owner=self.other,lot=self.lot,announcement=self.lot.announcement,enabled=True)
        delivery=StatusDelivery.objects.create(watch=watch,generation='active',event_key='pending-status')
        self.client.post(f'/workspace/items/{self.item.pk}/stage/',{'stage':'not_participated'})
        watch.refresh_from_db();delivery.refresh_from_db();foreign.refresh_from_db()
        self.assertFalse(watch.enabled)
        self.assertEqual(watch.lease,'')
        self.assertEqual(delivery.state,'cancelled')
        self.assertTrue(foreign.enabled)
        self.assertEqual(StatusDelivery.objects.filter(watch=watch,event_key__endswith=':stop').count(),1)
        state=self.client.get('/lots/ui-state/',{'ids':self.lot.pk}).json()['lots'][0]
        self.assertFalse(state['watched'])
        self.assertFalse(state['favorite'])

    def apply(self):
        self.item.stage='applied';self.item.result_tracking=True;self.item.save()
        return register_watch(self.owner.pk,self.lot.pk)
    def run_check(self,result):
        watch=self.apply()
        LotOutcomeWatch.objects.filter(pk=watch.pk).update(lease='lease')
        with patch('tender_search.core.outcome_service.source_client') as source,patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.return_value=result
            check_outcome(watch.pk,'lease')
        return LotOutcomeWatch.objects.get(pk=watch.pk)
    def test_price_first_available_only_in_interesting_then_fixed_and_applied(self):
        page=self.client.get(f'/lots/{self.lot.pk}/')
        self.assertContains(page,'class="lot-price-form"')
        self.assertContains(page,'>Подать цену</button>')
        self.assertNotContains(page,'>Сохранить цену</button>')
        for value in ('', '0', '-2', 'NaN'):
            self.assertEqual(self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':value,'vat_rate':'16'}).status_code,400)
            self.item.refresh_from_db();self.assertEqual(self.item.stage,'interesting')
            self.assertFalse(UserLotPrice.objects.exists());self.assertFalse(LotOutcomeWatch.objects.exists())
        result=self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':'116','vat_rate':'16'})
        self.assertEqual(result.status_code,302)
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'applied');self.assertTrue(self.item.result_tracking)
        saved=UserLotPrice.objects.get(owner=self.owner,lot=self.lot);self.assertTrue(saved.locked_at)
        self.assertTrue(LotOutcomeWatch.objects.filter(owner=self.owner,lot=self.lot,company_bin=BIN).exists())
        for gross in ('232',''):
            self.assertEqual(self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':gross,'vat_rate':'16'}).status_code,403)
        saved.refresh_from_db();self.assertEqual(saved.unit_gross,116)
        self.item.stage='new';self.item.save()
        UserLotPrice.objects.all().delete()
        self.assertNotContains(self.client.get(f'/lots/{self.lot.pk}/'),'class="lot-price-form"')
    def test_uncertain_source_allows_our_gross_price_but_not_claimed_submission_net(self):
        self.lot.announcement.raw={'system':{'id':4}};self.lot.announcement.save()
        page=self.client.get(f'/lots/{self.lot.pk}/')
        self.assertContains(page,'class="lot-price-form"')
        self.assertFalse(page.context['price_policy']['confirmed'])
        self.assertContains(page,'data-price-output="unit_net"')
        self.assertNotContains(page,'За единицу без НДС · для подачи')
        self.assertNotContains(page,'<select name="vat_rate"')
        self.assertContains(page,'type="hidden" name="vat_rate"')
        self.assertEqual(self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':'116','vat_rate':'16'}).status_code,302)
    def test_winner_moves_only_tracked_own_lot_and_notifies_once(self):
        other_profile=SearchProfile.objects.create(owner=self.other,name='Чужая')
        other_item=CollectionLot.objects.create(profile=other_profile,lot=self.lot,stage='applied',result_tracking=True)
        watch=self.run_check(Outcome('won',BIN,'Наша',protocol_url='https://v3bl.goszakup.gov.kz/files/download_file/1/1',fingerprint='a'*64))
        self.item.refresh_from_db();other_item.refresh_from_db()
        self.assertEqual((self.item.stage,self.item.outcome),('contract','won'))
        self.assertEqual(other_item.stage,'applied')
        self.assertEqual(OutcomeDelivery.objects.filter(watch=watch).count(),1)
        LotOutcomeWatch.objects.filter(pk=watch.pk).update(lease='again')
        with patch('tender_search.core.outcome_service.source_client'),patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.return_value=Outcome('won',BIN,'Наша',fingerprint='b'*64)
            check_outcome(watch.pk,'again')
        self.assertEqual(OutcomeDelivery.objects.filter(watch=watch).count(),1)
    def test_review_revokes_automatic_result_but_never_marks_a_loss(self):
        watch=self.run_check(Outcome('won',BIN,'Наша'))
        LotOutcomeWatch.objects.filter(pk=watch.pk).update(lease='review')
        with patch('tender_search.core.outcome_service.source_client'),patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.return_value=Outcome('review')
            check_outcome(watch.pk,'review')
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'contract');self.assertEqual(self.item.outcome,'')
    def test_no_participant_match_is_not_a_loss_and_no_protocol_is_pending(self):
        self.run_check(Outcome('not_listed',OTHER,'Другая'))
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'applied');self.assertEqual(self.item.outcome,'')
    def test_company_settings_are_validated_and_private(self):
        self.assertEqual(self.client.post('/settings/',{'action':'company','company_bin':'bad'}).status_code,200)
        self.assertEqual(CompanySettings.objects.get(owner=self.owner).company_bin,BIN)
        self.client.force_login(self.other)
        self.client.post('/settings/',{'action':'company','company_bin':OTHER})
        self.assertEqual(CompanySettings.objects.get(owner=self.owner).company_bin,BIN)
        self.assertEqual(CompanySettings.objects.get(owner=self.other).company_bin,OTHER)
    def test_company_change_invalidates_queued_old_checks(self):
        watch=self.apply();LotOutcomeWatch.objects.filter(pk=watch.pk).update(lease='old')
        def change_bin(*args):
            reset_company(self.owner.pk,OTHER)
            return Outcome('won',BIN,'Старый БИН')
        with patch('tender_search.core.outcome_service.source_client'),patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.side_effect=change_bin
            check_outcome(watch.pk,'old')
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'applied')
        watch.refresh_from_db();self.assertEqual(watch.company_bin,OTHER);self.assertNotEqual(watch.state,'won')
        self.assertFalse(OutcomeDelivery.objects.exists())
    @override_settings(TELEGRAM_BOT_TOKEN='')
    def test_scheduler_does_not_depend_on_telegram_or_daytime(self):
        watch=self.apply()
        with patch('tender_search.core.tasks.check_lot_outcome.delay') as queued:
            schedule_outcomes();queued.assert_called_once()
    def test_expired_unsubmitted_move_and_reopen_without_touching_applied(self):
        self.lot.announcement.deadline=timezone.now()-timedelta(hours=1);self.lot.announcement.save()
        move_closed_unsubmitted();self.item.refresh_from_db();self.assertEqual(self.item.stage,'interesting');self.assertTrue(self.item.result_tracking)
        self.lot.announcement.deadline=timezone.now()+timedelta(hours=1);self.lot.announcement.save()
        move_closed_unsubmitted();self.item.refresh_from_db();self.assertEqual(self.item.stage,'interesting')
        self.apply();self.lot.announcement.deadline=timezone.now()-timedelta(hours=1);self.lot.announcement.save()
        move_closed_unsubmitted();self.item.refresh_from_db();self.assertEqual(self.item.stage,'applied')

    def test_watch_failure_rolls_back_price_and_stage_together(self):
        with patch('tender_search.core.outcome_service.register_watch',side_effect=RuntimeError('queue unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':'116','vat_rate':'16'})
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'interesting')
        self.assertFalse(UserLotPrice.objects.exists());self.assertFalse(LotOutcomeWatch.objects.exists())

    def test_result_delivery_anytime_private_and_once(self):
        from .models import TelegramSubscription
        watch=self.run_check(Outcome('won',BIN,'Наша'))
        TelegramSubscription.objects.create(owner=self.owner,chat_id=9001,enabled=True)
        TelegramSubscription.objects.create(owner=self.other,chat_id=9002,enabled=True)
        with patch('tender_search.core.telegram_service.in_work_hours',return_value=False),patch('tender_search.core.outcome_service.telegram_api',return_value={'message_id':123}) as send:
            deliver_outcomes();deliver_outcomes()
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1]['chat_id'],9001)
        self.assertEqual(OutcomeDelivery.objects.get(watch=watch).state,'sent')

    def test_transient_source_failure_preserves_confirmed_result(self):
        watch=self.run_check(Outcome('won',BIN,'Наша'))
        LotOutcomeWatch.objects.filter(pk=watch.pk).update(lease='failing')
        with patch('tender_search.core.outcome_service.source_client',side_effect=RuntimeError('network unavailable')):
            check_outcome(watch.pk,'failing')
        watch.refresh_from_db();self.item.refresh_from_db()
        self.assertEqual(watch.state,'won');self.assertEqual(self.item.stage,'contract')
        self.assertTrue(watch.error);self.assertEqual(watch.lease,'')

    def test_migration_keeps_existing_draft_price_and_personal_note(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        from .models import UserLotNote
        saved=UserLotPrice.objects.create(owner=self.owner,lot=self.lot,unit_gross=116,vat_rate=16)
        UserLotNote.objects.create(owner=self.owner,lot=self.lot,text='Личная заметка')
        migration=importlib.import_module('tender_search.core.migrations.0020_automatic_workflow')
        class Editor:
            pass
        editor=Editor();editor.connection=connection
        migration.migrate_workflow(apps,editor)
        saved.refresh_from_db();self.item.refresh_from_db()
        self.assertIsNone(saved.locked_at);self.assertEqual(self.item.stage,'interesting')
        self.assertEqual(UserLotNote.objects.get().text,'Личная заметка')
        self.item.stage='waiting';self.item.save()
        migration.migrate_workflow(apps,editor)
        saved.refresh_from_db();self.item.refresh_from_db()
        self.assertIsNotNone(saved.locked_at);self.assertEqual(self.item.stage,'applied')
        self.assertTrue(self.item.result_tracking)

    def test_star_enters_interesting_but_does_not_reset_applied_lot(self):
        self.item.stage='new';self.item.save()
        Favorite.objects.get_or_create(owner=self.owner,lot=self.lot)[0]
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'interesting')
        self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':'116','vat_rate':'16'})
        Favorite.objects.filter(owner=self.owner,lot=self.lot).delete()
        Favorite.objects.get_or_create(owner=self.owner,lot=self.lot)[0]
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'applied')

    def test_cancel_mark_unlocks_price_stops_watch_and_can_submit_again(self):
        self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':'116'})
        watch=LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot)
        previous=watch.generation
        OutcomeDelivery.objects.create(watch=watch,generation=previous,revision=1,event_key='pending-cancel',payload={})
        page=self.client.get(f'/lots/{self.lot.pk}/')
        self.assertContains(page,'disabled>Подали заявку</button>')
        self.assertContains(page,'Отменить отметку')
        self.assertContains(page,'https://goszakup.gov.kz/')
        with patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            self.assertEqual(self.client.post(f'/lots/{self.lot.pk}/application/cancel/').status_code,302)
            portal.assert_not_called()
        self.item.refresh_from_db();watch.refresh_from_db()
        price=UserLotPrice.objects.get(owner=self.owner,lot=self.lot)
        self.assertEqual(self.item.stage,'interesting');self.assertFalse(self.item.result_tracking)
        self.assertIsNone(price.locked_at);self.assertEqual(price.unit_gross,116)
        self.assertFalse(watch.enabled);self.assertNotEqual(watch.generation,previous)
        self.assertEqual(OutcomeDelivery.objects.get().state,'cancelled')
        self.assertEqual(self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':'232','vat_rate':'0'}).status_code,302)
        price.refresh_from_db();watch.refresh_from_db();self.item.refresh_from_db()
        self.assertEqual((price.unit_gross,price.vat_rate),(232,16))
        self.assertTrue(price.locked_at);self.assertTrue(watch.enabled);self.assertEqual(self.item.stage,'applied')

    def test_cancel_is_private_and_forbids_reverting_confirmed_outcome(self):
        self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':'116'})
        endpoint=f'/lots/{self.lot.pk}/application/cancel/'
        self.assertEqual(self.client.get(endpoint).status_code,405)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(endpoint).status_code,403)
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'applied')
        self.client.force_login(self.owner)
        self.item.stage='won';self.item.save()
        self.assertEqual(self.client.post(endpoint).status_code,403)
        self.item.refresh_from_db();self.assertEqual(self.item.stage,'won')
        self.assertTrue(UserLotPrice.objects.get(owner=self.owner,lot=self.lot).locked_at)

    def test_cancellation_fences_a_running_result_check(self):
        from .outcome_service import cancel_application
        self.client.post(f'/lots/{self.lot.pk}/price/',{'unit_gross':'116'})
        watch=LotOutcomeWatch.objects.get(owner=self.owner,lot=self.lot)
        LotOutcomeWatch.objects.filter(pk=watch.pk).update(lease='running')
        def cancel_during_read(*args):
            cancel_application(self.owner,self.lot)
            return Outcome('won',BIN,'Наша')
        with patch('tender_search.core.outcome_service.source_client'),patch('tender_search.core.outcome_service.OutcomePortal') as portal:
            portal.return_value.outcome.side_effect=cancel_during_read
            check_outcome(watch.pk,'running')
        self.item.refresh_from_db();watch.refresh_from_db()
        self.assertEqual(self.item.stage,'interesting');self.assertFalse(watch.enabled)
        self.assertFalse(OutcomeDelivery.objects.exists())
