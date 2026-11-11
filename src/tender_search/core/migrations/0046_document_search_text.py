import unicodedata
from django.db import migrations, models


def install(apps, schema_editor):
    Version = apps.get_model('core', 'DocumentVersion')
    alias = schema_editor.connection.alias
    batch = []
    for row in Version.objects.using(alias).only('pk', 'text').iterator(chunk_size=100):
        row.search_text = ' '.join(unicodedata.normalize('NFC', row.text or '').split()).casefold()
        batch.append(row)
        if len(batch) == 100:
            Version.objects.using(alias).bulk_update(batch, ['search_text'], batch_size=100)
            batch = []
    if batch:
        Version.objects.using(alias).bulk_update(batch, ['search_text'], batch_size=100)
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('CREATE INDEX tender_document_text_gin ON core_documentversion USING gin (search_text gin_trgm_ops)')
        schema_editor.execute('''CREATE FUNCTION tender_document_text_changed() RETURNS trigger AS $$
            BEGIN
                IF NEW.text IS DISTINCT FROM OLD.text AND NEW.search_text IS NOT DISTINCT FROM OLD.search_text THEN
                    NEW.search_text := NULL;
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql''')
        schema_editor.execute('''CREATE TRIGGER tender_document_projection BEFORE UPDATE OF text ON core_documentversion
            FOR EACH ROW EXECUTE FUNCTION tender_document_text_changed()''')
    elif schema_editor.connection.vendor == 'sqlite':
        schema_editor.execute('''CREATE TRIGGER tender_document_projection AFTER UPDATE OF text ON core_documentversion
            WHEN NEW.text IS NOT OLD.text AND NEW.search_text IS OLD.search_text
            BEGIN UPDATE core_documentversion SET search_text=NULL WHERE id=NEW.id; END''')


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS tender_document_projection ON core_documentversion')
        schema_editor.execute('DROP FUNCTION IF EXISTS tender_document_text_changed()')
        schema_editor.execute('DROP INDEX IF EXISTS tender_document_text_gin')
    elif schema_editor.connection.vendor == 'sqlite':
        schema_editor.execute('DROP TRIGGER IF EXISTS tender_document_projection')


class Migration(migrations.Migration):
    dependencies = [('core', '0045_company_defaults')]
    operations = [
        migrations.AddField(model_name='documentversion', name='search_text',
            field=models.TextField(null=True, editable=False)),
        migrations.RunPython(install, uninstall),
    ]
