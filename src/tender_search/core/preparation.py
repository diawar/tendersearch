"""Personal preparation only. No authenticated portal requests or submissions."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
import json
import re
from urllib.parse import urlsplit
from datetime import timedelta
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import ApplicationPreparation, CompanySettings, UserLotWorkflow, Lot, SearchProfile


class PreparationForm(forms.Form):
    product_name = forms.CharField(label='Наименование предлагаемого товара', max_length=500)
    manufacturer = forms.CharField(label='Завод-изготовитель', max_length=500)
    country = forms.CharField(label='Страна происхождения', max_length=150,
        help_text='Страна производства конкретного товара, а не регистрации бренда.')
    legal_address = forms.CharField(label='Юридический адрес организации', max_length=1000, widget=forms.Textarea(attrs={'rows':2}))
    bank_account = forms.RegexField(label='Банковский счёт (ИИК)',regex=r'^KZ[0-9A-Z]{18}$',max_length=20,
        error_messages={'invalid':'Укажите казахстанский ИИК: KZ и ещё 18 букв или цифр.'})
    contact_phone = forms.RegexField(label='Контактный телефон',regex=r'^\+?[0-9 ()-]{10,25}$',max_length=25)
    security = forms.ChoiceField(label='Способ обеспечения',choices=[('wallet','Деньги с электронного кошелька'),('bank_guarantee','Электронная банковская гарантия')],initial='wallet')
    notes = forms.CharField(label='Заметки к подготовке',required=False,max_length=10000,widget=forms.Textarea(attrs={'rows':3}))


@login_required
@require_http_methods(['GET','POST'])
def prepare_application(request, lot_id):
    lot=get_object_or_404(Lot.objects.select_related('announcement'),pk=lot_id)
    workflow=get_object_or_404(UserLotWorkflow,owner=request.user,lot=lot)
    if workflow.stage not in ('interesting','applied','won','lost','contract','delivery','payment','archived'):
        raise PermissionDenied
    saved=ApplicationPreparation.objects.filter(owner=request.user,lot=lot).first()
    company_bin=CompanySettings.objects.filter(owner=request.user).values_list('company_bin',flat=True).first() or ''
    editable=workflow.stage=='interesting'
    form=PreparationForm(request.POST if request.method=='POST' else None,initial=saved.data if saved else {})
    if request.method=='POST':
        if not editable:
            raise PermissionDenied
        if form.is_valid():
            with transaction.atomic():
                company=CompanySettings.objects.select_for_update().filter(owner=request.user).first()
                list(SearchProfile.objects.select_for_update().filter(owner=request.user).order_by('pk'))
                current=UserLotWorkflow.objects.select_for_update().get(pk=workflow.pk)
                if current.stage!='interesting':
                    raise PermissionDenied
                if not company or not company.company_bin:
                    form.add_error(None,'Сначала укажите БИН организации в настройках.')
                else:
                    defaults={'company_bin':company.company_bin,'data':form.cleaned_data}
                    if saved and saved.company_bin!=company.company_bin:
                        defaults.update(portal_state='unconfirmed',portal_number='',portal_url='',portal_checked_at=None,portal_confirmed_at=None,portal_sync_error='')
                    ApplicationPreparation.objects.update_or_create(owner=request.user,lot=lot,
                        defaults=defaults)
                    messages.success(request,'Данные сохранены в приложении.')
                    return redirect('application_preparation',lot_id=lot.pk)
    if not editable:
        for field in form.fields.values():
            field.disabled=True
    response=render(request,'core/application_preparation.html',{'lot':lot,'form':form,'preparation':saved,
        'company_bin':company_bin,'editable':editable,'bin_changed':bool(saved and saved.company_bin!=company_bin)})
    response['Cache-Control']='no-store, private'
    return response


@login_required
@require_http_methods(['GET'])
def export_preparation(request, lot_id):
    saved=get_object_or_404(ApplicationPreparation.objects.select_related('lot__announcement'),owner=request.user,lot_id=lot_id)
    get_object_or_404(UserLotWorkflow,owner=request.user,lot_id=lot_id,stage='interesting')
    company=CompanySettings.objects.filter(owner=request.user).first()
    if not company or not company.company_bin or company.company_bin!=saved.company_bin:
        raise PermissionDenied('Проверьте и сохраните реквизиты после смены БИН.')
    lot=saved.lot
    from .outcome_service import supported_source
    if not supported_source(lot):
        raise PermissionDenied('Поддерживаются только подтверждённые лоты Госзакупа.')
    response=JsonResponse({'schema':'goszakup-preparation-v1','company_bin':saved.company_bin,
        'announcement_external_id':str(lot.announcement.external_id),'announcement_number':lot.announcement.number,
        'lot_number':lot.number,'data':{k:v for k,v in saved.data.items() if k!='notes'}},json_dumps_params={'ensure_ascii':False})
    response['Content-Disposition']=f'attachment; filename="preparation-{lot.pk}.json"'
    response['Cache-Control']='no-store, private'
    return response


@login_required
@require_http_methods(['POST'])
def import_portal_status(request, lot_id):
    try:
        file=request.FILES.get('report')
        if not file or file.size>12000:
            raise ValueError('Выберите файл отчёта расширения размером до 12 КБ.')
        report=json.loads(file.read().decode('utf-8'))
        if not isinstance(report,dict) or report.get('schema')!='goszakup-status-v1':
            raise ValueError('Неизвестный формат отчёта.')
        observed=parse_datetime(str(report.get('observed_at','')))
        if not observed or timezone.is_naive(observed) or observed>timezone.now()+timedelta(minutes=2):
            raise ValueError('Некорректное время проверки.')
        with transaction.atomic():
            company=CompanySettings.objects.select_for_update().filter(owner=request.user).first()
            saved=get_object_or_404(ApplicationPreparation.objects.select_for_update().select_related('lot__announcement'),owner=request.user,lot_id=lot_id)
            if (not company or not company.company_bin or company.company_bin!=saved.company_bin or
                report.get('company_bin')!=saved.company_bin or report.get('announcement_number')!=saved.lot.announcement.number or
                report.get('lot_number')!=saved.lot.number):
                raise ValueError('Отчёт не соответствует БИН, объявлению или лоту.')
            if saved.portal_checked_at and observed<=saved.portal_checked_at:
                raise ValueError('Этот отчёт уже загружен или устарел.')
            state=report.get('state')
            if state in ('draft','submitted'):
                link=str(report.get('url',''));parts=urlsplit(link)
                if (parts.scheme!='https' or parts.netloc!='zakup.gov.kz' or parts.query or parts.fragment or
                    not re.fullmatch(r'/cabinet/(?:applications?|purchase/my-applications)/\d+/?',parts.path) or
                    not re.fullmatch(r'\d{1,30}',str(report.get('number','')))):
                    raise ValueError('Отчёт не содержит корректную ссылку и номер заявки.')
                if saved.portal_state=='submitted' and state=='draft':
                    raise ValueError('Понижение поданной заявки до проекта требует отдельной проверки.')
                saved.portal_state=state;saved.portal_number=report['number'];saved.portal_url=link
                saved.portal_confirmed_at=observed
                saved.portal_sync_error=''
            elif state=='unknown':
                saved.portal_sync_error='Не удалось подтвердить текущее состояние. Сохранено последнее подтверждение.'
            else:
                raise ValueError('Неизвестное состояние заявки.')
            saved.portal_checked_at=observed
            saved.save()
        messages.success(request,'Отчёт расширения сохранён. Этап работы не изменён.')
    except (ValueError,TypeError,UnicodeDecodeError):
        messages.error(request,'Не удалось принять отчёт. Проверьте файл, БИН, лот и время проверки.')
    return redirect('application_preparation',lot_id=lot_id)
