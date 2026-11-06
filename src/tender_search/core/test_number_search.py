from django.contrib.auth import get_user_model
from django.test import TestCase
from .forms import SearchForm
from .models import WebSearch, TelegramDelivery, SearchProfile
from .index_service import execute_local_search
from .test_index import put, row
from .search_service import store_lot
from .telegram_service import message_for


class NumberSearchTests(TestCase):
    def test_number_without_keywords_and_other_filters(self):
        owner = get_user_model().objects.create_user('number-search')
        put(row(1, announcement_number='17921264-1', name_ru='Ноутбук'))
        put(row(2, announcement_number='999-1', name_ru='Ноутбук'))
        for kind, number in [('announcement_number', '17921264-1'), ('lot_number', '1-ЗЦП1')]:
            form = SearchForm({'terms': '', 'number': number, 'number_kind': kind, 'mode': 'any'})
            self.assertTrue(form.is_valid(), form.errors)
            spec = form.specification()
            job = WebSearch.objects.create(owner=owner, specification=spec, status='queued')
            execute_local_search(job.pk)
            self.assertEqual(list(job.hits.values_list('lot__source_id', flat=True)), [1])
            spec['exclude'] = ['ноутбук']
            job = WebSearch.objects.create(owner=owner, specification=spec, status='queued')
            execute_local_search(job.pk)
            self.assertFalse(job.hits.exists())

    def test_empty_search_rejected(self):
        form = SearchForm({'terms': '', 'number': ' ', 'number_kind': 'lot_number', 'mode': 'any'})
        self.assertFalse(form.is_valid())
        self.assertIn('terms', form.errors)

    def test_notification_labels_both_numbers(self):
        owner = get_user_model().objects.create_user('number-message')
        profile = SearchProfile.objects.create(owner=owner, name='Ноутбуки')
        lot = store_lot(row(1, lot_number='88544080-ЗЦП1', announcement_number='17921264-1'))
        text = message_for(TelegramDelivery(owner=owner, profile=profile, lot=lot))['text']
        self.assertIn('Лот: <code>88544080-ЗЦП1</code>', text)
        self.assertIn('Объявление: <code>17921264-1</code>', text)
        self.assertLess(text.index('88544080-ЗЦП1'), text.index('17921264-1'))
