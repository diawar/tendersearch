"""Personal lot state shared by every representation of a procurement."""
from django.db.models import Exists, OuterRef, Q
from .models import AnnouncementWatch, CollectionLot, Favorite


def visible_collection_lots(owner):
    return CollectionLot.objects.filter(profile__owner=owner).annotate(
        starred=Exists(Favorite.objects.filter(owner=owner, lot_id=OuterRef('lot_id'))),
        watched=Exists(AnnouncementWatch.objects.filter(owner=owner, enabled=True,
            announcement_id=OuterRef('lot__announcement_id'))),
    ).filter(Q(matches_filter=True) | Q(starred=True) | Q(watched=True) | ~Q(stage='new'))


def collection_lot_count(owner):
    return visible_collection_lots(owner).order_by().values('lot_id').distinct().count()


def apply_personal_state(owner, lots):
    ids = [lot.pk for lot in lots]
    favorites = set(Favorite.objects.filter(owner=owner, lot_id__in=ids).values_list('lot_id', flat=True))
    watched = set(AnnouncementWatch.objects.filter(owner=owner, enabled=True,
        announcement_id__in=[lot.announcement_id for lot in lots]).values_list('announcement_id', flat=True))
    saved = set(visible_collection_lots(owner).filter(lot_id__in=ids).values_list('lot_id', flat=True))
    for lot in lots:
        lot.is_favorite = lot.pk in favorites
        lot.is_status_watched = lot.announcement_id in watched
        lot.is_in_collection = lot.pk in saved
