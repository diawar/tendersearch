from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0050_p1_catalog_snapshot'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='LotObligationCosts', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('updated_at', models.DateTimeField(auto_now=True)),
        ('entries', models.JSONField(default=list)),
        ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ('lot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.lot')),
    ], options={'constraints': [models.UniqueConstraint(fields=('owner', 'lot'), name='unique_lot_obligation_costs')]})]
