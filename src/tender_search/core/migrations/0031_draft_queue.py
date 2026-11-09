import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core','0030_preparation_description_status'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='DraftQueueItem',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
        ('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
        ('generation',models.UUIDField(default=uuid.uuid4,editable=False)),
        ('state',models.CharField(max_length=20,default='queued',choices=[('queued','Ожидает'),('ready','Передано в расширение'),('running','Заполняется'),('paused','Приостановлено'),('blocked','Требует проверки'),('complete','Черновик готов'),('cancelled','Убрано из очереди')])),
        ('message',models.CharField(blank=True,max_length=500)),
        ('owner',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to=settings.AUTH_USER_MODEL)),
        ('lot',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to='core.lot')),
    ],options={'constraints':[models.UniqueConstraint(fields=('owner','lot'),name='unique_draft_queue_lot')]})]
