from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0046_document_search_text')]
    operations = [migrations.AddIndex(model_name='documentversion',
        index=models.Index(fields=['id'], condition=models.Q(search_text__isnull=True),
            name='idx_document_text_pending'))]
