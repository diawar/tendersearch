"""Presentation of the existing workflow; never advances stages or submits bids."""
from django.utils import timezone


def context(*, form, saved, company, workflow, pricing, specification, editable, positions):
    from .preparation import PreparationForm
    values = {name: form[name].value() for name in form.fields}
    checked = PreparationForm(values)
    checked.is_valid()
    product_names = ('product_name', 'manufacturer', 'country')
    product_missing = [form[name].label for name in product_names if name in checked.errors]
    company_missing = [form[name].label for name in form.fields if name not in product_names and name in checked.errors]
    if not company or not company.company_bin:
        company_missing.insert(0, 'БИН организации в настройках')
    if not values.get('taxpayer_type') and not getattr(company, 'taxpayer_type', ''):
        company_missing.append('Тип налогоплательщика')
    if pricing.get('price_policy'):
        if not pricing.get('saved_price') or pricing.get('price_form').errors:
            product_missing.append('Наша цена за единицу')
    if saved and company and saved.company_bin != company.company_bin:
        company_missing.append('Сохранить реквизиты после смены БИН')
    reason = ''
    if product_missing or company_missing or not saved:
        reason = 'Заполните обязательные поля и сохраните подготовку.'
    elif not positions:
        reason = 'Состав лота или площадка не подтверждены для расширения.'
    elif workflow.lot.announcement.deadline and workflow.lot.announcement.deadline <= timezone.now():
        reason = 'Приём заявок завершён. Дальше — «Ожидаем итоги» и проверка по БИН.'
    elif not editable:
        reason = 'Запуск недоступен на текущем этапе или во время заполнения.'
    return {'preparation_product_missing': product_missing, 'preparation_company_missing': company_missing,
            'preparation_launch_reason': reason, 'preparation_launch_ready': not reason,
            'preparation_workflow_stage': workflow.stage,
            'preparation_workflow_label': workflow.get_stage_display(),
            'preparation_spec_ready': bool(specification.get('specification_documents'))}
