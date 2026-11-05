from .models import Favorite, TelegramSubscription
from .lot_state import collection_lot_count
from django.utils.functional import SimpleLazyObject


def favorite_counts(request):
    from .user_admin import can_manage_users
    return {"favorite_count": Favorite.objects.filter(owner=request.user).count() if request.user.is_authenticated else 0,
            "collection_count": SimpleLazyObject(lambda: collection_lot_count(request.user)) if request.user.is_authenticated else 0,
            "telegram_connected": request.user.is_authenticated and TelegramSubscription.objects.filter(owner=request.user, enabled=True, chat_id__isnull=False).exists(),
            "can_manage_users": can_manage_users(request.user)}
def procurement_coverage(request):
    from .user_admin import can_manage_users
    if not can_manage_users(request.user) or not request.path.startswith('/search/'):
        return {}
    from .index_service import index_coverage, date_value
    def load_coverage():
        coverage = index_coverage()
        coverage['completed_date'] = date_value(coverage['completed_at'])
        return coverage
    # Results fragments do not display index statistics. Defer their expensive
    # counts until a template actually uses the administrative coverage block.
    coverage = SimpleLazyObject(load_coverage)
    from .medical_index import coverage as medical_coverage
    from .platform_status import platform_statuses
    return {'index_coverage': coverage, 'medical_coverage': SimpleLazyObject(medical_coverage),
            'platform_statuses': SimpleLazyObject(lambda: platform_statuses(coverage))}
