"""Personal preparation only. No authenticated portal requests or submissions."""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_http_methods
import json
import re
from urllib.parse import urlsplit
from datetime import timedelta
from django.utils import timezone
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from .models import ApplicationPreparation, CompanySettings, UserLotWorkflow, Lot, SearchProfile, CollectionLot
from .company_choices import TAXPAYER_TYPES
from .goszakup_countries import COUNTRY_CHOICES, canonical_country


class CountryField(forms.ChoiceField):
    def to_python(self, value):
        return canonical_country(super().to_python(value))

    def prepare_value(self, value):
        return canonical_country(value)


class PreparationForm(forms.Form):
    product_name = forms.CharField(label='Наименование предлагаемого товара', max_length=500)
    manufacturer = forms.CharField(label='Завод-изготовитель', max_length=500)
    country = CountryField(label='Страна происхождения', choices=COUNTRY_CHOICES,
        help_text='Справочник Госзакупа. Выберите страну производства товара, а не регистрации бренда.',
        error_messages={'invalid_choice': 'Выберите страну из справочника Госзакупа.'})
    taxpayer_type = forms.ChoiceField(label='Тип налогоплательщика на Госзакупе',choices=TAXPAYER_TYPES,required=False)
    legal_address = forms.CharField(label='Юридический адрес организации', max_length=1000, widget=forms.Textarea(attrs={'rows':2}))
    bank_account = forms.RegexField(label='Банковский счёт (ИИК)',regex=r'^KZ[0-9A-Z]{18}$',max_length=20,
        error_messages={'invalid':'Укажите казахстанский ИИК: KZ и ещё 18 букв или цифр.'})
    contact_phone = forms.RegexField(label='Контактный телефон',regex=r'^\+?[0-9 ()-]{10,25}$',max_length=25)
    security = forms.ChoiceField(label='Способ обеспечения',choices=[('wallet','Деньги с электронного кошелька'),('bank_guarantee','Электронная банковская гарантия')],initial='wallet')
    notes = forms.CharField(label='Заметки к подготовке',required=False,max_length=10000,widget=forms.Textarea(attrs={'rows':3}))


def supports_preparation_export(lot):
    """Preparation targets zakup.gov.kz, including its EPZ announcements.

    Keep this separate from protocol parsing, which has narrower support.
    """
    if (lot.announcement.source!='zakup' or
        not re.fullmatch(r'\d+',str(lot.announcement.external_id or '')) or
        not lot.number or not lot.announcement.number):
        return False
    ids=set()
    for raw in (lot.raw or {},lot.announcement.raw or {}):
        system=raw.get('system')
        for value in (raw.get('system_id'),system.get('id') if isinstance(system,dict) else system):
            if value is not None and value!='':
                ids.add(str(value))
    return bool(ids) and ids.issubset({'1','4'})


def draft_position_count(lot):
    """Count identity-checked registry plan entries, not editable application groups."""
    from .lot_pricing import cached_pricing_detail
    detail=cached_pricing_detail(lot)
    if not detail or detail.get('is_combined') is not False or not supports_preparation_export(lot):return None
    plans=detail.get('plan_items')
    if not isinstance(plans,list) or not 1<=len(plans)<=20:return None
    ids=[]
    for plan in plans:
        if not isinstance(plan,dict) or not plan.get('id'):return None
        method=plan.get('purchase_method') or {}
        if not isinstance(method,dict) or str(method.get('id'))!='12':return None
        ids.append(plan['id'])
    return len(plans) if len(set(ids))==len(ids) else None


