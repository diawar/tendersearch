"""Purge personal lot work thirty days after archiving, independently per owner."""
from contextvars import ContextVar
from datetime import timedelta
from django.db import transaction
from django.db.models import F,Q
from django.utils import timezone
from . import models as m

purging = ContextVar('purging_personal_lot',default=False)


def enqueue_file(kind,name):
    if name:return m.StoredFileDeletion.objects.get_or_create(kind=kind,name=name)[0]


def drain_file_deletions(limit=100):
    from .document_service import stored_path
    for pk in list(m.StoredFileDeletion.objects.order_by('pk').values_list('pk',flat=True)[:limit]):
        with transaction.atomic():
            entry=m.StoredFileDeletion.objects.select_for_update().filter(pk=pk).first()
            if not entry:continue
            try:
                if entry.kind=='act':
                    if not m.LotShipment.objects.filter(act=entry.name).exists():
                        # Only generated private-act paths, never arbitrary storage names.
                        from pathlib import PurePosixPath
                        path=PurePosixPath(entry.name)
                        if path.is_absolute() or '..' in path.parts or not entry.name.startswith('private-acts/'):
                            raise ValueError('Invalid private-act path')
                        m.LotShipment._meta.get_field('act').storage.delete(entry.name)
                elif entry.kind=='document':
                    from .storage_lifecycle import lock_path
                    lock_path(entry.name)
                    if not m.DocumentVersion.objects.filter(storage_path=entry.name).exists():
                        stored_path(entry.name).unlink(missing_ok=True)
                else:raise ValueError('Unknown storage kind')
            except Exception:
                m.StoredFileDeletion.objects.filter(pk=pk).update(attempts=F('attempts')+1,updated_at=timezone.now())
            else:entry.delete()


def purge_archived_lots(*,now=None,limit=100):
    cutoff=(now or timezone.now())-timedelta(days=30)
    due=m.UserLotWorkflow.objects.filter(stage='archived',archived_at__lte=cutoff)
    candidates=list(due.order_by('archived_at','pk').values_list('owner_id','lot_id')[:limit])
    removed=0
    for owner_id,lot_id in candidates:
        announcement_id=None
        with transaction.atomic():
            # Same owner/profile/workflow lock order as restore and user actions.
            list(m.SearchProfile.objects.select_for_update().filter(owner_id=owner_id).order_by('pk'))
            state=due.select_for_update().select_related('lot').filter(owner_id=owner_id,lot_id=lot_id).first()
            if not state:continue
            announcement_id=state.lot.announcement_id
            m.PurgedUserLot.objects.get_or_create(owner_id=owner_id,lot_id=lot_id)
            for name in m.LotShipment.objects.filter(owner_id=owner_id,lot_id=lot_id).exclude(act='').values_list('act',flat=True):
                enqueue_file('act',name)
            # Cascades erase contract evidence and outcome notification payloads.
            for model in (m.ApplicationPreparation,m.LotDealEstimate,m.UserLotPrice,m.UserLotNote,
                          m.LotObligationCosts,m.LotShipment,m.DraftQueueItem,m.ProductAnalysis,
                          m.LotSpecification,m.WorkflowDelivery,m.TelegramDelivery,m.LotOutcomeWatch):
                model.objects.filter(owner_id=owner_id,lot_id=lot_id).delete()
            m.SearchHit.objects.filter(search__owner_id=owner_id,lot_id=lot_id).delete()
            m.Match.objects.filter(run__profile__owner_id=owner_id).filter(Q(lot_id=lot_id)|Q(lot__isnull=True,version__document__lot_id=lot_id)).delete()
            from .workflow_state import syncing
            token=syncing.set(True);purge_token=purging.set(True)
            try:
                m.CollectionLot.objects.filter(profile__owner_id=owner_id,lot_id=lot_id).delete()
                m.Favorite.objects.filter(owner_id=owner_id,lot_id=lot_id).delete()
                state.delete()
            finally:
                purging.reset(purge_token);syncing.reset(token)
            # An announcement watch can still belong to another lot of this user.
            m.AnnouncementWatch.objects.filter(owner_id=owner_id,lot_id=lot_id).delete()
            sibling=m.CollectionLot.objects.filter(profile__owner_id=owner_id,
                lot__announcement_id=announcement_id).order_by('lot_id').first()
            if sibling:
                from .workflow_state import sync_lot
                sync_lot(owner_id,sibling.lot_id)
            else:
                m.AnnouncementWatch.objects.filter(owner_id=owner_id,announcement_id=announcement_id).delete()
                from .telegram_service import announcement_key
                key=announcement_key(state.lot)
                m.TelegramSeen.objects.filter(profile__owner_id=owner_id,announcement_key=key).delete()
            from .document_service import cleanup_documents
            transaction.on_commit(lambda identifier=announcement_id:cleanup_documents(identifier))
            transaction.on_commit(drain_file_deletions)
            removed+=1
    return removed


def cleanup_archived_lots():
    from django.core.cache import cache
    if not cache.add('personal-archive-retention-hour',True,timeout=3600):return
    try:
        removed=purge_archived_lots()
        drain_file_deletions()
    except Exception:
        cache.delete('personal-archive-retention-hour')
        raise
    # Drain a backlog in bounded batches on subsequent monitor ticks.
    if removed==100:cache.delete('personal-archive-retention-hour')


def collect_orphan_acts():
    """Reclaim legacy unreferenced uploads; fresh uploads have durable intents."""
    storage=m.LotShipment._meta.get_field('act').storage
    cutoff=timezone.now()-timedelta(days=1)
    pending=['private-acts']
    while pending:
        directory=pending.pop()
        try:directories,files=storage.listdir(directory)
        except FileNotFoundError:continue
        pending.extend(f'{directory}/{name}' for name in directories)
        for filename in files:
            name=f'{directory}/{filename}'
            try:
                if storage.get_modified_time(name)<=cutoff and not m.LotShipment.objects.filter(act=name).exists():
                    enqueue_file('act',name)
            except FileNotFoundError:continue
