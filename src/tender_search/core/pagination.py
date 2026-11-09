PAGE_SIZES=(10,20,50,100)


def page_settings(request,scope):
    key=scope+'_page_size'
    size=request.session.get(key,20)
    if size not in PAGE_SIZES:size=20
    requested=request.GET.get('per_page')
    if requested in {str(value) for value in PAGE_SIZES}:
        size=int(requested)
        if request.session.get(key)!=size:request.session[key]=size
    return {'per_page':size,'page_size_options':PAGE_SIZES,
        'page_query_items':[(key,value) for key,values in request.GET.lists() if key not in ('page','per_page') for value in values]}
