import time

from django.core.management import BaseCommand
from django.db import close_old_connections

from tender_search.core.lead_service import collect_lead_round, set_collection_enabled
from tender_search.core.source import source_client


class Command(BaseCommand):
    help = 'Collect public procurement participants and verified business e-mails in the background.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--enable', action='store_true')
        parser.add_argument('--pause', action='store_true')

    def handle(self, *args, **options):
        if options['enable'] or options['pause']:
            set_collection_enabled(options['enable'] and not options['pause'])
            self.stdout.write('Lead collection enabled.' if not options['pause'] else 'Lead collection paused.')
            return
        while True:
            close_old_connections()
            try:
                with source_client() as api:
                    changed = collect_lead_round(api)
            except Exception:
                self.stderr.write('Lead collection cycle unavailable; retrying with preserved progress.')
                changed = False
            if options['once']:
                return
            time.sleep(5 if changed else 30)