@login_required
@require_http_methods(['GET','POST'])
def prepare_application(request, lot_id, price_form=None):
    lot=get_object_or_404(Lot.objects.select_related('announcement'),pk=lot_id)
    workflow=get_object_or_404(UserLotWorkflow,owner=request.user,lot=lot)
    if workflow.stage not in ('interesting','preparing','applied','waiting_results','won','lost','contract','delivery','payment','archived','not_participated'):
        raise PermissionDenied
    saved=ApplicationPreparation.objects.filter(owner=request.user,lot=lot).first()
    from .models import DraftQueueItem
    preparation_busy=DraftQueueItem.objects.filter(owner=request.user,lot=lot,state__in=('running','waiting')).exists()
    editable=workflow.stage == 'preparing' and not preparation_busy
    company=CompanySettings.objects.filter(owner=request.user).first()
    company_bin=company.company_bin if company else ''
    from .lot_pricing import attach_lot_budgets
    attach_lot_budgets([lot])
    initial={key:getattr(company,key,'') for key in ('legal_address','bank_account','contact_phone','taxpayer_type')}
    if saved:initial.update(saved.data)
    from .lot_pricing import LotPriceForm, company_vat_rate, price_entry_data
    from .models import UserLotPrice
    supplied_price_form = price_form is not None
    if price_form is None and request.method == 'POST' and price_entry_data(lot) and 'unit_gross' in request.POST:
        price_form=LotPriceForm(request.POST,lot=lot,initial={'vat_rate':company_vat_rate(request.user,company=company)})
    form=PreparationForm(request.POST if request.method=='POST' and not supplied_price_form else None,initial=initial)
    if request.method=='POST' and not supplied_price_form:
        if not editable:
            raise PermissionDenied
        if form.is_valid() and (price_form is None or price_form.is_valid()):
            with transaction.atomic():
                company=CompanySettings.objects.select_for_update().filter(owner=request.user).first()
                list(SearchProfile.objects.select_for_update().filter(owner=request.user).order_by('pk'))
                current=UserLotWorkflow.objects.select_for_update().get(pk=workflow.pk)
                if DraftQueueItem.objects.filter(owner=request.user,lot=lot,state__in=('running','waiting')).exists():
                    raise PermissionDenied('Сначала остановите заполнение в расширении.')
                if current.stage != 'preparing' or UserLotPrice.objects.select_for_update().filter(owner=request.user,lot=lot,locked_at__isnull=False).exists():
                    raise PermissionDenied
                if not company or not company.company_bin:
                    form.add_error(None,'Сначала укажите БИН организации в настройках.')
                else:
                    if price_form is not None and price_form.cleaned_data['vat_rate'] != company_vat_rate(request.user,company=company):
                        raise PermissionDenied('Настройка НДС изменилась. Обновите страницу.')
                    data=dict(form.cleaned_data)
                    data.update({key:getattr(company,key,'') for key in ('company_name','bank_name','bank_bic')})
                    if data['legal_address'] == company.legal_address and company.address_city and company.address_street and company.address_house:
                        data['address_parts']={key:getattr(company,'address_'+key) for key in ('city','street','house','office')}
                    if price_form is not None:
                        UserLotPrice.objects.update_or_create(owner=request.user,lot=lot,defaults={'unit_gross':price_form.cleaned_data['unit_gross'],'vat_rate':price_form.cleaned_data['vat_rate'],'locked_at':None})
                        data['price_confirmation']={'unit_gross':str(price_form.cleaned_data['unit_gross']),'vat_rate':str(price_form.cleaned_data['vat_rate']),'discount_confirmed':price_form.cleaned_data['confirm_discount'],'budget_total':str(price_form.budget['total']),'checked_at':timezone.now().isoformat()}
                    defaults={'company_bin':company.company_bin,'data':data}
                    if saved and saved.company_bin!=company.company_bin:
                        defaults.update(portal_state='unconfirmed',portal_number='',portal_url='',portal_checked_at=None,portal_confirmed_at=None,portal_sync_error='',portal_description_saved_at=None)
                    ApplicationPreparation.objects.update_or_create(owner=request.user,lot=lot,
                        defaults=defaults)
                    messages.success(request,'Подготовка сохранена. Заявка ещё не подана.')
                    return redirect('application_preparation',lot_id=lot.pk)
    if not editable:
        for field in form.fields.values():
            field.disabled=True
    export_supported=supports_preparation_export(lot)
    from .lot_pricing import pricing_context
    price_context=pricing_context(request.user,lot,price_form,company=company)
    if not editable and price_context.get('price_form'):
        price_context['price_editable']=False
        for field in price_context['price_form'].fields.values():field.disabled=True
    from .product_ai import configured
    specification_context=preparation_specification_context(request.user,lot,auto_load=workflow.stage=='preparing')
    positions=draft_position_count(lot) if export_supported and editable else None
    from .preparation_steps import context as steps_context
    steps=steps_context(form=form,saved=saved,company=company,workflow=workflow,pricing=price_context,
        specification=specification_context,editable=editable,positions=positions)
    from .obligation_costs import context as obligation_context
    from .deal_snapshot import context as deal_context
    response=render(request,'core/application_preparation.html',{'lot':lot,'form':form,'preparation':saved,**price_context,**steps,**deal_context(request.user,lot),
        **specification_context,
        **obligation_context(request.user,lot,specification_context['specification_documents']),
        'product_ai_configured':configured(),
        'product_ai_chat_only':getattr(settings,'OPENAI_CHAT_ONLY',True),
        'export_supported':export_supported,
        'decline_item_id':CollectionLot.objects.filter(profile__owner=request.user,lot=lot,stage='preparing').values_list('pk',flat=True).first() if workflow.stage=='preparing' else None,
        'draft_registry_positions':positions,
        'company_bin':company_bin,'preparation_busy':preparation_busy,'editable':editable,'bin_changed':bool(saved and saved.company_bin!=company_bin)})
    response['Cache-Control']='no-store, private'
    return response


