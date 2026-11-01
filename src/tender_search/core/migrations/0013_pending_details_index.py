from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0012_search_projection_and_cache')]

    operations = [
        migrations.AddIndex(
            model_name='indexedlot',
            index=models.Index(fields=['deadline'],
                               condition=models.Q(current=True, row___details_pending=True),
                               name='idx_pending_details_deadline'),
        ),
    ]
