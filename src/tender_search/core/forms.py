import re
from datetime import datetime, time
from django import forms
from django.utils import timezone
from .models import ReferenceEntry
from .procurement_groups import grouped_choices


def split_terms(value):
    terms = list(dict.fromkeys(t.strip() for t in re.split(r"[\s;,]+", value or "") if t.strip()))
    if any(len(t) > 150 for t in terms):
        raise forms.ValidationError("Слово не должно быть длиннее 150 символов.")
    return terms


class SearchForm(forms.Form):
    terms = forms.CharField(label="Ключевые слова",
                            widget=forms.Textarea(attrs={"rows": 2, "placeholder": "телефон\nсмартфон"}),
                            help_text="Один тег — одно слово. Количество тегов не ограничено.")
    mode = forms.ChoiceField(label="Объединение условий", initial="any", choices=[("any", "Любое условие — ИЛИ"), ("all", "Все условия — И")])
    kind = forms.ChoiceField(label="Совпадение", choices=[("contains", "Содержит текст"), ("word", "Целые слова"),
                               ("phrase", "Точная фраза"), ("field", "Название целиком")])
    exclude = forms.CharField(label="Исключить слова", required=False,
                              widget=forms.Textarea(attrs={"rows": 2, "placeholder": "ремонт; монтаж"}),
                              help_text="Исключаем вхождения текста в названии и описании: услуг → услуги, ремонт → ремонта.")
    amount_min = forms.DecimalField(label="Сумма от, ₸", required=False, min_value=0, max_digits=18, decimal_places=2)
    amount_max = forms.DecimalField(label="Сумма до, ₸", required=False, min_value=0, max_digits=18, decimal_places=2)
    only_advance = forms.BooleanField(label="Только с авансом", required=False, initial=False)
    deadline_from = forms.DateField(label="Приём заявок до — начиная с", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    deadline_to = forms.DateField(label="Приём заявок до — по", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    status = forms.MultipleChoiceField(label="Статус лота", required=False, widget=forms.CheckboxSelectMultiple)
    method = forms.MultipleChoiceField(label="Способ закупки", required=False, widget=forms.CheckboxSelectMultiple)
    region = forms.MultipleChoiceField(label="Область поставки", required=False, widget=forms.CheckboxSelectMultiple)
    city = forms.MultipleChoiceField(label="Город", required=False, widget=forms.CheckboxSelectMultiple)
    customer_bin = forms.RegexField(label="БИН заказчика", regex=r"^[0-9]{12}$", required=False,
                                    error_messages={"invalid": "БИН должен содержать 12 цифр."})
    number = forms.CharField(label="Номер лота или объявления", required=False, max_length=100)
    number_kind = forms.ChoiceField(label="Искать номер", choices=[("lot_number", "Лота"), ("announcement_number", "Объявления")])


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.procurement_options = {}
        for field, kind in [("status", "lot-statuses"), ("method", "purchase-methods")]:
            rows = list(ReferenceEntry.objects.filter(kind=kind))
            groups = grouped_choices(rows, field)
            self.procurement_options[field] = groups
            self.fields[field].choices = [option for _, options in groups for option in options]
            if not self.initial.get(field) and not self.initial.get("_defaults_version"):
                self.initial[field] = ([f'open:{value}' for value in (6,7,9) if any(row.source_id == value for row in rows)] if field == 'status'
                                       else [str(row.source_id) for row in rows if row.name == 'Запрос ценовых предложений'])
        self.fields["region"].choices = [(str(r.source_id), r.name) for r in ReferenceEntry.objects.filter(kind="katos")]
        self.fields["city"].choices = [(str(r.source_id), r.name) for r in ReferenceEntry.objects.filter(kind="cities")]
        for field in ("region", "city", "status", "method"):
            value = self.initial.get(field)
            if isinstance(value, str):
                self.initial[field] = value.split("__") if value else []

        for field in ('status','method'):
            selected = set(str(value) for value in self[field].value() or [])
            groups = [{'label':label,'options':[{'value':value,'label':label_text,'selected':value in selected} for value,label_text in options]}
                      for label,options in self.procurement_options[field]]
            setattr(self,field+'_groups',groups)


    def clean_terms(self):
        terms = split_terms(self.cleaned_data["terms"])
        if not terms:
            raise forms.ValidationError("Введите хотя бы одно слово.")
        return terms

    def clean_exclude(self):
        return split_terms(self.cleaned_data.get("exclude"))

    def clean(self):
        data = super().clean()
        for low, high in [("amount_min", "amount_max"), ("deadline_from", "deadline_to")]:
            if data.get(low) is not None and data.get(high) is not None and data[low] > data[high]:
                self.add_error(high, "Верхняя граница должна быть не меньше нижней.")
        return data

    def specification(self):
        data = self.cleaned_data
        filters = {}
        for field, param in [("amount_min", "total_price__gte"), ("amount_max", "total_price__lte"),
                             ("deadline_from", "offer_end_date__gte"), ("deadline_to", "offer_end_date__lte")]:
            if data.get(field) is not None and data.get(field) != "":
                if field == "deadline_to":
                    filters[param] = datetime.combine(data[field], time(23, 59, 59), tzinfo=timezone.get_current_timezone()).isoformat()
                else:
                    filters[param] = str(data[field])
        for field, param in [("status", "status_id__in"), ("method", "purchase_method_id__in")]:
            if data.get(field):
                filters[param] = "__".join(dict.fromkeys(value.rsplit(":",1)[-1] for value in data[field]))
        regions = data.get("region", [])
        cities = data.get("city", [])
        city_rows = list(ReferenceEntry.objects.filter(kind="cities", source_id__in=cities))
        covered = {str(r.raw.get("region_id")) for r in city_rows}
        addresses = cities + [r for r in regions if r not in covered]
        if addresses:
            filters["delivery_address_id__in"] = "__".join(dict.fromkeys(addresses))
        if data.get("number"):
            filters[data["number_kind"]] = data["number"].strip()
        if data.get("only_advance"):
            filters["only_advance"] = True
        return {"terms": data["terms"], "exclude": data["exclude"], "kind": data["kind"],
                "mode": data["mode"], "backend": "local_index", "pages": 10, "auto_pages": False, "extended_descriptions": True, "filters": filters,
                "customer_bin": data.get("customer_bin", ""), "regions": regions, "cities": cities, "defaults_version": 1, "status_selection": data.get("status", [])}
