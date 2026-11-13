"""AJAX transport for existing transitions; permission and stage rules stay in views."""
from functools import wraps
from django.contrib.messages import get_messages, ERROR
from django.http import JsonResponse
from .account_session import identity


def workspace_state(request,profile,kwargs):
    from django.db.models import Count
    from .models import CollectionLot,Favorite,UserLotWorkflow
    from .lot_state import visible_collection_lots
    ids=[]
    if 'item_id' in kwargs:
        ids=list(CollectionLot.objects.filter(pk=kwargs['item_id'],profile__owner=request.user).values_list('lot_id',flat=True))
    elif 'lot_id' in kwargs:ids=[kwargs['lot_id']]
    else:ids=[int(value) for value in request.POST.getlist('lots')[:20] if value.isdecimal()]
    visible=visible_collection_lots(request.user)
    scoped=visible if profile=='all' else visible.filter(profile_id=profile)
    counts={row['stage']:row['count'] for row in scoped.order_by().values('stage').annotate(count=Count('lot_id',distinct=True))}
    counts['all']=counts.pop('new',0)
    return {'profile':profile,'counts':counts,
        'collection_count':visible.exclude(stage='archived').values('lot_id').distinct().count(),
        'favorite_count':Favorite.objects.filter(owner=request.user).count(),
        'lots':list(UserLotWorkflow.objects.filter(owner=request.user,lot_id__in=ids).values('lot_id','stage'))}


def workflow_async(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        if request.headers.get('X-Workflow-Async')!='1':return view(request,*args,**kwargs)
        account=identity(request)
        if not account['authenticated'] or request.headers.get('X-Account-Scope')!=account['scope']:
            response=JsonResponse({'error':'Учётная запись изменилась. Откройте страницу заново.'},status=409)
        else:
            profile=request.headers.get('X-Workspace-Profile')
            if profile is not None and profile!='all':
                from .models import SearchProfile
                if not profile.isdecimal() or not SearchProfile.objects.filter(pk=profile,owner=request.user).exists():
                    response=JsonResponse({'error':'Подборка недоступна.'},status=404)
                    response['Cache-Control']='no-store, private'
                    return response
            response=view(request,*args,**kwargs)
            if response.status_code in (301,302,303):
                messages=[{'text':str(message),'level':message.level} for message in get_messages(request)]
                result={'ok':not any(m['level']>=ERROR for m in messages),
                    'messages':messages,'scope':account['scope'],'owner':str(request.user.pk)}
                if profile is not None:result['workspace']=workspace_state(request,profile,kwargs)
                response=JsonResponse(result)
        response['Cache-Control']='no-store, private'
        return response
    return wrapped
