from unittest.mock import patch
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Favorite,AnnouncementWatch,StatusDelivery,TelegramSubscription,WebSearch,SearchHit
from .search_service import store_lot
from .status_monitor import check_status,deliver_status_notifications

def set_watch(**values):
    owner=values.pop('owner');announcement=values.pop('announcement')
    return AnnouncementWatch.objects.update_or_create(owner=owner,announcement=announcement,defaults=values)[0]

@override_settings(TELEGRAM_BOT_TOKEN='test-only',STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class StatusWatchTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user('watch-owner')
        self.other=get_user_model().objects.create_user('watch-other')
        self.client.force_login(self.owner)
        TelegramSubscription.objects.create(owner=self.owner,chat_id=12345,enabled=True)
        self.lot=store_lot({'id':500,'announcement_id':1000,'announcement_number':'1000-1','lot_number':'500-1','name_ru':'Ноутбук','total_price':50000,'customers':[]})
        self.url=f'/lots/{self.lot.pk}/status-watch/'
    def enable(self):
        self.client.post(f'/lots/{self.lot.pk}/favorite/',{'action':'add'})
        return AnnouncementWatch.objects.get(owner=self.owner)
    def scan(self,card):
        watch=AnnouncementWatch.objects.get(owner=self.owner)
        AnnouncementWatch.objects.filter(pk=watch.pk).update(lease='test')
        with patch('tender_search.core.status_monitor.in_work_hours',return_value=True),patch('tender_search.core.status_monitor.ZakupClient') as api:
            api.return_value.__enter__.return_value.announcement.return_value=card
            check_status(watch.pk,'test')
    def test_star_enters_interesting_and_enables_notifications(self):
        self.client.post(f'/lots/{self.lot.pk}/favorite/',{'action':'add'})
        self.assertTrue(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.assertTrue(AnnouncementWatch.objects.filter(owner=self.owner,enabled=True).exists())
    def test_plane_adds_favorite_and_is_idempotent(self):
        self.enable();self.enable()
        self.assertEqual(AnnouncementWatch.objects.filter(owner=self.owner,enabled=True).count(),1)
        self.assertTrue(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
        self.client.post(f'/lots/{self.lot.pk}/favorite/',{'action':'remove'})
        self.assertFalse(AnnouncementWatch.objects.get(owner=self.owner).enabled)
        self.assertFalse(Favorite.objects.filter(owner=self.owner,lot=self.lot).exists())
    def test_favorite_removal_stops_watch_and_pending_delivery(self):
        watch=self.enable()
        StatusDelivery.objects.create(watch=watch,generation=watch.generation,event_key='test',payload={})
        self.client.post(f'/lots/{self.lot.pk}/favorite/',{'action':'remove'})
        watch.refresh_from_db();self.assertFalse(watch.enabled)
        self.assertEqual(StatusDelivery.objects.get(event_key='test').state,'cancelled')
    def test_initial_confirmation_and_only_changed_status(self):
        self.enable()
        self.scan({'id':1000,'status':{'id':6,'name':'Published'}})
        self.assertFalse(StatusDelivery.objects.exists())
        self.scan({'id':1000,'status':{'id':6,'name':'Published'}})
        self.assertFalse(StatusDelivery.objects.exists())
        self.scan({'id':1000,'status':{'id':10,'name':'Completed'}})
        self.assertEqual(StatusDelivery.objects.count(),1)
        changed=StatusDelivery.objects.get().payload['text']
        self.assertIn('Published',changed);self.assertIn('Completed',changed)
        self.scan({'id':1000,'status':{'id':6,'name':'Published'}})
        self.assertEqual(StatusDelivery.objects.count(),2)

    def test_failed_check_keeps_last_status(self):
        watch=self.enable()
        AnnouncementWatch.objects.filter(pk=watch.pk).update(last_status='6',last_status_name='Опубликован',lease='test')
        with patch('tender_search.core.status_monitor.in_work_hours',return_value=True),patch('tender_search.core.status_monitor.ZakupClient') as api:
            api.return_value.__enter__.return_value.announcement.side_effect=RuntimeError('not logged')
            check_status(watch.pk,'test')
        watch.refresh_from_db();self.assertEqual(watch.last_status,'6');self.assertTrue(watch.error)
        self.assertFalse(StatusDelivery.objects.exists())
    def test_no_access_to_other_users_watch(self):
        self.enable();self.client.force_login(self.other)
        self.client.post(f'/lots/{self.lot.pk}/favorite/',{'action':'remove'})
        self.assertTrue(AnnouncementWatch.objects.get(owner=self.owner).enabled)
        self.assertEqual(self.client.get(self.url).status_code,405)
        response=self.client.post(self.url,{'action':'on'},HTTP_HX_REQUEST='true')
        self.assertContains(response,'Уведомления определяются этапом')
        self.assertFalse(Favorite.objects.filter(owner=self.other).exists())
    def test_delivery_not_repeated_and_not_sent_after_remove(self):
        self.enable();self.scan({'id':1000,'status':{'id':6,'name':'Published'}})
        self.scan({'id':1000,'status':{'id':10,'name':'Completed'}})
        with patch('tender_search.core.status_monitor.telegram_api',return_value={'message_id':3}) as api:
            deliver_status_notifications();deliver_status_notifications();api.assert_called_once()
        self.scan({'id':1000,'status':{'id':6,'name':'Published'}})
        Favorite.objects.filter(owner=self.owner,lot=self.lot).delete()
        with patch('tender_search.core.status_monitor.telegram_api') as api:
            deliver_status_notifications();api.assert_not_called()

    def test_status_check_and_confirmation_work_at_night(self):
        watch=self.enable()
        AnnouncementWatch.objects.filter(pk=watch.pk).update(lease='test',last_status='6',last_status_name='Published')
        with patch('tender_search.core.status_monitor.in_work_hours',return_value=False),patch('tender_search.core.status_monitor.ZakupClient') as api,patch('tender_search.core.status_monitor.telegram_api',return_value={'message_id':5}) as send:
            api.return_value.__enter__.return_value.announcement.return_value={'id':1000,'status':{'id':10,'name':'Completed'}}
            check_status(watch.pk,'test');deliver_status_notifications()
            api.assert_called_once();send.assert_called_once()
            self.assertIn('Completed',send.call_args.args[1]['text'])
            self.client.post(f'/lots/{self.lot.pk}/favorite/',{'action':'remove'})
            deliver_status_notifications();self.assertEqual(send.call_count,1)

    def test_table_has_four_columns_and_actions_in_first(self):
        job=WebSearch.objects.create(owner=self.owner,specification={'terms':['ноут']},status='done')
        SearchHit.objects.create(search=job,lot=self.lot,terms=['ноут'])
        response=self.client.get(f'/search/{job.pk}/')
        self.assertNotContains(response,'<th>Избранное</th>')
        self.assertContains(response,'status-watch-icon')

    def test_favorites_table_and_personal_sidebar_count(self):
        self.enable()
        response = self.client.get('/favorites/')
        self.assertContains(response, 'favorite-icon selected')
        self.assertContains(response, '<th>Даты заявки</th>')
        self.assertContains(response, 'id="sidebar-favorite-count">(1)</span>')
        self.client.post(f'/lots/{self.lot.pk}/favorite/', {'action': 'remove', 'next': '/favorites/'})
        response = self.client.get('/favorites/')
        self.assertContains(response, 'id="sidebar-favorite-count">(0)</span>')
        self.assertFalse(AnnouncementWatch.objects.get(owner=self.owner).enabled)

    def test_stop_confirmation_once_and_stale_stop_cancelled_after_reenable(self):
        watch=self.enable()
        self.client.post(f'/lots/{self.lot.pk}/favorite/',{'action':'remove'})
        self.assertFalse(StatusDelivery.objects.exists())
        StatusDelivery.objects.create(watch=watch,generation=watch.generation,event_key=f'{watch.pk}:{watch.generation}:stop',payload={})
        self.enable()
        with patch('tender_search.core.status_monitor.telegram_api') as api:
            deliver_status_notifications();api.assert_not_called()
        self.assertEqual(StatusDelivery.objects.get().state,'cancelled')

    def test_favorite_sorting_and_added_column(self):
        second=store_lot({'id':501,'announcement_id':1001,'announcement_number':'1001-1','lot_number':'501-1','name_ru':'Monitor','total_price':10000,'customers':[]})
        first=Favorite.objects.create(owner=self.owner,lot=self.lot)
        Favorite.objects.filter(pk=first.pk).update(created_at=timezone.now()-timedelta(days=1))
        Favorite.objects.create(owner=self.owner,lot=second)
        self.lot.status='AAA';self.lot.save(update_fields=['status'])
        second.status='ZZZ';second.save(update_fields=['status'])
        for sort,expected in [('amount',second.pk),('-amount',self.lot.pk),('added',self.lot.pk),('-added',second.pk),('status',self.lot.pk),('-status',second.pk)]:
            response=self.client.get('/favorites/',{'sort':sort})
            self.assertEqual(response.context['page'][0].pk,expected)
            self.assertContains(response,'favorite-added')
        response=self.client.get('/favorites/',{'sort':'invalid'})
        self.assertEqual(response.context['favorite_sort'],'-added')
