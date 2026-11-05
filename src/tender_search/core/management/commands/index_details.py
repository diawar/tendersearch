import time
from django.core.management import BaseCommand
from django.db import close_old_connections
from tender_search.core.detail_index import refresh_details
from tender_search.core.source import source_client


class Command(BaseCommand):
    help = 'Update queued announcement details independently, using the shared API pace.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write('Announcement detail collector started.')
        while True:
            close_old_connections()
            try:
                with source_client() as api:
                    changed = refresh_details(api)
            except Exception:
                self.stderr.write('Detail queue unavailable; retrying with persisted tasks.')
                changed = False
            if options['once']:
                return
            # RedisGate already enforces the shared one-request-per-second
            # ceiling. An extra success delay would keep a large queue stale.
            if not changed:
                time.sleep(5)
