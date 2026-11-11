import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from tender_search.core.coverage_snapshot import refresh_snapshot


class Command(BaseCommand):
    help = 'Refresh shared administrative procurement counters outside page requests.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                refresh_snapshot()
            except Exception:
                if options['once']:
                    raise
                self.stderr.write('Coverage refresh unavailable; retaining the previous snapshot and retrying.')
            if options['once']:
                return
            time.sleep(60)
