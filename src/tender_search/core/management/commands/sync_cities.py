from django.core.management import BaseCommand, CommandError
from tender_search.core.models import ReferenceEntry
from tender_search.core.source import source_client

class Command(BaseCommand):
    help = "Load city KATO entries and associate them with top-level regions."
    def handle(self, *args, **options):
        regions = list(ReferenceEntry.objects.filter(kind="katos"))
        count = 0
        with source_client() as api:
            for offset in range(0, 1000, 100):
                data = api.get_json("/api/core/api/dicts/katos/", {"q": "г.", "limit": 100, "offset": offset})
                for row in data["result"]:
                    name = row.get("name_ru") or row.get("name", "")
                    if not (name.casefold().startswith("г.") or (row.get("level") == 2 and name.casefold().endswith("г.а."))):
                        continue
                    region = next((r for r in regions if str(r.raw.get("code", ""))[:2] == str(row.get("code", ""))[:2]), None)
                    row["region_id"] = region.source_id if region else None
                    ReferenceEntry.objects.update_or_create(kind="cities", source_id=row["id"], defaults={"name": row.get("full_name_ru") or name, "raw": row})
                    count += 1
                if not data.get("next"):
                    break
            else:
                raise CommandError("City dictionary exceeds expected size")
        for region in regions:
            if region.name.startswith("г."):
                raw = dict(region.raw, region_id=region.source_id)
                ReferenceEntry.objects.update_or_create(kind="cities", source_id=region.source_id, defaults={"name": region.name, "raw": raw})
        self.stdout.write(f"Cities updated: {count}")
