"""Derive personal favorites and announcement notifications from workflow stages."""
from contextvars import ContextVar
import secrets
from django.db import transaction
from .models import CollectionLot, Favorite, AnnouncementWatch, SearchProfile, Lot, UserLotWorkflow
from django.utils import timezone
from .storage_lifecycle import lock_retention

FAVORITE_STAGES = ('preparing', 'applied', 'waiting_results', 'contract', 'delivery', 'payment', 'won', 'lost')
WATCH_STAGES = ('preparing', 'applied', 'waiting_results', 'contract')
LOCKED_STAGES = ('waiting_results', 'applied', 'contract', 'delivery', 'payment', 'won', 'lost', 'archived')
syncing = ContextVar('workflow_state_syncing', default=False)
WORK_FIELDS = ('stage', 'outcome', 'expired_from_stage', 'result_tracking')


def publish_workflow(item, created=False, update_fields=None):
    """New filter membership inherits work; an explicit edit updates every membership."""
    with transaction.atomic():
        lock_retention(item.lot.announcement_id)
        state, fresh = UserLotWorkflow.objects.select_for_update().get_or_create(
            owner_id=item.profile.owner_id, lot_id=item.lot_id,
            defaults={field: getattr(item, field) for field in WORK_FIELDS})
        if not created and not fresh:
            fields = set(WORK_FIELDS) if update_fields is None else set(WORK_FIELDS) & set(update_fields)
            for field in fields:
                setattr(state, field, getattr(item, field))
            if fields:
                state.save(update_fields=[*fields, 'updated_at'])
        values = {field: getattr(state, field) for field in WORK_FIELDS}
        CollectionLot.objects.filter(profile__owner_id=state.owner_id, lot_id=state.lot_id).update(**values)
        for field, value in values.items():
            setattr(item, field, value)


def update_workflow(owner_id, lot_id, **values):
    with transaction.atomic():
        lock_retention(Lot.objects.values_list("announcement_id",flat=True).get(pk=lot_id))
        state, _ = UserLotWorkflow.objects.select_for_update().get_or_create(owner_id=owner_id, lot_id=lot_id)
        for field, value in values.items():
            setattr(state, field, value)
        state.save(update_fields=[*values, 'updated_at'])
        CollectionLot.objects.filter(profile__owner_id=owner_id, lot_id=lot_id).update(
            **{field:getattr(state, field) for field in WORK_FIELDS}, updated_at=timezone.now())
        sync_lot(owner_id, lot_id)
        return state


@transaction.atomic
def inherit_workflows(owner_id, items):
    """Prepare bulk-created filter memberships without resetting existing decisions."""
    for announcement_id in sorted(set(Lot.objects.filter(pk__in=[item.lot_id for item in items]).values_list("announcement_id",flat=True))):
        lock_retention(announcement_id)
    states = {state.lot_id:state for state in UserLotWorkflow.objects.filter(owner_id=owner_id,
        lot_id__in=[item.lot_id for item in items])}
    for item in items:
        state = states.get(item.lot_id)
        if state:
            for field in WORK_FIELDS:
                setattr(item, field, getattr(state, field))
    UserLotWorkflow.objects.bulk_create([UserLotWorkflow(owner_id=owner_id, lot_id=item.lot_id)
        for item in items if item.lot_id not in states], ignore_conflicts=True)
    return items


def manual_profile(owner_id):
    from django.contrib.auth import get_user_model
    get_user_model().objects.select_for_update().get(pk=owner_id)
    profile = SearchProfile.objects.filter(owner_id=owner_id, filters__manual=True).order_by('pk').first()
    if profile is None:
        profile = SearchProfile.objects.create(owner_id=owner_id, name='Выбранные лоты',
            enabled=False, collection_enabled=False, filters={'manual':True})
    return profile


def delete_profile_preserving_work(owner_id, profile_id):
    with transaction.atomic():
        profiles = list(SearchProfile.objects.select_for_update().filter(owner_id=owner_id).order_by('pk'))
        profile = next((p for p in profiles if p.pk == profile_id), None)
        if profile is None:
            return
        work = CollectionLot.objects.filter(profile=profile).exclude(stage='new')
        if profile.filters.get('manual') and work.exists():
            raise ValueError('В этой подборке сохранена история работы с лотами. Удаление недоступно.')
        # Preserve work even if its filter was the last membership.
        orphans = work.exclude(lot_id__in=CollectionLot.objects.filter(
            profile__owner_id=owner_id).exclude(profile=profile).values('lot_id'))
        if orphans.exists():
            target = manual_profile(owner_id)
            orphans.update(profile=target)
        profile.delete()


