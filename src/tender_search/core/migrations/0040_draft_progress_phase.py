from django.db import migrations,models

class Migration(migrations.Migration):
    dependencies=[('core','0039_unify_submission_path')]
    operations=[migrations.AddField(model_name='draftqueueitem',name='progress_phase',field=models.CharField(max_length=20,blank=True))]
