from django.db import migrations, models


def skip_unavailable(apps, schema_editor):
    alias = schema_editor.connection.alias
    IndexedLot = apps.get_model('core', 'IndexedLot')
    SearchHit = apps.get_model('core', 'SearchHit')
    ids = list(IndexedLot.objects.using(alias).filter(row___details_pending=True).values_list('source_id', flat=True))
    IndexedLot.objects.using(alias).filter(source_id__in=ids).update(current=False, details_skipped=True)
    SearchHit.objects.using(alias).filter(lot__announcement__source='zakup', lot__source_id__in=ids).delete()


class Migration(migrations.Migration):
    dependencies = [('core', '0009_medicalindex_medicalindexedlot')]
    operations = [
        migrations.AddField(model_name='indexedlot', name='details_skipped', field=models.BooleanField(default=False, db_index=True)),
        migrations.RunPython(skip_unavailable, migrations.RunPython.noop),
    ]