def preparation_specification_context(owner,lot,*,auto_load=False):
    """Read shared specifications for this lot only; keep form rendering offline."""
    from django.db.models import OuterRef,Subquery,Q
    from .models import DocumentVersion,LotSpecification
    job=LotSpecification.objects.filter(owner=owner,lot=lot).first()
    latest=DocumentVersion.objects.filter(document_id=OuterRef('document_id')).order_by('-pk').values('pk')[:1]
    versions=DocumentVersion.objects.filter(document__announcement_id=lot.announcement_id,
        document__kind='technical',pk=Subquery(latest)).filter(
        Q(document__lot_id=lot.pk)|Q(document__lot_id__isnull=True)).exclude(text='').order_by('pk')
    documents=[{'name':v.document.name,'text':v.text,'source_url':v.document.url,
        'scope':'lot' if v.document.lot_id else 'announcement',
        'truncated':bool(v.pages and v.pages[0].get('truncated'))}
        for v in versions.select_related('document')[:8]]
    pending=bool(job and job.state in ('queued','running'))
    error=job.error if job else ''
    if pending and job.updated_at<=timezone.now()-timedelta(minutes=15):
        pending=False
        error='Извлечение не завершилось вовремя. Повторите загрузку.'
    if not documents and job:documents=job.documents
    return {'specification_documents':documents,'specification_pending':pending,
        'specification_state':job.state if pending else '',
        'specification_error':error,
        'specification_auto_load':bool(auto_load and not job and not documents)}


@login_required
@require_http_methods(['GET','POST'])
def preparation_specification(request,lot_id):
    workflow=get_object_or_404(UserLotWorkflow.objects.select_related('lot__announcement'),
        owner=request.user,lot_id=lot_id,
        stage__in=('interesting','preparing','applied','waiting_results','won','lost','contract','delivery','payment','archived'))
    if request.method=='POST':
        from .specification_service import queue_specification
        queue_specification(request.user,workflow.lot)
    response=render(request,'core/_preparation_specification.html',{
        'lot':workflow.lot,**preparation_specification_context(request.user,workflow.lot)})
    response['Cache-Control']='no-store, private'
    return response


@login_required
@require_http_methods(['GET','POST'])
def preparation_product_analysis(request,lot_id):
    from .product_ai import configured,VERSION
    from .models import ProductAnalysis,DraftQueueItem,UserLotPrice
    from .lot_pricing import attach_lot_budgets,price_budget
    workflow=get_object_or_404(UserLotWorkflow.objects.select_related('lot__announcement'),
        owner=request.user,lot_id=lot_id,stage='preparing')
    if request.method=='POST':
        if not configured():
            from .product_ai import unavailable_reason
            return JsonResponse({'error':unavailable_reason(),'ai_configured':False},status=409)
        if DraftQueueItem.objects.filter(owner=request.user,lot_id=lot_id,state__in=('running','waiting')).exists() or UserLotPrice.objects.filter(owner=request.user,lot_id=lot_id,locked_at__isnull=False).exists():
            return JsonResponse({'error':'Сначала остановите заполнение черновика. Зафиксированную цену менять нельзя.'},status=409)
        attach_lot_budgets([workflow.lot])
        if not price_budget(workflow.lot):
            return JsonResponse({'error':'Количество или бюджет лота не подтверждены.'},status=409)
        specification=preparation_specification_context(request.user,workflow.lot)
        if not specification['specification_documents']:
            return JsonResponse({'error':'Сначала дождитесь загрузки технической спецификации.'},status=409)
        import secrets
        from .tasks import analyze_preparation_product
        with transaction.atomic():
            job,_=ProductAnalysis.objects.get_or_create(owner=request.user,lot_id=lot_id,defaults={'generation':''})
            job=ProductAnalysis.objects.select_for_update().get(pk=job.pk)
            if job.state not in ('queued','running') or not job.generation or job.updated_at<timezone.now()-timedelta(minutes=6):
                job.generation=secrets.token_hex(16);job.state='queued';job.error='';job.save()
                def dispatch(pk=job.pk,generation=job.generation):
                    try:analyze_preparation_product.delay(pk,generation)
                    except Exception:ProductAnalysis.objects.filter(pk=pk,generation=generation).update(state='error',error='Очередь подбора недоступна. Повторите позже.')
                transaction.on_commit(dispatch)
    job=ProductAnalysis.objects.filter(owner=request.user,lot_id=lot_id).first()
    data={'state':job.state,'result':{k:v for k,v in job.result.items() if k!='comparison_cache'},'error':job.error} if job else {'state':'none'}
    if job and job.state=='done' and job.result.get('engine')!='openai':
        data={'state':'outdated','error':'Предыдущий подбор выполнен по правилам. Для ИИ-подбора подключите API и запустите анализ заново.'}
    elif job and job.state=='done' and job.result.get('analysis_version')!=VERSION:
        data={'state':'outdated','error':'Отбор и проверка товаров обновлены. Запустите анализ заново; прежний результат не используется для подстановки.'}
    if job and job.state in ('queued','running') and job.updated_at<timezone.now()-timedelta(minutes=6):
        data={'state':'error','error':'Подбор не завершился вовремя. Запустите повторно.'}
    from .overshop import preparation_fields
    if data.get('result',{}).get('candidates'):
        from .product_ai import candidate_type_mismatch
        data['result']['candidates']=[{**product,'preparation_fields':preparation_fields(product)} for product in data['result']['candidates'] if not candidate_type_mismatch(job.lot.name,product)]
        if not data['result']['candidates']:
            data['result']['summary']='Расходные материалы исключены: требуется само устройство. Подходящих устройств в сохранённой выборке нет.'
    data['ai_configured']=configured()
    response=JsonResponse(data);response['Cache-Control']='no-store, private'
    return response


