"""Checks start only for explicitly applied collection lots and stay independent of Telegram polling."""
from datetime import timedelta
from hashlib import sha256
from html import escape
import secrets
from django.db import transaction
from django.db.models import Q, F, Exists, OuterRef
from django.utils import timezone
from tender_search.client import SourceError
from .models import CompanySettings, CollectionLot, LotOutcomeWatch, OutcomeDelivery, SearchProfile, TelegramSubscription, UserLotPrice
from .source import source_client
from .outcome_source import OutcomePortal, Outcome, ProtocolFormatError
from .telegram_service import telegram_api, TelegramError

TRACKED_STAGES = ('applied','won','lost')


def company_bin_for(owner_id):
    return CompanySettings.objects.filter(owner_id=owner_id).values_list('company_bin',flat=True).first() or ''


def tracked_items(owner_id, lot_id):
    return CollectionLot.objects.filter(profile__owner_id=owner_id,lot_id=lot_id,result_tracking=True,stage__in=TRACKED_STAGES)


def register_watch(owner_id, lot_id):
    company_bin=company_bin_for(owner_id)
    with transaction.atomic():
        watch,created=LotOutcomeWatch.objects.select_for_update().get_or_create(owner_id=owner_id,lot_id=lot_id,
            defaults={'company_bin':company_bin,'state':'pending' if company_bin else 'no_bin'})
        if not created:
            reset=not watch.enabled or watch.company_bin!=company_bin
            watch.enabled=True;watch.next_check_at=None
            if reset:
                watch.company_bin=company_bin;watch.generation=secrets.token_hex(16)
                watch.state='pending' if company_bin else 'no_bin';watch.winner_bin='';watch.winner_name=''
                watch.protocol_url='';watch.fingerprint='';watch.revision=0;watch.lease='';watch.lease_until=None;watch.error=''
                OutcomeDelivery.objects.filter(watch=watch,state='pending').update(state='cancelled')
            watch.save()
    return watch


def reset_company(owner_id, new_bin):
    with transaction.atomic():
        company,_=CompanySettings.objects.select_for_update().get_or_create(owner_id=owner_id)
        if company.company_bin==new_bin:return
        company.company_bin=new_bin;company.save(update_fields=['company_bin','updated_at'])
        list(SearchProfile.objects.select_for_update().filter(owner_id=owner_id).order_by('pk'))
        CollectionLot.objects.filter(profile__owner_id=owner_id,result_tracking=True,stage__in=['won','lost']).update(stage='applied',outcome='')
        watches=LotOutcomeWatch.objects.select_for_update().filter(owner_id=owner_id)
        for watch in watches:
            watch.company_bin=new_bin;watch.generation=secrets.token_hex(16)
            watch.state='pending' if new_bin else 'no_bin';watch.winner_bin='';watch.winner_name=''
            watch.protocol_url='';watch.fingerprint='';watch.revision=0;watch.lease='';watch.lease_until=None
            watch.next_check_at=None;watch.error='';watch.save()
            OutcomeDelivery.objects.filter(watch=watch,state='pending').update(state='cancelled')


def schedule_outcomes():
    from .tasks import check_lot_outcome
    now=timezone.now()
    candidates=CollectionLot.objects.filter(result_tracking=True,stage__in=TRACKED_STAGES,profile__owner__is_active=True)
    candidates=candidates.annotate(has_watch=Exists(LotOutcomeWatch.objects.filter(owner_id=OuterRef('profile__owner_id'),lot_id=OuterRef('lot_id')))).filter(has_watch=False)
    for owner_id,lot_id in candidates.values_list('profile__owner_id','lot_id').distinct()[:100]:
        register_watch(owner_id,lot_id)
    watches=LotOutcomeWatch.objects.filter(enabled=True,owner__is_active=True).filter(
        Q(next_check_at__isnull=True)|Q(next_check_at__lte=now)).filter(Q(lease_until__isnull=True)|Q(lease_until__lte=now))
    for watch in watches.order_by('next_check_at','pk')[:30]:
        if not tracked_items(watch.owner_id,watch.lot_id).exists():
            LotOutcomeWatch.objects.filter(pk=watch.pk).update(enabled=False,lease='',lease_until=None)
            OutcomeDelivery.objects.filter(watch=watch,state='pending').update(state='cancelled');continue
        lease=secrets.token_hex(16)
        claimed=LotOutcomeWatch.objects.filter(pk=watch.pk,enabled=True).filter(Q(lease_until__isnull=True)|Q(lease_until__lte=now)).update(lease=lease,lease_until=now+timedelta(minutes=10))
        if claimed:
            try:check_lot_outcome.delay(watch.pk,lease)
            except Exception:
                LotOutcomeWatch.objects.filter(pk=watch.pk,lease=lease).update(lease='',lease_until=None,
                    next_check_at=now+timedelta(minutes=5),error='Очередь проверки итогов недоступна.')


