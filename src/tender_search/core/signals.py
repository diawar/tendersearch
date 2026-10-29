from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from .models import Favorite
from django.db import transaction

@receiver(post_delete,sender=Favorite)
def stop_watch_when_removed(sender,instance,**kwargs):
    from .status_monitor import cancel_watch
    cancel_watch(instance.owner_id,instance.lot.announcement_id)

    from .tasks import cleanup_favorite_documents
    announcement_id = instance.lot.announcement_id
    def enqueue_cleanup():
        try:
            cleanup_favorite_documents.delay(announcement_id)
        except Exception:
            from .document_service import cleanup_documents
            cleanup_documents(announcement_id)
    transaction.on_commit(enqueue_cleanup)


@receiver(post_save,sender=Favorite)
def download_when_favorited(sender,instance,created,**kwargs):
    if created:
        from .document_service import queue_documents
        lot_id = instance.lot_id
        transaction.on_commit(lambda: queue_documents(lot_id))
