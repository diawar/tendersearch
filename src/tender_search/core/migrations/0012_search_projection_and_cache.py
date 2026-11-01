from django.db import migrations, models


TABLES = [('core_indexedlot', 'source_id', 'tender_idx'),
          ('core_medicalindexedlot', 'id', 'tender_med')]


def install(apps, schema_editor):
    Epoch = apps.get_model('core', 'SearchIndexEpoch')
    Epoch.objects.using(schema_editor.connection.alias).get_or_create(pk=1)
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        schema_editor.execute('''
            CREATE FUNCTION tender_search_invalidate_projection() RETURNS trigger AS $$
            BEGIN
                IF NEW.row IS DISTINCT FROM OLD.row AND
                   NEW.search_fingerprint IS NOT DISTINCT FROM OLD.search_fingerprint THEN
                    NEW.search_text := NULL;
                    NEW.search_meta := NULL;
                    NEW.search_fingerprint := NULL;
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql;
        ''')
        schema_editor.execute('''
            CREATE FUNCTION tender_search_index_changed() RETURNS trigger AS $$
            BEGIN
                INSERT INTO core_searchindexepoch (id, revision) VALUES (1, 1)
                ON CONFLICT (id) DO UPDATE SET revision = core_searchindexepoch.revision + 1;
                RETURN NULL;
            END; $$ LANGUAGE plpgsql;
        ''')
        for table, pk, prefix in TABLES:
            schema_editor.execute(f'CREATE INDEX {prefix}_text_gin ON {table} USING gin (search_text gin_trgm_ops)')
            schema_editor.execute(f'CREATE INDEX {prefix}_meta_gin ON {table} USING gin (search_meta jsonb_path_ops)')
            schema_editor.execute(f'CREATE INDEX {prefix}_unprojected ON {table} ({pk}) WHERE search_text IS NULL')
            schema_editor.execute(f'''CREATE TRIGGER {prefix}_projection BEFORE UPDATE OF row ON {table}
                FOR EACH ROW EXECUTE FUNCTION tender_search_invalidate_projection()''')
            schema_editor.execute(f'''CREATE TRIGGER {prefix}_epoch AFTER INSERT OR UPDATE OR DELETE ON {table}
                FOR EACH STATEMENT EXECUTE FUNCTION tender_search_index_changed()''')
    elif schema_editor.connection.vendor == 'sqlite':
        for table, pk, prefix in TABLES:
            schema_editor.execute(f'''CREATE TRIGGER {prefix}_projection AFTER UPDATE OF row ON {table}
                WHEN NEW.row IS NOT OLD.row AND NEW.search_fingerprint IS OLD.search_fingerprint
                BEGIN UPDATE {table} SET search_text=NULL, search_meta=NULL, search_fingerprint=NULL
                    WHERE {pk}=NEW.{pk}; END''')


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        for table, _, prefix in TABLES:
            for suffix in ('projection', 'epoch'):
                schema_editor.execute(f'DROP TRIGGER IF EXISTS {prefix}_{suffix} ON {table}')
            for suffix in ('text_gin', 'meta_gin', 'unprojected'):
                schema_editor.execute(f'DROP INDEX IF EXISTS {prefix}_{suffix}')
        schema_editor.execute('DROP FUNCTION IF EXISTS tender_search_invalidate_projection()')
        schema_editor.execute('DROP FUNCTION IF EXISTS tender_search_index_changed()')
    elif schema_editor.connection.vendor == 'sqlite':
        for _, _, prefix in TABLES:
            schema_editor.execute(f'DROP TRIGGER IF EXISTS {prefix}_projection')


class Migration(migrations.Migration):
    dependencies = [('core', '0011_index_refresh_cache')]
    operations = [
        *[migrations.AddField(model_name=model, name=name, field=field)
          for model in ('indexedlot', 'medicalindexedlot')
          for name, field in [
              ('search_text', models.TextField(null=True, editable=False)),
              ('search_meta', models.JSONField(null=True, editable=False)),
              ('search_fingerprint', models.CharField(max_length=64, null=True, editable=False))]],
        migrations.CreateModel(name='SearchIndexEpoch', fields=[
            ('id', models.PositiveSmallIntegerField(default=1, primary_key=True, serialize=False)),
            ('revision', models.BigIntegerField(default=0))]),
        migrations.CreateModel(name='SearchCandidateCache', fields=[
            ('key', models.CharField(max_length=64, primary_key=True, serialize=False)),
            ('revision', models.BigIntegerField()), ('candidates', models.JSONField()),
            ('expires_at', models.DateTimeField(db_index=True))]),
        migrations.RunPython(install, uninstall),
    ]