def supported_source(lot):
    if lot.announcement.source!='zakup' or not lot.announcement.external_id or not lot.number or not lot.announcement.number:return False
    ids=set()
    for raw in (lot.raw or {},lot.announcement.raw or {}):
        system=raw.get('system')
        for value in (raw.get('system_id'),system.get('id') if isinstance(system,dict) else system):
            if value is not None and value!='':ids.add(str(value))
    return ids=={'1'}


def outcome_payload(watch):
    titles={'won':'🏆 Выиграли тендер','lost':'📄 Итоги: победил другой поставщик','review':'🔎 Итоги требуют повторной проверки',
            'not_listed':'🔎 Проверьте подачу заявки','cancelled':'🚫 Закупка отменена','unsuccessful':'📄 Закупка не состоялась'}
    esc=lambda value:escape(str(value or ''))
    names=list(tracked_items(watch.owner_id,watch.lot_id).values_list('profile__name',flat=True).distinct())
    text=f'{titles.get(watch.state,"📄 Итоги закупки")}\n\n<b>{esc(watch.lot.name[:600])}</b>\nЛот: <code>{esc(watch.lot.number)}</code>\nБИН компании: <code>{esc(watch.company_bin)}</code>'
    if names:text+='\n📂 '+esc(' · '.join(names)[:300])
    if watch.winner_bin:text+=f'\n\nПобедитель: {esc(watch.winner_name[:400])}\nБИН: <code>{esc(watch.winner_bin)}</code>'
    if watch.state=='not_listed':text+='\n\nВ протоколе нужного лота ваш БИН не найден. Сохранение цены в приложении не означает подачу заявки на портале.'
    elif watch.state=='review':text+='\n\nРанее подтверждённый результат нельзя считать актуальным. Требуется повторная проверка протокола.'
    text+='\n\nРезультат проверен по опубликованному протоколу нужного лота.' if watch.state in ('won','lost') else ''
    buttons=[[{'text':'Открыть закупку ↗','url':watch.lot.portal_url}]]
    if watch.protocol_url:buttons.insert(0,[{'text':'Протокол итогов ↗','url':watch.protocol_url}])
    return {'text':text,'parse_mode':'HTML','link_preview_options':{'is_disabled':True},'reply_markup':{'inline_keyboard':buttons}}


def check_outcome(watch_id, lease):
    snapshot=LotOutcomeWatch.objects.filter(pk=watch_id,enabled=True,lease=lease,owner__is_active=True).select_related('lot__announcement').first()
    if not snapshot:return
    try:
        if not tracked_items(snapshot.owner_id,snapshot.lot_id).exists():return
        current_bin=company_bin_for(snapshot.owner_id)
        if not current_bin: result=Outcome('no_bin')
        elif current_bin!=snapshot.company_bin:return
        elif not supported_source(snapshot.lot):result=Outcome('unsupported')
        else:
            try:
                with source_client(snapshot.lot.announcement.source) as api:
                    result=OutcomePortal(api).outcome(snapshot.lot.announcement.external_id,snapshot.lot.announcement.number,snapshot.lot.number,snapshot.company_bin)
            except ProtocolFormatError as exc:
                result=Outcome('review');result.error=str(exc)[:300]
        with transaction.atomic():
            company=CompanySettings.objects.select_for_update().filter(owner_id=snapshot.owner_id).first()
            if (company.company_bin if company else '')!=snapshot.company_bin:return
            list(SearchProfile.objects.select_for_update().filter(owner_id=snapshot.owner_id).order_by('pk'))
            items=list(tracked_items(snapshot.owner_id,snapshot.lot_id).select_for_update())
            current=LotOutcomeWatch.objects.select_for_update().filter(pk=watch_id,enabled=True,lease=lease,generation=snapshot.generation).select_related('lot__announcement').first()
            if not current or not items:return
            old_state=current.state
            changed=(old_state,current.winner_bin)!=(result.state,result.winner_bin)
            current.state=result.state;current.winner_bin=result.winner_bin;current.winner_name=result.winner_name
            current.protocol_url=result.protocol_url;current.fingerprint=result.fingerprint
            current.checked_at=timezone.now();current.error=getattr(result,'error','')
            current.next_check_at=current.checked_at+timedelta(hours=24 if result.state in ('won','lost') else (6 if result.state in ('unsupported','no_bin') else 1))
            if changed:current.revision+=1
            current.save()
            if result.state in ('won','lost','cancelled','unsuccessful'):
                for item in items:
                    item.stage=result.state if result.state in ('won','lost') else 'applied';item.outcome=result.state if result.state in ('won','lost') else ''
                    item.save(update_fields=['stage','outcome','updated_at'])
            elif old_state in ('won','lost'):
                for item in items:
                    if item.stage in ('won','lost'):
                        item.stage='applied';item.outcome='';item.save(update_fields=['stage','outcome','updated_at'])
            if changed and (result.state in ('won','lost','not_listed','cancelled','unsuccessful') or old_state in ('won','lost')):
                OutcomeDelivery.objects.filter(watch=current,state='pending').update(state='cancelled')
                OutcomeDelivery.objects.get_or_create(event_key=f'{current.pk}:{current.generation}:{current.revision}',
                    defaults={'watch':current,'generation':current.generation,'revision':current.revision,'payload':outcome_payload(current)})
    except Exception as exc:
        LotOutcomeWatch.objects.filter(pk=watch_id,lease=lease).update(error=str(exc)[:300] if isinstance(exc,SourceError) else 'Не удалось проверить итоги. Повторим позже.',next_check_at=timezone.now()+timedelta(hours=1))
    finally:
        LotOutcomeWatch.objects.filter(pk=watch_id,lease=lease).update(lease='',lease_until=None)


