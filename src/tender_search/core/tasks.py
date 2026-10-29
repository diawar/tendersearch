from celery import shared_task


@shared_task
def service_check():
    """Verifies that a worker can receive and complete a task."""
    return {"status": "ok"}


@shared_task(soft_time_limit=1200, time_limit=1230)
def web_search(job_id):
    from .index_service import execute_local_search
    execute_local_search(job_id)


@shared_task
def refresh_lot(lot_id):
    from .search_service import refresh_lot_data
    from .source import source_client
    with source_client() as api:
        refresh_lot_data(lot_id, api)

@shared_task(soft_time_limit=1200, time_limit=1230)
def monitor_profile(profile_id, lease):
    from .telegram_service import scan_profile
    scan_profile(profile_id, lease)

@shared_task(soft_time_limit=120,time_limit=150)
def check_announcement_status(watch_id,lease):
    from .status_monitor import check_status
    check_status(watch_id,lease)


@shared_task(soft_time_limit=1200, time_limit=1230)
def download_favorite_documents(lot_id):
    from .document_service import download_documents
    from .source import source_client
    with source_client() as api:
        download_documents(lot_id, api)


@shared_task
def cleanup_favorite_documents(announcement_id):
    from .document_service import cleanup_documents
    cleanup_documents(announcement_id)
