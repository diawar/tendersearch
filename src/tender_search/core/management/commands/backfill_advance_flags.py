from django.core.management.base import BaseCommand
from django.db.models import F, Func, JSONField, Value
from tender_search.core.advances import has_advance
from tender_search.core.models import AnnouncementLotCache, IndexedLot


class Command(BaseCommand):
    help = 'Fill advance flags from cached API responses without contacting the portal.'

    def handle(self, **options):
        cursor = 0
        updated = 0
        while True:
            rows = list(IndexedLot.objects.filter(source_id__gt=cursor)
                        .exclude(row__has_key='_has_advance')
                        .order_by('source_id').values_list('source_id', 'row')[:500])
            if not rows:
                break
            cursor = rows[-1][0]
            announcements = {row.get('announcement_id') for _, row in rows}
            details = {row['id']: row for cache in AnnouncementLotCache.objects.filter(source_id__in=announcements)
                       for row in cache.rows}
            groups = {True: [], False: []}
            for identifier, _ in rows:
                flag = has_advance(details.get(identifier))
                if flag is not None:
                    groups[flag].append(identifier)
            for flag, ids in groups.items():
                # Update only our JSON key, preserving concurrent index refreshes.
                updated += IndexedLot.objects.filter(source_id__in=ids).exclude(row__has_key='_has_advance').update(
                    row=Func(F('row'), Value('{_has_advance}'), Value(flag, output_field=JSONField()),
                             function='jsonb_set', output_field=JSONField()))
        self.stdout.write(f'Advance flags updated: {updated}')
