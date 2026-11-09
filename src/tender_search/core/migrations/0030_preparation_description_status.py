from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0029_company_taxpayer_type')]
    operations = [migrations.AddField(
        model_name='applicationpreparation', name='portal_description_saved_at',
        field=models.DateTimeField(null=True, blank=True),
    )]
