import time
from django.conf import settings
from django.core.management import BaseCommand
from tender_search.core.status_monitor import schedule_status_checks, deliver_status_notifications
from tender_search.core.telegram_service import poll_updates, schedule_profiles, deliver_pending, TelegramError


class Command(BaseCommand):
    help = 'Receive Telegram updates and independently send procurement notifications.'

    def cycle(self):
        operations = ([poll_updates] if settings.TELEGRAM_POLLING_ENABLED else []) + [
            schedule_profiles, deliver_pending, schedule_status_checks, deliver_status_notifications]
        if not hasattr(self, '_errors'):
            self._errors, self._retry_at = {}, {}
        for operation in operations:
            name = operation.__name__
            if time.monotonic() < self._retry_at.get(name, 0):
                continue
            try:
                operation()
            except TelegramError as exc:
                # No credentials/URLs; incoming failures must not block outgoing work.
                message = f'{name}: Telegram API {exc.code}'
                if exc.code == 409:
                    message += ' (another receiver or webhook is using this bot)'
                if self._errors.get(name) != message:
                    self.stderr.write(message)
                self._errors[name] = message
                self._retry_at[name] = time.monotonic() + (max(1, exc.retry_after) if exc.code == 429 else 60)
            except Exception:
                message = f'{name}: operation failed; retrying.'
                if self._errors.get(name) != message:
                    self.stderr.write(message)
                self._errors[name] = message
                self._retry_at[name] = time.monotonic() + 15
            else:
                if self._errors.pop(name, None):
                    self.stdout.write(f'{name}: recovered.')
                self._retry_at.pop(name, None)

    def handle(self, *args, **options):
        self.stdout.write('Telegram monitor started; incoming polling ' +
            ('enabled.' if settings.TELEGRAM_POLLING_ENABLED else 'disabled (send-only).'))
        while True:
            if not settings.TELEGRAM_BOT_TOKEN:
                time.sleep(30)
                continue
            self.cycle()
            time.sleep(2)
