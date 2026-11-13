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
    from .source import lot_client
    with lot_client(lot_id) as api:
        refresh_lot_data(lot_id, api)

@shared_task(soft_time_limit=1200, time_limit=1230)
def monitor_profile(profile_id, lease):
    from .telegram_service import scan_profile
    scan_profile(profile_id, lease)


@shared_task(soft_time_limit=1200, time_limit=1230)
def refresh_collection(profile_id, lease, revision):
    from .collections_service import update_collection
    update_collection(profile_id, lease, revision)

@shared_task(soft_time_limit=300,time_limit=330)
def check_announcement_status(watch_id,lease):
    from .status_monitor import check_status
    check_status(watch_id,lease)


@shared_task(soft_time_limit=1200, time_limit=1230)
def download_favorite_documents(lot_id):
    from .document_service import download_documents
    from .source import lot_client
    with lot_client(lot_id) as api:
        download_documents(lot_id, api)


@shared_task
def cleanup_favorite_documents(announcement_id):
    from .document_service import cleanup_documents
    cleanup_documents(announcement_id)


@shared_task(soft_time_limit=240, time_limit=270)
def check_lot_outcome(watch_id, lease):
    from .outcome_service import check_outcome
    check_outcome(watch_id, lease)


@shared_task(soft_time_limit=240, time_limit=270)
def check_lot_contract(watch_id, lease):
    from .contract_service import check_contract
    check_contract(watch_id, lease)


@shared_task(soft_time_limit=600, time_limit=630)
def extract_lot_specification(job_id, generation):
    from .specification_service import process_specification
    process_specification(job_id, generation)


@shared_task(soft_time_limit=600, time_limit=630)
def index_technical_documents(index_id):
    from .technical_documents import process
    process(index_id)


@shared_task(soft_time_limit=900,time_limit=930)
def sync_p1_catalog(lease):
    from .p1_sync import process
    process(lease)


@shared_task(soft_time_limit=300,time_limit=330)
def analyze_preparation_product(job_id,generation):
    from .models import ProductAnalysis,CompanySettings,UserLotWorkflow
    from .preparation import preparation_specification_context
    from .lot_pricing import attach_lot_budgets,price_budget,company_vat_rate
    from .product_ai import select_products_ai
    from django.utils import timezone
    if not ProductAnalysis.objects.filter(pk=job_id,generation=generation,state='queued').update(state='running',updated_at=timezone.now()):return
    job=ProductAnalysis.objects.select_related('lot__announcement','owner').get(pk=job_id)
    try:
        if not UserLotWorkflow.objects.filter(owner=job.owner,lot=job.lot,stage='preparing').exists():raise ValueError('Лот уже перешёл на другой этап.')
        documents=preparation_specification_context(job.owner,job.lot)['specification_documents']
        if not documents:raise ValueError('Техническая спецификация пока не загружена.')
        attach_lot_budgets([job.lot]);budget=price_budget(job.lot)
        if not budget:raise ValueError('Бюджет лота не подтверждён.')
        company=CompanySettings.objects.filter(owner=job.owner).first()
        checkpoint=dict(job.result)
        def progress(stage, values):
            checkpoint.update(values);checkpoint['_progress']=stage
            ProductAnalysis.objects.filter(pk=job_id,generation=generation).update(result=checkpoint,updated_at=timezone.now())
        result=select_products_ai(job.lot.name,documents,budget,company_vat_rate(job.owner,company=company),previous=job.result,progress=progress)
        result['checked_at']=timezone.now().isoformat()
        ProductAnalysis.objects.filter(pk=job_id,generation=generation).update(state='done',result=result,error='',updated_at=timezone.now())
    except Exception as error:
        message=str(error)[:300] if isinstance(error,ValueError) else 'Не удалось получить каталог Over-shop. Повторите позже.'
        ProductAnalysis.objects.filter(pk=job_id,generation=generation).update(state='error',error=message,updated_at=timezone.now())
