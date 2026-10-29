from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo
from django.test import SimpleTestCase
from .deadlines import remaining_time, telegram_remaining_time
from .templatetags.search_tags import deadline_remaining


class DeadlineTests(SimpleTestCase):
    def test_deadline_edges_and_russian_day_labels(self):
        now = datetime(2026, 10, 29, 13, tzinfo=dt_timezone.utc)
        cases = [(-1, 'Приём завершён', False), (0, 'Приём завершён', False),
                 (1, 'Осталось меньше дня', True), (86399, 'Осталось меньше дня', True),
                 (86400, 'Осталось: 1 день', True), (86401, 'Осталось: 2 дня', False),
                 (5*86400, 'Осталось: 5 дней', False), (11*86400, 'Осталось: 11 дней', False),
                 (21*86400, 'Осталось: 21 день', False), (22*86400, 'Осталось: 22 дня', False)]
        for seconds, text, urgent in cases:
            with self.subTest(seconds=seconds):
                end = (now + timedelta(seconds=seconds)).astimezone(ZoneInfo('Asia/Almaty'))
                self.assertEqual(remaining_time(end, now), (text, urgent))
        self.assertEqual(remaining_time(None, now), ('', False))

    def test_web_and_telegram_share_urgent_threshold(self):
        now = datetime(2026, 10, 29, 13, tzinfo=dt_timezone.utc)
        with patch('tender_search.core.deadlines.timezone.now', return_value=now):
            end = now + timedelta(hours=12)
            self.assertIn('deadline-remaining--urgent', deadline_remaining(end))
            self.assertEqual(telegram_remaining_time(end), '🔴 <b>Осталось меньше дня</b>\n')
            self.assertNotIn('urgent', deadline_remaining(now))
            self.assertNotIn('🔴', telegram_remaining_time(now))
            self.assertEqual(deadline_remaining(None), '')
            self.assertEqual(telegram_remaining_time(None), '')
