"""Opaque account/session binding; never exposes a session cookie or auth secret."""
from django.http import JsonResponse
from django.utils.crypto import salted_hmac
from django.views.decorators.http import require_GET


def identity(request):
    if not request.user.is_authenticated:
        return {'authenticated': False, 'scope': '', 'account': ''}
    account = salted_hmac('private-app-account', str(request.user.pk)).hexdigest()
    session_key = getattr(getattr(request, 'session', None), 'session_key', '') or ''
    scope = salted_hmac('private-app-session', account+':'+session_key+':'+request.user.get_session_auth_hash()).hexdigest()
    return {'authenticated': True, 'scope': scope, 'account': account}


@require_GET
def current(request):
    response = JsonResponse(identity(request))
    response['Cache-Control'] = 'no-store, private'
    return response
