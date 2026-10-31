from .models import Favorite


def favorite_counts(request):
    return {"favorite_count": Favorite.objects.filter(owner=request.user).count() if request.user.is_authenticated else 0}
def procurement_coverage(request):
    if not request.user.is_authenticated or not request.user.is_staff or not request.path.startswith('/search/'):
        return {}
    from .index_service import index_coverage, date_value
    coverage = index_coverage()
    coverage['completed_date'] = date_value(coverage['completed_at'])
    from .medical_index import coverage as medical_coverage
    from .platform_status import platform_statuses
    return {'index_coverage': coverage, 'medical_coverage': medical_coverage(),
            'platform_statuses': platform_statuses(coverage)}
