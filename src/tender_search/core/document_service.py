from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from tender_search.client import SourceError
from .models import Announcement, Document, DocumentVersion, Favorite, Lot, LotDocumentDownload


def stored_path(value):
    root = Path(settings.MEDIA_ROOT).resolve()
    path = Path(value).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Invalid document path')
    return path


def queue_documents(lot_id):
    from .tasks import download_favorite_documents
    if not Favorite.objects.filter(lot_id=lot_id).exists():
        return
    batch, created = LotDocumentDownload.objects.get_or_create(lot_id=lot_id)
    if not created and (batch.state == 'done' or (batch.state in {'queued', 'running'} and batch.updated_at > timezone.now()-timedelta(minutes=25))):
        return
    LotDocumentDownload.objects.filter(pk=batch.pk).update(state='queued', error='', updated_at=timezone.now())
    try:
        download_favorite_documents.delay(lot_id)
    except Exception:
        LotDocumentDownload.objects.filter(pk=batch.pk).update(state='error',error='Очередь недоступна. Повторите загрузку.')


def download_documents(lot_id, api):
    if not LotDocumentDownload.objects.filter(lot_id=lot_id,state='queued').update(state='running',updated_at=timezone.now()):
        return
    lot = Lot.objects.select_related('announcement').get(pk=lot_id)
    errors = 0
    try:
        if not Favorite.objects.filter(lot=lot).exists():
            return
        files = api.documents(lot.announcement.source_id)
        for file in files:
            if file.lot_id not in {None, lot.source_id}:
                continue
            if not Favorite.objects.filter(lot=lot).exists():
                break
            doc, _ = Document.objects.update_or_create(announcement=lot.announcement,source_id=file.id,
                defaults={'lot':lot if file.lot_id else None,'name':file.name,'url':file.url})
            cached = doc.versions.order_by('-pk').first()
            if cached and stored_path(cached.storage_path).is_file():
                Document.objects.filter(pk=doc.pk).update(status='ready',error='')
                continue
            try:
                Document.objects.filter(pk=doc.pk).update(status='downloading',error='')
                result = api.download(file,Path(settings.MEDIA_ROOT))
                DocumentVersion.objects.update_or_create(document=doc,sha256=result['sha256'],defaults={'storage_path':result['path']})
                Document.objects.filter(pk=doc.pk).update(status='ready',error='')
            except Exception as exc:
                errors += 1
                Document.objects.filter(pk=doc.pk).update(status='error',error=str(exc)[:200] if isinstance(exc,SourceError) else 'Не удалось скачать файл.')
        LotDocumentDownload.objects.filter(lot=lot).update(state='partial' if errors else 'done',error='Некоторые файлы не удалось скачать. Повторите загрузку.' if errors else '',updated_at=timezone.now())
    except Exception as exc:
        LotDocumentDownload.objects.filter(lot=lot).update(state='error',error=str(exc)[:200] if isinstance(exc,SourceError) else 'Не удалось получить документы.',updated_at=timezone.now())
    finally:
        cleanup_documents(lot.announcement_id)


def cleanup_documents(announcement_id):
    paths = []
    with transaction.atomic():
        Announcement.objects.select_for_update().filter(pk=announcement_id).first()
        favorites = Favorite.objects.filter(lot__announcement_id=announcement_id)
        for doc in Document.objects.filter(announcement_id=announcement_id):
            needed = favorites.filter(lot_id=doc.lot_id).exists() if doc.lot_id else favorites.exists()
            if not needed:
                paths.extend(doc.versions.values_list('storage_path',flat=True))
                doc.delete()
        LotDocumentDownload.objects.filter(lot__announcement_id=announcement_id).exclude(lot_id__in=Favorite.objects.values('lot_id')).update(state='cancelled',error='')
        def remove_files():
            for value in set(paths):
                if not DocumentVersion.objects.filter(storage_path=value).exists():
                    try:
                        stored_path(value).unlink(missing_ok=True)
                    except (OSError, ValueError):
                        pass
        transaction.on_commit(remove_files)
