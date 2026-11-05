import time
from django.core.management import BaseCommand
from django.db import close_old_connections
from tender_search.core.index_service import collect_index_page
from tender_search.core.models import ProcurementIndex
from tender_search.core.source import source_client


class Command(BaseCommand):
    help = 'Resume the shared active procurement index (24/7).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--pause', action='store_true')
        parser.add_argument('--resume', action='store_true')

    def handle(self, *args, **options):
        state, _ = ProcurementIndex.objects.get_or_create(pk=1)
        if options['pause'] or options['resume']:
            state.paused = options['pause']
            state.save(update_fields=['paused'])
            self.stdout.write('Paused' if state.paused else 'Resumed')
            return
        self.stdout.write('Shared index collector started; source pace remains shared.')
        while True:
            close_old_connections()
            try:
                with source_client() as api:
                    changed = collect_index_page(api)
            except Exception:
                self.stderr.write('Index cycle unavailable; retrying with preserved cursor.')
                changed = False
            if options['once']:
                return
            if not changed:
                time.sleep(60)
