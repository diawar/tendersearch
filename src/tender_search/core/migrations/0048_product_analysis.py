from django.conf import settings
from django.db import migrations,models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[('core','0047_document_pending_projection_index'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[migrations.CreateModel(name='ProductAnalysis',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
        ('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
        ('state',models.CharField(default='queued',max_length=20)),('generation',models.CharField(max_length=64)),
        ('result',models.JSONField(default=dict)),('error',models.CharField(blank=True,max_length=300)),
        ('lot',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to='core.lot')),
        ('owner',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to=settings.AUTH_USER_MODEL)),
    ],options={'constraints':[models.UniqueConstraint(fields=('owner','lot'),name='unique_owner_lot_product_analysis')]})]
