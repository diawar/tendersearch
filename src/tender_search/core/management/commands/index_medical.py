import time
from django.core.management.base import BaseCommand
from tender_search.medical import HOSTS
from tender_search.core.medical_index import collect_one
from tender_search.core.source import source_client


class Command(BaseCommand):
    help = 'Collect public MED/FMS lots directly, around the clock.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        while True:
            progressed = False
            for source in HOSTS:
                with source_client(source) as api:
                    progressed = collect_one(source, api) or progressed
            if options['once']:
                return
            time.sleep(1 if progressed else 30)
