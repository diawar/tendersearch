from django.core.management import BaseCommand
from tender_search.core.models import ReferenceEntry
from tender_search.core.source import source_client


class Command(BaseCommand):
    help = "Update local labels for procurement filters using public dictionaries."

    def handle(self, *args, **options):
        with source_client() as api:
            for kind in ("lot-statuses", "purchase-methods", "katos"):
                page = api.reference(kind, limit=100)
                count = 0
                for row in page.results:
                    if kind == "katos" and row.get("level") != 1:
                        continue
                    ReferenceEntry.objects.update_or_create(kind=kind, source_id=row["id"], defaults={
                        "name": row.get("full_name_ru") or row.get("name_ru") or row.get("name") or str(row["id"]), "raw": row})
                    count += 1
                self.stdout.write(f"{kind}: {count}")
