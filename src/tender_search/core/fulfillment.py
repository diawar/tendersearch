"""Explicit confirmations of fulfillment. Never submit or sign anything on a portal."""
import secrets
from django.db import transaction
from django.utils import timezone
from .models import UserLotWorkflow, CollectionLot, SearchProfile, LotOutcomeWatch, OutcomeDelivery, TelegramDelivery
from .workflow_state import update_workflow

TRANSITIONS = {
    'won': ('contract', 'К договору'),
    'contract': ('delivery', 'Договор заключён'),
    'delivery': ('payment', 'Поставка выполнена'),
    'payment': ('paid', 'Оплата получена'),
}
ARCHIVABLE = ('won', 'lost', 'not_participated')


def actions_for(state):
    if not state:
        return []
    actions = []
    if state.stage in TRANSITIONS:
        action, label = TRANSITIONS[state.stage]
        actions.append({'value':action, 'label':label})
    if state.stage in ARCHIVABLE:
        actions.append({'value':'archive', 'label':'В архив'})
    if state.stage == 'archived':
        actions.append({'value':'restore', 'label':'Вернуть из архива'})
    return actions


def pause_outcome(owner_id, lot_id):
    for watch in LotOutcomeWatch.objects.select_for_update().filter(owner_id=owner_id, lot_id=lot_id):
        watch.enabled = False
        watch.generation = secrets.token_hex(16)
        watch.lease = ''; watch.lease_until = None; watch.next_check_at = None
        watch.save(update_fields=['enabled','generation','lease','lease_until','next_check_at','updated_at'])
        OutcomeDelivery.objects.filter(watch=watch,state='pending').update(state='cancelled')


def advance(owner_id, lot_id, action):
    with transaction.atomic():
        list(SearchProfile.objects.select_for_update().filter(owner_id=owner_id).order_by('pk'))
        state = UserLotWorkflow.objects.select_for_update().filter(owner_id=owner_id,lot_id=lot_id).first()
        if not state or not CollectionLot.objects.filter(profile__owner_id=owner_id,lot_id=lot_id).exists():
            raise ValueError('Лот отсутствует в рабочем пространстве.')
        if action not in {item['value'] for item in actions_for(state)}:
            raise ValueError('Действие недоступно на текущем этапе. Обновите страницу.')
        previous = state.stage
        if action == 'restore':
            previous = state.archived_from_stage
            if previous not in (*ARCHIVABLE, 'payment'):
                raise ValueError('Не удалось определить этап для восстановления.')
            tracking = state.archived_result_tracking and previous in ('won','lost','payment')
            update_workflow(owner_id,lot_id,stage=previous,result_tracking=tracking,
                archived_at=None,archived_from_stage='',archived_result_tracking=False,completed_at=None)
            if tracking:
                # Keep the known verdict: restoring must not send the same result again.
                LotOutcomeWatch.objects.filter(owner_id=owner_id,lot_id=lot_id).update(
                    enabled=True,lease='',lease_until=None,next_check_at=None)
            from .contract_service import invalidate
            invalidate(owner_id, lot_id)
        elif action in ('archive','paid'):
            update_workflow(owner_id,lot_id,stage='archived',result_tracking=False,
                archived_from_stage=previous,archived_result_tracking=state.result_tracking,
                archived_at=timezone.now(),completed_at=timezone.now() if action == 'paid' else None)
            pause_outcome(owner_id,lot_id)
            from .models import ContractWatch
            ContractWatch.objects.filter(workflow=state).update(generation=secrets.token_hex(16),lease='',lease_until=None,next_check_at=None)
            TelegramDelivery.objects.filter(owner_id=owner_id,lot_id=lot_id,state='pending').update(state='cancelled')
        else:
            update_workflow(owner_id,lot_id,stage=action,result_tracking=True)
            from .contract_service import invalidate
            invalidate(owner_id, lot_id)
        return state.stage
