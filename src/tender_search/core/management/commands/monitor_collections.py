import time
from django.core.management import BaseCommand
from django.db import close_old_connections
from tender_search.core.collections_service import schedule_collections
from tender_search.core.outcome_service import schedule_outcomes, deliver_outcomes, move_closed_unsubmitted
from tender_search.core.contract_service import schedule_contracts


class Command(BaseCommand):
    help = 'Schedule saved collection refreshes around the clock, independently of Telegram.'

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                schedule_collections()
            except Exception:
                self.stderr.write('Collection scheduling unavailable; retrying.')
            for operation in (move_closed_unsubmitted, schedule_outcomes, deliver_outcomes, schedule_contracts):
                try:
                    operation()
                except Exception:
                    self.stderr.write('Outcome monitoring unavailable; retrying.')
            time.sleep(15)
