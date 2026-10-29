from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0007_shared_procurement_index')]
    operations = [migrations.AlterField(
        model_name='indexedlot', name='method_id',
        field=models.PositiveIntegerField(blank=True, db_index=True, null=True),
    )]
