from django.core.management.base import BaseCommand
from tender_search.core.models import DocumentVersion
from tender_search.matching import normalize


class Command(BaseCommand):
    help = 'Normalize pending document search projections without downloading files.'

    def handle(self, *args, **options):
        updated = 0
        rows = DocumentVersion.objects.filter(search_text__isnull=True).values_list('pk', 'text')
        for identifier, text in rows.iterator(chunk_size=100):
            # Do not overwrite newer extraction work during a rolling deployment.
            updated += DocumentVersion.objects.filter(pk=identifier, text=text, search_text__isnull=True).update(
                search_text=normalize(text))
        self.stdout.write(f'Document search projections updated: {updated}')
