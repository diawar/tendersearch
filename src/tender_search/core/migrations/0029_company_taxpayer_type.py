from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0028_company_requisites')]
    operations = [migrations.AddField(
        model_name='companysettings', name='taxpayer_type',
        field=models.CharField(blank=True, max_length=200))]