def deliver_outcomes():
    now=timezone.now()
    OutcomeDelivery.objects.filter(state='sending',updated_at__lt=now-timedelta(minutes=5)).update(state='uncertain',error='Отправка не подтверждена; повтор отключён.')
    deliveries=OutcomeDelivery.objects.filter(state='pending',watch__owner__is_active=True).filter(Q(retry_at__isnull=True)|Q(retry_at__lte=now)).select_related('watch')
    for delivery in deliveries[:20]:
        watch=delivery.watch
        if not watch.enabled or watch.generation!=delivery.generation or watch.revision!=delivery.revision or not tracked_items(watch.owner_id,watch.lot_id).exists():
            OutcomeDelivery.objects.filter(pk=delivery.pk).update(state='cancelled');continue
        subscription=TelegramSubscription.objects.filter(owner_id=watch.owner_id,enabled=True,chat_id__isnull=False).first()
        if not subscription:continue
        if not OutcomeDelivery.objects.filter(pk=delivery.pk,state='pending').update(state='sending',updated_at=timezone.now()):continue
        try:
            result=telegram_api('sendMessage',dict(delivery.payload,chat_id=subscription.chat_id))
            OutcomeDelivery.objects.filter(pk=delivery.pk).update(state='sent',message_id=result['message_id'],error='')
        except TelegramError as exc:
            if exc.code==429:
                OutcomeDelivery.objects.filter(pk=delivery.pk).update(state='pending',retry_at=now+timedelta(seconds=max(1,exc.retry_after)),error='Лимит Telegram.');return
            if exc.code==403:TelegramSubscription.objects.filter(pk=subscription.pk).update(enabled=False)
            OutcomeDelivery.objects.filter(pk=delivery.pk).update(state='uncertain' if exc.code==0 else 'error',error=f'Telegram: {exc.code}')


def move_closed_unsubmitted():
    now=timezone.now()
    records=CollectionLot.objects.filter(profile__owner__is_active=True,result_tracking=False)
    records.filter(stage__in=['new','interesting'],lot__announcement__deadline__lte=now).update(expired_from_stage=F('stage'),stage='not_participated')
    # Source extensions reopen only automatically moved lots, never submitted or decided ones.
    records.filter(stage='not_participated',expired_from_stage__in=['new','interesting'],lot__announcement__deadline__gt=now).update(stage=F('expired_from_stage'),expired_from_stage='')


def cancel_application(owner, lot):
    """Undo the app's test submission mark; never contact the procurement portal."""
    with transaction.atomic():
        list(SearchProfile.objects.select_for_update().filter(owner=owner).order_by('pk'))
        items=list(CollectionLot.objects.select_for_update().filter(profile__owner=owner,lot=lot))
        applied=[item for item in items if item.stage=='applied']
        price=UserLotPrice.objects.select_for_update().filter(owner=owner,lot=lot).first()
        if not applied or not price or not price.locked_at:
            raise ValueError('Нет отметки о подаче для отмены.')
        if any(item.stage in ('won','lost','contract','delivery','payment') for item in items):
            raise ValueError('Лот уже перешёл к итогам или исполнению.')
        watch=LotOutcomeWatch.objects.select_for_update().filter(owner=owner,lot=lot).first()
        if watch:
            watch.enabled=False;watch.generation=secrets.token_hex(16)
            watch.lease='';watch.lease_until=None;watch.next_check_at=None
            watch.state='pending';watch.winner_bin='';watch.winner_name=''
            watch.protocol_url='';watch.fingerprint='';watch.checked_at=None;watch.error=''
            watch.save()
            OutcomeDelivery.objects.filter(watch=watch,state='pending').update(state='cancelled')
        price.locked_at=None;price.save(update_fields=['locked_at','updated_at'])
        CollectionLot.objects.filter(pk__in=[item.pk for item in applied]).update(
            stage='interesting',outcome='',result_tracking=False,expired_from_stage='',updated_at=timezone.now())
