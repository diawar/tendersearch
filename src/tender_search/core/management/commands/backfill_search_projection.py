from django.core.management import BaseCommand
from django.db import transaction

from tender_search.core.models import IndexedLot, MedicalIndexedLot
from tender_search.core.search_projection import projection


class Command(BaseCommand):
    help = 'Build normalized search fields from saved JSON without source HTTP requests.'

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true', help='Rebuild existing projections as well.')

    def handle(self, *args, **options):
        for model in (IndexedLot, MedicalIndexedLot):
            rows = model.objects.all()
            if not options['all']:
                rows = rows.filter(search_fingerprint__isnull=True)
            batch, count = [], 0
            for record in rows.only('pk', 'row').order_by('pk').iterator(chunk_size=200):
                record.search_text, record.search_meta, record.search_fingerprint = projection(record.row)
                batch.append(record)
                if len(batch) == 200:
                    count += self.save_batch(model, batch)
                    batch = []
            count += self.save_batch(model, batch)
            self.stdout.write(f'{model.__name__}: {count}')

    @staticmethod
    @transaction.atomic
    def save_batch(model, batch):
        # Concurrent collection may update a row while it is being read. The
        # guarded update only publishes a projection of that exact saved JSON.
        count = 0
        for record in batch:
            count += model.objects.filter(pk=record.pk, row=record.row).update(
                search_text=record.search_text, search_meta=record.search_meta,
                search_fingerprint=record.search_fingerprint)
        return count