def preparation_export_data(owner, lot_id):
    saved=get_object_or_404(ApplicationPreparation.objects.select_related('lot__announcement'),owner=owner,lot_id=lot_id)
    get_object_or_404(UserLotWorkflow,owner=owner,lot_id=lot_id,stage__in=('interesting','preparing'))
    company=CompanySettings.objects.filter(owner=owner).first()
    return export_saved_preparation(saved,company)


def export_saved_preparation(saved,company):
    """Serialize an already owner-scoped preparation after the workflow gate."""
    if not company or not company.company_bin or company.company_bin!=saved.company_bin:
        raise PermissionDenied('Проверьте и сохраните реквизиты после смены БИН.')
    lot=saved.lot
    if not supports_preparation_export(lot):
        raise ValueError('Экспорт для расширения доступен только для лотов Госзакупа и EPZ с подтверждённой площадкой.')
    country_field = PreparationForm.base_fields['country']
    try:
        country = country_field.clean(saved.data.get('country'))
    except forms.ValidationError:
        raise ValueError('Выберите страну из справочника Госзакупа и сохраните подготовку.')
    position_count=draft_position_count(lot)
    return {'schema':'goszakup-preparation-v1','company_bin':saved.company_bin,
        'purchase_method_id':(lot.raw or {}).get('purchase_method_id'), 'single_position':position_count==1,
        'position_count':position_count,
        'existing_application_number':saved.portal_number if saved.portal_state in ('draft','submitted','completed') else '',
        'announcement_external_id':str(lot.announcement.external_id),'announcement_number':lot.announcement.number,
        'lot_number':lot.number,'data':{'taxpayer_type':company.taxpayer_type,
            **{k:v for k,v in saved.data.items() if k not in ('notes','price_confirmation')}, 'country':country, 'taxpayer_type':saved.data.get('taxpayer_type') or company.taxpayer_type}}


@login_required
@require_http_methods(['GET'])
def export_preparation(request, lot_id):
    try:data=preparation_export_data(request.user,lot_id)
    except ValueError as error:
        messages.error(request,str(error))
        return redirect('application_preparation',lot_id=lot_id)
    response=JsonResponse(data,json_dumps_params={'ensure_ascii':False})
    response['Content-Disposition']=f'attachment; filename="preparation-{lot_id}.json"'
    response['Cache-Control']='no-store, private'
    return response


