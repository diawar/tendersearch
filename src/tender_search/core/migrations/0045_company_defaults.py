from django.conf import settings
from django.db import migrations, models

DEFAULTS = {
    "company_bin": "241040001415",
    "company_name": "ТОО \"OVERSHOP.KZ\"",
    "address_city": "Павлодар",
    "address_street": "Желтоксан",
    "address_house": "7",
    "address_office": "25Б",
    "legal_address": "Павлодар, Желтоксан, 7, 25Б",
    "bank_account": "KZ92601A241008980281",
    "bank_name": "АО \"Народный Банк Казахстана\"",
    "bank_bic": "HSBKKZKX",
    "taxpayer_type": "Юридическое лицо"
}

def fill_empty(apps, schema_editor):
    Company = apps.get_model("core", "CompanySettings")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    alias = schema_editor.connection.alias
    for user_id in User.objects.using(alias).values_list("pk", flat=True).iterator():
        company, _ = Company.objects.using(alias).get_or_create(owner_id=user_id)
        fields = []
        # Preserve other organisations and explicitly entered addresses.
        if company.company_bin not in ("", DEFAULTS["company_bin"]):
            continue
        for name, value in DEFAULTS.items():
            if name.startswith("address_") or name == "legal_address":
                if any(getattr(company, key) and getattr(company, key) != DEFAULTS[key] for key in ("address_city", "address_street", "address_house", "address_office")):
                    continue
            if not getattr(company, name):
                setattr(company, name, value)
                fields.append(name)
        if fields:
            company.save(using=alias, update_fields=fields)

class Migration(migrations.Migration):
    dependencies = [("core", "0044_procurement_coverage_snapshot"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AlterField(model_name="companysettings", name="company_bin", field=models.CharField(max_length=12, blank=True, default="241040001415")),
        migrations.AlterField(model_name="companysettings", name="company_name", field=models.CharField(max_length=500, blank=True, default="ТОО \"OVERSHOP.KZ\"")),
        migrations.AlterField(model_name="companysettings", name="address_city", field=models.CharField(max_length=200, blank=True, default="Павлодар")),
        migrations.AlterField(model_name="companysettings", name="address_street", field=models.CharField(max_length=300, blank=True, default="Желтоксан")),
        migrations.AlterField(model_name="companysettings", name="address_house", field=models.CharField(max_length=50, blank=True, default="7")),
        migrations.AlterField(model_name="companysettings", name="address_office", field=models.CharField(max_length=50, blank=True, default="25Б")),
        migrations.AlterField(model_name="companysettings", name="legal_address", field=models.CharField(max_length=1000, blank=True, default="Павлодар, Желтоксан, 7, 25Б")),
        migrations.AlterField(model_name="companysettings", name="bank_account", field=models.CharField(max_length=20, blank=True, default="KZ92601A241008980281")),
        migrations.AlterField(model_name="companysettings", name="bank_name", field=models.CharField(max_length=200, blank=True, default="АО \"Народный Банк Казахстана\"")),
        migrations.AlterField(model_name="companysettings", name="bank_bic", field=models.CharField(max_length=11, blank=True, default="HSBKKZKX")),
        migrations.AlterField(model_name="companysettings", name="taxpayer_type", field=models.CharField(max_length=200, blank=True, default="Юридическое лицо")),
        migrations.RunPython(fill_empty, migrations.RunPython.noop),
    ]
