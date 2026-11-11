from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0031_draft_queue')]
    operations = [migrations.AlterField(
        model_name='draftqueueitem', name='state',
        field=models.CharField(max_length=20, default='queued', choices=[
            ('queued','Ожидает'), ('ready','Передано в расширение'), ('running','Заполняется'),
            ('waiting','Ожидание восстановления'), ('paused','Приостановлено'),
            ('blocked','Требует проверки'), ('complete','Черновик готов'), ('cancelled','Убрано из очереди'),
        ]),
    )]