def sync_lot(owner_id, lot_id):
    if syncing.get():
        return
    token = syncing.set(True)
    try:
        with transaction.atomic():
            lot = Lot.objects.filter(pk=lot_id).first()
            if not lot:
                return
            stages = CollectionLot.objects.filter(profile__owner_id=owner_id, lot_id=lot_id)
            if stages.exists() and not stages.exclude(stage__in=('not_participated','archived')).exists():
                from .models import TelegramDelivery
                TelegramDelivery.objects.filter(owner_id=owner_id,lot_id=lot_id,state='pending').update(state='cancelled')
            if stages.filter(stage__in=FAVORITE_STAGES).exists():
                Favorite.objects.get_or_create(owner_id=owner_id, lot_id=lot_id)
            else:
                Favorite.objects.filter(owner_id=owner_id, lot_id=lot_id).delete()
            # Watches belong to an announcement: another active lot can keep it on.
            active = CollectionLot.objects.filter(profile__owner_id=owner_id,
                lot__announcement_id=lot.announcement_id, stage__in=WATCH_STAGES).order_by('lot_id').first()
            if active:
                watch, _ = AnnouncementWatch.objects.select_for_update().get_or_create(
                    owner_id=owner_id, announcement_id=lot.announcement_id, defaults={'lot_id':active.lot_id})
                if watch.enabled and watch.lot_id != active.lot_id:
                    from .models import StatusDelivery
                    StatusDelivery.objects.filter(watch=watch, state='pending').update(state='cancelled')
                    watch.generation = secrets.token_hex(16)
                    watch.lease = ''; watch.lease_until = None; watch.next_check_at = None
                if not watch.enabled:
                    watch.enabled = True
                    watch.generation = secrets.token_hex(16)
                    watch.last_status = ''; watch.last_status_name = ''; watch.revision = 0
                    watch.next_check_at = None; watch.lease = ''; watch.lease_until = None; watch.error = ''
                watch.lot_id = active.lot_id
                watch.save()
            else:
                from .status_monitor import cancel_watch
                cancel_watch(owner_id, lot.announcement_id)
    finally:
        syncing.reset(token)


def add_to_preparation(owner_id, lot_id):
    """Take a lot into work; never create a portal application here."""
    from .models import DraftQueueItem
    import uuid
    with transaction.atomic():
        list(SearchProfile.objects.select_for_update().filter(owner_id=owner_id).order_by('pk'))
        records = CollectionLot.objects.filter(profile__owner_id=owner_id, lot_id=lot_id)
        if records.filter(stage__in=LOCKED_STAGES).exists():
            return
        if not records.exists():
            CollectionLot.objects.create(profile=manual_profile(owner_id), lot_id=lot_id, stage='preparing')
        if not records.filter(stage='preparing').exists():
            deadline = Lot.objects.get(pk=lot_id).announcement.deadline
            update_workflow(owner_id, lot_id, stage='preparing', expired_from_stage='',
                outcome='', result_tracking=False, keep_after_deadline=bool(deadline and deadline <= timezone.now()))
        from .models import PurgedUserLot
        PurgedUserLot.objects.filter(owner_id=owner_id,lot_id=lot_id).delete()
        item, _ = DraftQueueItem.objects.get_or_create(owner_id=owner_id, lot_id=lot_id)
        if item.state == 'cancelled':
            item.state='queued'; item.progress_phase=''; item.message=''; item.generation=uuid.uuid4()
            item.save(update_fields=['state','progress_phase','message','generation','updated_at'])
        sync_lot(owner_id, lot_id)


def add_to_interesting(owner_id, lot_id):
    # Compatibility for already delivered Telegram buttons and older integrations.
    return add_to_preparation(owner_id, lot_id)


def remove_from_work(owner_id, lot_id):
    from .models import DraftQueueItem
    import uuid
    with transaction.atomic():
        list(SearchProfile.objects.select_for_update().filter(owner_id=owner_id).order_by('pk'))
        records = CollectionLot.objects.filter(profile__owner_id=owner_id, lot_id=lot_id)
        queue = DraftQueueItem.objects.select_for_update().filter(owner_id=owner_id,lot_id=lot_id).first()
        if records.filter(stage__in=LOCKED_STAGES).exists() or (queue and queue.state in ('running','waiting')):
            return False
        if queue:
            queue.state='cancelled'; queue.generation=uuid.uuid4()
            queue.save(update_fields=['state','generation','updated_at'])
        update_workflow(owner_id, lot_id, stage='not_participated', expired_from_stage='',
            outcome='', result_tracking=False, keep_after_deadline=False)
        from .fulfillment import pause_outcome
        pause_outcome(owner_id,lot_id)
        return True


def reconcile_workflow_states():
    """Repair bulk changes without rewriting unchanged rows or sending repeats."""
    rows = list(CollectionLot.objects.filter(stage__in=FAVORITE_STAGES).values_list(
        'profile__owner_id','lot_id','lot__announcement_id','stage'))
    wanted = {(owner,lot) for owner,lot,ann,stage in rows}
    existing = set(Favorite.objects.values_list('owner_id','lot_id'))
    changes = wanted ^ existing
    active = {(owner,lot) for owner,lot,ann,stage in rows if stage in WATCH_STAGES}
    announcements = {(owner,ann):lot for owner,lot,ann,stage in rows if stage in WATCH_STAGES}
    watches = {(owner,ann):(lot,enabled) for owner,ann,lot,enabled in AnnouncementWatch.objects.values_list('owner_id','announcement_id','lot_id','enabled')}
    for key,lot in announcements.items():
        current = watches.get(key)
        if not current or not current[1] or (key[0],current[0]) not in active:
            changes.add((key[0],lot))
    for (owner,ann),(lot,enabled) in watches.items():
        if enabled and (owner,ann) not in announcements:
            changes.add((owner,lot))
    for owner,lot in sorted(changes):
        sync_lot(owner,lot)
