import time
from django.conf import settings
from django.core.management import BaseCommand
from tender_search.core.status_monitor import schedule_status_checks, deliver_status_notifications
from tender_search.core.telegram_service import poll_updates, schedule_profiles, deliver_pending, TelegramError

class Command(BaseCommand):
    help = 'Receive Telegram /start links and schedule daytime procurement notifications.'
    def handle(self,*args,**options):
        self.stdout.write('Telegram monitor started (07:00–19:00 Asia/Almaty).')
        while True:
            if not settings.TELEGRAM_BOT_TOKEN:
                time.sleep(30)
                continue
            try:
                poll_updates()
                schedule_profiles()
                deliver_pending()
                schedule_status_checks()
                deliver_status_notifications()
            except TelegramError:
                self.stderr.write('Telegram connection unavailable; retrying without logging credentials.')
                time.sleep(15)
            except Exception:
                self.stderr.write('Monitor cycle failed; retrying.')
                time.sleep(15)
            time.sleep(2)
