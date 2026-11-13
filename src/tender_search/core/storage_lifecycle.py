"""Coordinate shared-document retention and atomic publication across processes."""
from contextlib import contextmanager
from hashlib import blake2b
from pathlib import Path
from tempfile import TemporaryDirectory
from django.conf import settings
from django.db import connection, transaction


def lock(namespace, value, *, shared=False):
    if not connection.in_atomic_block:
        raise RuntimeError('Storage lock requires a transaction')
    key=int.from_bytes(blake2b(f'{namespace}:{value}'.encode(),digest_size=8).digest(),'big',signed=True)
    function='pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {function}(%s)',[key])


def lock_retention(announcement_id):
    # Use the same row lock as cache writers and joined workflow queries.
    # A second lock namespace here would invert their existing lock order.
    from .models import Announcement
    return Announcement.objects.select_for_update().filter(pk=announcement_id).first()


def lock_path(name):
    lock('document-path',str(Path(name).resolve()))


@contextmanager
def publish_download(api,file,**kwargs):
    # Never let a downloader overwrite the shared SHA path before its DB lease.
    root=Path(settings.MEDIA_ROOT).resolve()
    staging=root/'document-staging';staging.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=staging) as directory:
        result=api.download(file,Path(directory),**kwargs)
        downloaded=Path(result['path']).resolve()
        if not downloaded.is_relative_to(Path(directory).resolve()):
            raise ValueError('Download outside staging directory')
        target=root/downloaded.name
        with transaction.atomic():
            lock_path(target)
            downloaded.replace(target)
            # The caller saves DocumentVersion before releasing this transaction.
            yield {**result,'path':str(target)}
