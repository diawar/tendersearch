from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0049_waiting_results_stage')]
    operations = [migrations.CreateModel(name='P1CatalogSnapshot', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('products', models.JSONField(default=dict)),
        ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('lease', models.CharField(blank=True, max_length=64)),
        ('lease_until', models.DateTimeField(blank=True, null=True)),
        ('retry_at', models.DateTimeField(blank=True, null=True)),
        ('error', models.CharField(blank=True, max_length=250)),
    ])]
