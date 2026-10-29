import re
from django import template
from django.utils.html import format_html_join, format_html

register = template.Library()


@register.filter
def highlight(value, terms):
    text = str(value or "")
    terms = [t for t in terms if t]
    if not terms:
        return text
    pattern = "(" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + ")"
    pieces = re.split(pattern, text, flags=re.IGNORECASE)
    return format_html_join("", "{}", ((format_html("<mark>{}</mark>", piece) if index % 2 else piece,) for index, piece in enumerate(pieces)))


@register.simple_tag
def icon(name):
    from django.templatetags.static import static
    return format_html('<svg class="icon" aria-hidden="true" focusable="false"><use href="{}#{}"></use></svg>', static('core/icons.svg'), name)


@register.simple_tag
def search_conditions(spec):
    from tender_search.core.models import ReferenceEntry
    conditions = list(spec.get("terms", []))
    conditions.append("Любое слово — ИЛИ" if spec.get("mode","any")=="any" else "Все слова — И")
    names = {"contains":"Содержит текст","word":"Целые слова","phrase":"Точная фраза","field":"Название целиком"}
    conditions.append(names.get(spec.get("kind","contains"),"Содержит текст"))
    filters = spec.get("filters", {})
    for parameter, label in [("total_price__gte", "Сумма от"), ("total_price__lte", "Сумма до")]:
        if filters.get(parameter):
            conditions.append(f"{label}: {filters[parameter]} ₸")
    if spec.get("customer_bin"):
        conditions.append(f"БИН заказчика: {spec['customer_bin']}")
    for parameter, kind, label in [("status_id", "lot-statuses", "Статус"),
                                    ("purchase_method_id", "purchase-methods", "Способ закупки")]:
        value = filters.get(parameter + "__in", filters.get(parameter, ""))
        ids = str(value).split("__") if value else []
        names = {str(row.source_id): row.name for row in ReferenceEntry.objects.filter(kind=kind, source_id__in=[v for v in ids if v.isdigit()])}
        conditions.extend(f"{label}: {names.get(identifier, identifier)}" for identifier in ids)
    regions, cities = spec.get("regions", []), spec.get("cities", [])
    if regions or cities:
        for ids, kind, label in [(regions,"katos","Область"),(cities,"cities","Город")]:
            names = {str(row.source_id):row.name for row in ReferenceEntry.objects.filter(kind=kind,source_id__in=ids)}
            conditions.extend(f"{label}: {names.get(str(identifier), identifier)}" for identifier in ids)
    elif filters.get("delivery_address_id__in"):
        ids = str(filters["delivery_address_id__in"]).split("__")
        names = {str(row.source_id):row.name for row in ReferenceEntry.objects.filter(kind__in=["katos","cities"], source_id__in=[v for v in ids if v.isdigit()])}
        conditions.extend(f"Место поставки: {names.get(identifier,identifier)}" for identifier in ids)
    for parameter, label in [("offer_end_date__gte","Окончание приёма от"),("offer_end_date__lte","Окончание приёма до"),
                              ("lot_number","Номер лота"),("announcement_number","Номер объявления")]:
        if filters.get(parameter):
            value = str(filters[parameter])
            if parameter.startswith("offer_end_date"):
                parts = value[:10].split("-")
                if len(parts)==3:value=".".join(reversed(parts))
            if parameter.startswith("total_price"):value += " ₸"
            conditions.append(f"{label}: {value}")
    conditions.append("Автоматический обход страниц" if spec.get("auto_pages") else f"До {spec.get('depth', int(spec.get('pages',1))*50)} записей на слово")
    return conditions


@register.simple_tag
def condition_controls(spec):
    labels = search_conditions(spec)
    terms = spec.get("terms", [])
    controls = []
    prefixes = {"Сумма от:":"amount_min", "Сумма до:":"amount_max", "БИН заказчика:":"customer_bin",
                "Статус:":"status", "Способ закупки:":"method", "Область:":"region", "Город:":"city",
                "Место поставки:":"region", "Окончание приёма от:":"deadline_from", "Окончание приёма до:":"deadline_to",
                "Номер лота:":"number", "Номер объявления:":"number"}
    for index, label in enumerate(labels):
        if index < len(terms): field, value = "terms", terms[index]
        elif index == len(terms): field, value = "mode", "any"
        elif index == len(terms)+1: field, value = "kind", "contains"
        else:
            field = next((field for prefix, field in prefixes.items() if label.startswith(prefix)), "depth")
            value = label.split(": ",1)[1] if ": " in label else "500"
        controls.append({"label":label,"field":field,"value":value})
    controls.extend({"label":term,"field":"exclude","value":term} for term in spec.get("exclude",[]))
    return controls
