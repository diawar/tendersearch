from .models import Favorite


def favorite_counts(request):
    return {"favorite_count": Favorite.objects.filter(owner=request.user).count() if request.user.is_authenticated else 0}
def procurement_coverage(request):
    if not request.user.is_authenticated or not request.path.startswith('/search/'):
        return {}
    from .index_service import index_coverage, date_value
    coverage = index_coverage()
    coverage['completed_date'] = date_value(coverage['completed_at'])
    return {'index_coverage': coverage}
