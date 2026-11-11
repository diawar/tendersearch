"""On-demand technical specifications inside a lot card. No favorite or Telegram side effects."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree
import re
import secrets
from django.conf import settings
from django.utils import timezone
from pypdf import PdfReader
from tender_search.client import SourceError
from .models import LotSpecification,Document
from .source import lot_client
from .document_service import stored_path

MAX_TEXT=160000


def technical_file(file):
    label=((file.group_name or '')+' '+file.name).casefold()
    return bool(re.search(r'технич|техспец|тех[.\s_-]*спец|техникалық|ерекшелік|technical[\s_-]*spec',label))


def extract_text(path, name):
    path=Path(path)
    data=path.read_bytes()
    if len(data)>20*1024*1024:raise SourceError('Файл техспецификации больше 20 МБ.')
    if data.startswith(b'%PDF-'):
        reader=PdfReader(BytesIO(data))
        if reader.is_encrypted or len(reader.pages)>80:raise SourceError('PDF зашифрован или содержит более 80 страниц.')
        text='\n\n'.join(page.extract_text(extraction_mode='layout') or '' for page in reader.pages)
    elif data.startswith(b'PK'):
        with ZipFile(BytesIO(data)) as archive:
            infos=archive.infolist()
            if len(infos)>1000 or sum(info.file_size for info in infos)>30*1024*1024:
                raise SourceError('Офисный документ слишком большой после распаковки.')
            if 'word/document.xml' not in archive.namelist():
                raise SourceError('Поддерживается текст PDF, DOCX и TXT. Этот архив не является DOCX.')
            root=ElementTree.fromstring(archive.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text='\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p',ns))
    elif name.casefold().endswith('.txt'):
        try:text=data.decode('utf-8-sig')
        except UnicodeDecodeError:text=data.decode('cp1251')
    else:raise SourceError('Поддерживается текст PDF, DOCX и TXT. Для этого формата требуется конвертация.')
    text=text.strip()
    if not text:raise SourceError('Текст не извлечён. Возможно, документ является сканом и требует OCR.')
    return text[:MAX_TEXT],len(text)>MAX_TEXT


def queue_specification(owner, lot):
    from .tasks import extract_lot_specification
    job,_=LotSpecification.objects.get_or_create(owner=owner,lot=lot)
    if job.state in ('queued','running') and job.generation and job.updated_at>timezone.now()-timedelta(minutes=15):return
    generation=secrets.token_hex(16)
    LotSpecification.objects.filter(pk=job.pk).update(state='queued',generation=generation,error='',updated_at=timezone.now())
    try:extract_lot_specification.delay(job.pk,generation)
    except Exception:
        LotSpecification.objects.filter(pk=job.pk,generation=generation).update(state='error',error='Очередь извлечения недоступна. Повторите позже.')


def process_specification(job_id, generation):
    if not LotSpecification.objects.filter(pk=job_id,generation=generation,state='queued').update(state='running',updated_at=timezone.now()):return
    job=LotSpecification.objects.select_related('lot__announcement').get(pk=job_id)
    documents=[];errors=[]
    try:
        with lot_client(job.lot_id) as api:
            files=[file for file in api.documents(job.lot.announcement.source_id) if file.lot_id in (None,job.lot.source_id) and technical_file(file)]
            if not files:
                LotSpecification.objects.filter(pk=job_id,generation=generation).update(state='not_found',error='Связанная техническая спецификация не найдена среди файлов портала.',updated_at=timezone.now());return
            work=Path(settings.MEDIA_ROOT)/'specification-work';work.mkdir(parents=True,exist_ok=True)
            with TemporaryDirectory(dir=work) as temporary:
                for file in files[:8]:
                    try:
                        from .technical_documents import cache_file
                        version=cache_file(job.lot,file,api)
                        text=version.text
                        truncated=bool(version.pages and version.pages[0].get('truncated'))
                        documents.append({'name':file.name,'text':text,'source_url':file.url,'scope':'lot' if file.lot_id else 'announcement','truncated':truncated})
                    except Exception as exc:
                        errors.append(str(exc)[:200] if isinstance(exc,SourceError) else 'Не удалось извлечь текст одного из документов.')
                if len(files)>8:errors.append('Показаны первые 8 технических документов.')
        LotSpecification.objects.filter(pk=job_id,generation=generation).update(documents=documents,
            state='partial' if documents and errors else ('done' if documents else 'error'),error=' '.join(errors)[:300],updated_at=timezone.now())
    except Exception as exc:
        LotSpecification.objects.filter(pk=job_id,generation=generation).update(state='error',error=str(exc)[:300] if isinstance(exc,SourceError) else 'Не удалось получить техническую спецификацию.',updated_at=timezone.now())
