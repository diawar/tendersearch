from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from tender_search.core.archive_retention import purge_archived_lots,drain_file_deletions
from tender_search.core.models import UserLotWorkflow

class Command(BaseCommand):
    help='Remove personal lot data after thirty days in archive.'
    def add_arguments(self,parser):
        parser.add_argument('--dry-run',action='store_true')
        parser.add_argument('--limit',type=int,default=100)
    def handle(self,*args,**options):
        if options['dry_run']:
            count=UserLotWorkflow.objects.filter(stage='archived',archived_at__lte=timezone.now()-timedelta(days=30)).count()
            self.stdout.write(f'Eligible personal archives: {count}. No data changed.')
        else:
            count=purge_archived_lots(limit=max(1,min(options['limit'],1000)))
            drain_file_deletions()
            self.stdout.write(f'Purged personal archives: {count}.')
