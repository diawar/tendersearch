from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods
from django.shortcuts import render
from django.urls import reverse
import json
from urllib.parse import urlsplit


FILES = ('manifest.json', 'popup.html', 'popup.css', 'popup.js', 'status.js', 'check.js', 'draft.js', 'background.js', 'bridge-config.js', 'bridge.js', 'README.md')


@login_required
@require_GET
def download_extension(request):
    root=Path(settings.BASE_DIR)/'extensions'/'goszakup-assistant'
    if any(not (root/name).is_file() for name in FILES):
        raise Http404('Сборка расширения отсутствует. Пересоберите приложение.')
    buffer=BytesIO()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    # Chrome match patterns omit ports; the content script and service worker
    # additionally enforce the exact configured application origin, including port.
    host=request.get_host()
    hostname=urlsplit('//'+host).hostname
    local=hostname in ('localhost','127.0.0.1','::1')
    origin=('https' if request.is_secure() or not local else 'http')+'://'+host
    origins=list(dict.fromkeys(['http://localhost:8000','http://127.0.0.1:8000',origin]))
    patterns=list(dict.fromkeys(urlsplit(value).scheme+'://'+urlsplit(value).hostname+path
        for value in origins if urlsplit(value).hostname!='::1' for path in ('/lots/*/preparation/*','/workspace/drafts/')))
    manifest['content_scripts'][0]['matches']=patterns
    with ZipFile(buffer,'w',ZIP_DEFLATED) as archive:
        for name in FILES:
            data=(root/name).read_bytes()
            if name=='manifest.json':data=json.dumps(manifest,ensure_ascii=False).encode('utf-8')
            if name=='bridge-config.js':data=('globalThis.GOSZAKUP_APP_ORIGINS='+json.dumps(origins)+';\n').encode('utf-8')
            archive.writestr('goszakup-assistant/'+name,data)
    response=HttpResponse(buffer.getvalue(),content_type='application/zip')
    response['Content-Disposition']='attachment; filename="goszakup-assistant.zip"'
    response['Cache-Control']='no-store, private'
    return response


@login_required
@require_http_methods(['GET','POST'])
def receive_extension_report(request):
    if request.method=='GET':
        response=render(request,'core/extension_report.html')
    else:
        from .models import UserLotWorkflow
        from .preparation import save_portal_report
        try:
            if len(request.body)>12000:
                raise ValueError('Отчёт слишком большой.')
            report=json.loads(request.body)
            if not isinstance(report,dict):
                raise ValueError('Некорректный отчёт.')
            workflows=list(UserLotWorkflow.objects.filter(owner=request.user,
                lot__announcement__source='zakup',lot__announcement__number=report.get('announcement_number'),
                lot__number=report.get('lot_number')).values_list('lot_id',flat=True)[:2])
            if len(workflows)!=1:
                raise ValueError('Нужный лот не найден однозначно в вашем рабочем пространстве. Добавьте его туда и повторите передачу.')
            if report.get('schema') == 'goszakup-restriction-v1':
                from .restrictions import save_restriction_report
                lot = save_restriction_report(request.user, workflows[0], report)
                response = JsonResponse({'saved': True, 'national_withdrawn': lot.national_withdrawn,
                    'lot_url': reverse('lot_detail', kwargs={'lot_id': lot.pk}),
                    'preparation_url': reverse('lot_detail', kwargs={'lot_id': lot.pk})})
                response['Cache-Control'] = 'no-store, private'
                response['Referrer-Policy'] = 'no-referrer'
                return response
            saved=save_portal_report(request.user,workflows[0],report,allow_create=True)
            stage=UserLotWorkflow.objects.get(owner=request.user,lot_id=saved.lot_id).stage
            response=JsonResponse({'saved':True,'state':saved.portal_state,'number':saved.portal_number,
                'description_saved':bool(saved.portal_description_saved_at),'stage':stage,
                'lot_url':reverse('lot_detail',kwargs={'lot_id':saved.lot_id}),
                'preparation_url':reverse('application_preparation',kwargs={'lot_id':saved.lot_id})})
        except (ValueError,TypeError,UnicodeDecodeError):
            response=JsonResponse({'saved':False,'error':'Не удалось сохранить отчёт. Проверьте БИН в настройках, наличие лота в рабочем пространстве и актуальность отчёта.'},status=400)
    response['Cache-Control']='no-store, private'
    response['Referrer-Policy']='no-referrer'
    return response
