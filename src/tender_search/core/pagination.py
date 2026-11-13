PAGE_SIZES=(10,20,50,100)


def page_numbers(current, total):
    """Keep the first five pages visible, then follow the current page."""
    if total <= 7:
        return list(range(1, total + 1))
    visible = {1, total}
    if current <= 4:
        visible.update(range(1, 6))
    elif current >= total - 3:
        visible.update(range(total - 4, total + 1))
    else:
        visible.update(range(current - 2, current + 3))
    result = []
    for number in sorted(visible):
        if result and number - result[-1] == 2:
            result.append(number - 1)
        elif result and number - result[-1] > 2:
            result.append(None)
        result.append(number)
    return result


def page_settings(request,scope):
    key=scope+'_page_size'
    size=request.session.get(key,20)
    if size not in PAGE_SIZES:size=20
    requested=request.GET.get('per_page')
    if requested in {str(value) for value in PAGE_SIZES}:
        size=int(requested)
        if request.session.get(key)!=size:request.session[key]=size
    return {'per_page':size,'page_size_options':PAGE_SIZES,
        'page_query_items':[(key,value) for key,values in request.GET.lists() if key not in ('page','per_page','remaining') for value in values]}
