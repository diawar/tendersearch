"""Personal lot state shared by every representation of a procurement."""
from django.db.models import Exists, OuterRef, Q
from .models import AnnouncementWatch, CollectionLot, Favorite, UserLotWorkflow


def visible_collection_lots(owner):
    return CollectionLot.objects.filter(profile__owner=owner).annotate(
        starred=Exists(Favorite.objects.filter(owner=owner, lot_id=OuterRef('lot_id'))),
        watched=Exists(AnnouncementWatch.objects.filter(owner=owner, enabled=True,
            announcement_id=OuterRef('lot__announcement_id'))),
    ).filter(Q(matches_filter=True) | Q(starred=True) | ~Q(stage='new'))


def collection_lot_count(owner):
    return visible_collection_lots(owner).exclude(stage='archived').order_by().values('lot_id').distinct().count()


def apply_personal_state(owner, lots):
    from .fulfillment import actions_for
    ids = [lot.pk for lot in lots]
    from .models import ApplicationPreparation, CompanySettings
    prepared = {item.lot_id:item for item in ApplicationPreparation.objects.filter(owner=owner,lot_id__in=ids)}
    company_bin = CompanySettings.objects.filter(owner=owner).values_list('company_bin',flat=True).first() if prepared else None
    states = {state.lot_id:state for state in UserLotWorkflow.objects.filter(owner=owner,lot_id__in=ids)}
    from .models import ContractWatch
    contracts = {watch.workflow.lot_id: watch for watch in ContractWatch.objects.filter(
        workflow__owner=owner,workflow__lot_id__in=ids).select_related('workflow')}
    favorites = set(Favorite.objects.filter(owner=owner, lot_id__in=ids).values_list('lot_id', flat=True))
    watched = set(AnnouncementWatch.objects.filter(owner=owner, enabled=True,
        announcement_id__in=[lot.announcement_id for lot in lots]).values_list('announcement_id', flat=True))
    saved = set(visible_collection_lots(owner).filter(lot_id__in=ids).values_list('lot_id', flat=True))
    from .workflow_state import LOCKED_STAGES, WATCH_STAGES
    active = set(CollectionLot.objects.filter(profile__owner=owner,lot_id__in=ids,
        stage__in=WATCH_STAGES).values_list('lot_id',flat=True))
    locked = set(CollectionLot.objects.filter(profile__owner=owner,lot_id__in=ids,
        stage__in=LOCKED_STAGES).values_list('lot_id',flat=True))
    for lot in lots:
        lot.workflow = states.get(lot.pk)
        lot.has_preparation = lot.pk in prepared
        preparation = prepared.get(lot.pk)
        lot.preparation = preparation if preparation and preparation.company_bin == company_bin else None
        lot.contract_watch = contracts.get(lot.pk)
        lot.workflow_actions = actions_for(lot.workflow)
        lot.is_archived = bool(lot.workflow and lot.workflow.stage == 'archived')
        lot.is_favorite = lot.pk in favorites
        lot.is_status_watched = lot.pk in active and lot.announcement_id in watched
        lot.is_in_collection = lot.pk in saved
        lot.favorite_locked = lot.pk in locked