def save_portal_report(owner, lot_id, report, *, allow_create=False):
    if not isinstance(report,dict) or report.get('schema')!='goszakup-status-v1':
        raise ValueError('Неизвестный формат отчёта.')
    observed=parse_datetime(str(report.get('observed_at','')))
    if not observed or timezone.is_naive(observed) or observed>timezone.now()+timedelta(minutes=2):
        raise ValueError('Некорректное время проверки.')
    with transaction.atomic():
        company=CompanySettings.objects.select_for_update().filter(owner=owner).first()
        # Match the lock order used by company settings and workflow actions.
        list(SearchProfile.objects.select_for_update().filter(owner=owner).order_by('pk'))
        workflow=UserLotWorkflow.objects.select_for_update().select_related('lot__announcement').filter(owner=owner,lot_id=lot_id).first()
        if allow_create:
            if not company or not company.company_bin or report.get('company_bin')!=company.company_bin:
                raise ValueError('БИН отчёта не соответствует вашему аккаунту.')
            if not workflow or not supports_preparation_export(workflow.lot):
                raise ValueError('Лот отсутствует в рабочем пространстве или площадка не поддерживается.')
            ApplicationPreparation.objects.get_or_create(owner=owner,lot_id=lot_id,defaults={'company_bin':company.company_bin})
        saved=get_object_or_404(ApplicationPreparation.objects.select_for_update().select_related('lot__announcement'),owner=owner,lot_id=lot_id)
        if (not company or not company.company_bin or company.company_bin!=saved.company_bin or
            report.get('company_bin')!=saved.company_bin or report.get('announcement_number')!=saved.lot.announcement.number or
            report.get('lot_number')!=saved.lot.number):
            raise ValueError('Отчёт не соответствует БИН, объявлению или лоту.')
        if saved.portal_checked_at and observed<=saved.portal_checked_at:
            raise ValueError('Этот отчёт уже загружен или устарел.')
        state=report.get('state')
        if 'description_saved' in report and type(report['description_saved']) is not bool:
            raise ValueError('Некорректное подтверждение описания товара.')
        if report.get('description_saved') and state!='draft':
            raise ValueError('Описание товара подтверждается отдельным отчётом заполнения черновика.')
        if state in ('draft','submitted','completed'):
            link=str(report.get('url',''));parts=urlsplit(link)
            if (parts.scheme!='https' or parts.netloc!='zakup.gov.kz' or parts.query or parts.fragment or
                not re.fullmatch(r'/cabinet/(?:applications?|purchase/my-applications)/'+re.escape(str(report.get('number','')))+r'/?',parts.path) or
                not re.fullmatch(r'\d{1,30}',str(report.get('number','')))):
                raise ValueError('Отчёт не содержит корректную ссылку и номер заявки.')
            ranks={'draft':1,'submitted':2,'completed':3}
            if saved.portal_number and saved.portal_number!=report['number']:
                if saved.portal_state in ('submitted','completed'):
                    raise ValueError('Уже подтверждена другая поданная заявка.')
                saved.portal_description_saved_at=None
            if ranks.get(saved.portal_state,0)>ranks[state]:
                raise ValueError('Понижение подтверждённого статуса требует отдельной проверки.')
            saved.portal_state=state;saved.portal_number=report['number'];saved.portal_url=link
            saved.portal_confirmed_at=observed
            saved.portal_sync_error=''
            if report.get('description_saved'):
                saved.portal_description_saved_at=observed
            if state=='submitted' and workflow and workflow.stage in ('new','interesting','preparing','not_participated','applied','waiting_results'):
                from .workflow_state import update_workflow
                from .outcome_service import register_watch
                from .models import UserLotPrice
                # The report confirms submission, not the portal price. Freeze
                # an existing local estimate without inventing a submitted price.
                UserLotPrice.objects.select_for_update().filter(owner=owner,lot_id=lot_id,locked_at__isnull=True).update(locked_at=observed)
                expired=bool(saved.lot.announcement.deadline and saved.lot.announcement.deadline<=timezone.now())
                update_workflow(owner.pk,lot_id,stage='waiting_results' if expired else 'applied',outcome='',result_tracking=True,
                    expired_from_stage='applied' if expired else '',keep_after_deadline=False)
                register_watch(owner.pk,lot_id)
                from .workflow_notifications import queue_event
                queue_event(owner.pk,saved.lot,company.company_bin,'submitted',report['number'])
        elif state=='unknown':
            saved.portal_sync_error='Не удалось подтвердить текущее состояние. Сохранено последнее подтверждение.'
        else:
            raise ValueError('Неизвестное состояние заявки.')
        saved.portal_checked_at=observed
        saved.save()
        return saved


@login_required
@require_http_methods(['POST'])
def import_portal_status(request, lot_id):
    try:
        file=request.FILES.get('report')
        if not file or file.size>12000:
            raise ValueError('Выберите файл отчёта расширения размером до 12 КБ.')
        report=json.loads(file.read().decode('utf-8'))
        save_portal_report(request.user,lot_id,report)
        messages.success(request,'Отчёт расширения сохранён.')
    except (ValueError,TypeError,UnicodeDecodeError):
        messages.error(request,'Не удалось принять отчёт. Проверьте файл, БИН, лот и время проверки.')
    return redirect('application_preparation',lot_id=lot_id)
