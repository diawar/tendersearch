from contextlib import ExitStack
from io import StringIO
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from .management.commands.telegram_monitor import Command
from .telegram_service import TelegramError


class TelegramMonitorTests(SimpleTestCase):
    def operations(self, stack):
        operations = {}
        for name in ('poll_updates','schedule_profiles','deliver_pending','schedule_status_checks','deliver_status_notifications'):
            mock = stack.enter_context(patch('tender_search.core.management.commands.telegram_monitor.' + name))
            mock.__name__ = name
            operations[name] = mock
        return operations

    @override_settings(TELEGRAM_POLLING_ENABLED=False)
    def test_send_only_never_receives_updates(self):
        with ExitStack() as stack:
            operations = self.operations(stack)
            Command().cycle()
            operations['poll_updates'].assert_not_called()
            for name in operations.keys() - {'poll_updates'}:
                operations[name].assert_called_once()

    @override_settings(TELEGRAM_POLLING_ENABLED=True)
    def test_poll_conflict_does_not_block_sending_and_has_backoff(self):
        with ExitStack() as stack:
            operations = self.operations(stack)
            operations['poll_updates'].side_effect = TelegramError(409)
            error = StringIO()
            command = Command(stderr=error)
            command.cycle(); command.cycle()
            operations['poll_updates'].assert_called_once()
            self.assertEqual(operations['deliver_status_notifications'].call_count,2)
            self.assertEqual(error.getvalue().count('409'),1)
