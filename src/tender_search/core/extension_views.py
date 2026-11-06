from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET


FILES = ('manifest.json', 'popup.html', 'popup.css', 'popup.js', 'status.js', 'README.md')


@login_required
@require_GET
def download_extension(request):
    root=Path(settings.BASE_DIR)/'extensions'/'goszakup-assistant'
    if any(not (root/name).is_file() for name in FILES):
        raise Http404('Сборка расширения отсутствует. Пересоберите приложение.')
    buffer=BytesIO()
    with ZipFile(buffer,'w',ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.writestr('goszakup-assistant/'+name,(root/name).read_bytes())
    response=HttpResponse(buffer.getvalue(),content_type='application/zip')
    response['Content-Disposition']='attachment; filename="goszakup-assistant.zip"'
    response['Cache-Control']='no-store, private'
    return response
