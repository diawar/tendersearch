"""Durable personal queue; portal actions run only in the user's extension."""
import json
import uuid
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.http import Http404
from django.core.exceptions import PermissionDenied
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from datetime import timedelta
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from .models import DraftQueueItem, UserLotWorkflow, CompanySettings, ApplicationPreparation, SearchProfile
from .preparation import preparation_export_data, save_portal_report, supports_preparation_export, PreparationForm
from .lot_state import apply_personal_state


@login_required
@require_GET
def queue(request):
    items=list(DraftQueueItem.objects.filter(owner=request.user).exclude(state='cancelled').select_related('lot__announcement','lot__customer').order_by('created_at','pk'))
    apply_personal_state(request.user,[item.lot for item in items])
    response=render(request,'core/draft_queue.html',{'items':items})
    response['Cache-Control']='no-store, private'
    return response


@login_required
@require_POST
def enqueue(request):
    values=list(dict.fromkeys(request.POST.getlist('lots')))
    if not values or len(values)>20 or any(not value.isdecimal() for value in values):
        messages.error(request,'Выберите от 1 до 20 интересных лотов.')
        return redirect('draft_queue')
    count=0
    with transaction.atomic():
        list(SearchProfile.objects.select_for_update().filter(owner=request.user).order_by('pk'))
        workflows=UserLotWorkflow.objects.select_for_update().select_related('lot__announcement').filter(owner=request.user,lot_id__in=values,stage='interesting')
        for workflow in workflows:
            if not supports_preparation_export(workflow.lot):continue
            item,created=DraftQueueItem.objects.get_or_create(owner=request.user,lot=workflow.lot)
            if item.state=='cancelled':
                item.state='queued';item.message='';item.generation=uuid.uuid4();item.save()
            count+=1
    if count:messages.success(request,f'В очереди: {count}. Повторно выбранные лоты не дублируются.')
    if count<len(values):messages.warning(request,'Часть лотов не добавлена: нужен этап «Интересные лоты» и поддерживаемая площадка Госзакупа.')
    return redirect('draft_queue')


@login_required
@require_POST
def packet(request,item_id):
    item=get_object_or_404(DraftQueueItem,owner=request.user,pk=item_id)
    if item.state in ('complete','cancelled'):
        return JsonResponse({'error':'Этот пункт очереди уже завершён или убран.'},status=400)
    saved=ApplicationPreparation.objects.filter(owner=request.user,lot=item.lot).first()
    if not saved or not PreparationForm(saved.data).is_valid():
        return JsonResponse({'error':'Сначала заполните и сохраните подготовку этого лота.'},status=400)
    try:data=preparation_export_data(request.user,item.lot_id)
    except (ValueError,Http404,PermissionDenied):
        return JsonResponse({'error':'Подготовка недоступна: проверьте БИН, этап и площадку.'},status=400)
    count=data.get('position_count')
    repeat=request.POST.get('repeat_product')=='on'
    if type(count) is not int or count<1:
        return JsonResponse({'error':'Состав лота не подтверждён. Проверьте подготовку в карточке.'},status=400)
    if count>1 and not repeat:
        return JsonResponse({'error':'Подтвердите одинаковый товар для всех позиций именно этого лота.'},status=400)
    data['repeat_product']=repeat
    response=JsonResponse({'packet':data,'queue_context':{'id':item.pk,'generation':str(item.generation)}})
    response['Cache-Control']='no-store, private'
    return response


@login_required
@require_POST
def progress(request,item_id):
    try:
        if len(request.body)>12000:raise ValueError()
        payload=json.loads(request.body)
        if not isinstance(payload,dict) or not isinstance(payload.get('snapshot'),dict):raise ValueError()
        snapshot=payload['snapshot']
        with transaction.atomic():
            company=CompanySettings.objects.select_for_update().get(owner=request.user)
            list(SearchProfile.objects.select_for_update().filter(owner=request.user).order_by('pk'))
            item=get_object_or_404(DraftQueueItem.objects.select_for_update().select_related('lot__announcement'),owner=request.user,pk=item_id)
            if payload.get('generation')!=str(item.generation) or item.state=='cancelled':raise ValueError()
            if (snapshot.get('company_bin')!=company.company_bin or snapshot.get('lot_number')!=item.lot.number or snapshot.get('announcement_number')!=item.lot.announcement.number):raise ValueError()
            state=snapshot.get('status')
            if state not in ('ready','running','paused','blocked','complete'):raise ValueError()
            if item.state=='complete' and state!='complete':raise ValueError()
            has_draft=bool(snapshot.get('number') and snapshot.get('lotConfirmed') is True and snapshot.get('observed_at'))
            if state=='complete' and (not has_draft or snapshot.get('description_saved') is not True):raise ValueError()
            if has_draft:
                number=snapshot.get('number','')
                observed=parse_datetime(str(snapshot.get('observed_at','')))
                if not isinstance(number,str) or not observed or timezone.is_naive(observed) or observed>timezone.now()+timedelta(minutes=2):raise ValueError()
                report={'schema':'goszakup-status-v1','state':'draft','description_saved':state=='complete',
                    'company_bin':company.company_bin,'announcement_number':item.lot.announcement.number,'lot_number':item.lot.number,
                    'number':number,'url':'https://zakup.gov.kz/cabinet/purchase/my-applications/'+str(number),'observed_at':snapshot.get('observed_at')}
                saved=ApplicationPreparation.objects.filter(owner=request.user,lot=item.lot).first()
                if not(saved and saved.company_bin==company.company_bin and saved.portal_number==number and (state!='complete' or saved.portal_description_saved_at) and saved.portal_checked_at and saved.portal_checked_at>=observed):
                    # Preserve idempotency across repeated browser polls.
                    save_portal_report(request.user,item.lot_id,report,allow_create=True)
            item.state=state;item.message=str(snapshot.get('message',''))[:500];item.save(update_fields=['state','message','updated_at'])
            return JsonResponse({'saved':True,'state':item.state,'label':item.get_state_display()})
    except (ValueError,TypeError,KeyError,CompanySettings.DoesNotExist):
        return JsonResponse({'error':'Прогресс не сохранён: проверьте аккаунт, БИН и актуальность очереди.'},status=400)


@login_required
@require_POST
def cancel(request,item_id):
    with transaction.atomic():
        item=get_object_or_404(DraftQueueItem.objects.select_for_update(),owner=request.user,pk=item_id)
        if item.state=='running':
            messages.error(request,'Сначала остановите заполнение в расширении и дождитесь обновления очереди.')
        else:
            item.state='cancelled';item.save(update_fields=['state','updated_at'])
    return redirect('draft_queue')
