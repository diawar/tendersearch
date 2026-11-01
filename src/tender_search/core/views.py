from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import DateTimeField, F, Q
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from django.http import JsonResponse, FileResponse, Http404
from django.shortcuts import render
from django.shortcuts import get_object_or_404, redirect
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from datetime import timedelta
from urllib.parse import urlsplit
from django.urls import reverse

from .models import Favorite, Notification, SearchProfile, WebSearch, Lot, TelegramSubscription, TelegramDelivery, AnnouncementWatch, StatusDelivery, Document, DocumentVersion, LotDocumentDownload
from .forms import SearchForm
from . import tasks
from tender_search.matching import matches
from tender_search.client import utc_datetime, SchemaError


@login_required
def index_status(request):
    if not request.user.is_staff:
        raise PermissionDenied
    return render(request, 'core/_index_coverage.html')


@login_required
def dashboard(request):
    return render(request, "core/dashboard.html", {
        "profiles": SearchProfile.objects.filter(owner=request.user).count(),
        "tracked": SearchProfile.objects.filter(owner=request.user, enabled=True).count(),
        "favorites": Favorite.objects.filter(owner=request.user).count(),
        "favorites_tracked": Favorite.objects.filter(owner=request.user,lot__announcement_id__in=AnnouncementWatch.objects.filter(owner=request.user,enabled=True).values("announcement_id")).count(),
        "notifications": Notification.objects.filter(owner=request.user, read_at__isnull=True).count(),
    })


@login_required
def search(request):
    initial = None
    profile = None
    if request.GET.get("profile"):
        profile = get_object_or_404(SearchProfile, pk=request.GET["profile"], owner=request.user)
        initial = profile.filters.get("form", {})
    form = SearchForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        if request.POST.get("action") == "save":
            values = {key: (request.POST.getlist(key) if key in {"region", "city", "status", "method"} else request.POST.get(key, "")) for key in form.fields}
            values["_defaults_version"] = 1
            values["kind"] = form.cleaned_data["kind"]
            values["terms"] = "; ".join(form.cleaned_data["terms"])
            name = request.POST.get("profile_name", "").strip()[:200] or " · ".join(form.cleaned_data["terms"])[:200]
            settings = dict(name=name, include_terms=form.cleaned_data["terms"], exclude_terms=form.cleaned_data["exclude"],
                mode=form.cleaned_data["mode"], filters={"form": values, "specification": form.specification()})
            if request.POST.get("profile_id"):
                profile = get_object_or_404(SearchProfile, pk=request.POST["profile_id"], owner=request.user)
                if profile.filters != settings["filters"]:
                    profile.baseline_at = None
                    profile.index_baseline_at = None
                    profile.next_monitor_at = None
                    profile.monitor_lease = ""
                    profile.monitor_lease_until = None
                    TelegramDelivery.objects.filter(profile=profile,state="pending").update(state="cancelled")
                for key, value in settings.items():
                    setattr(profile, key, value)
                profile.save()
            else:
                profile = SearchProfile.objects.create(owner=request.user, **settings)
            return redirect(f"{request.path}?profile={profile.pk}")
        if WebSearch.objects.filter(owner=request.user, status__in=["queued", "running"],
                                    created_at__gte=timezone.now()-timedelta(minutes=25)).count() >= 2:
            form.add_error(None, "У вас уже выполняются два поиска. Дождитесь завершения одного из них.")
        else:
            specification = form.specification()
            if request.POST.get("profile_id"):
                selected_profile = get_object_or_404(SearchProfile, pk=request.POST["profile_id"], owner=request.user)
                specification["profile_id"] = selected_profile.pk
            job = WebSearch.objects.create(owner=request.user, specification=specification)
            try:
                tasks.web_search.delay(str(job.pk))
            except Exception:
                job.status, job.error = "error", "Очередь поиска недоступна. Повторите попытку позже."
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "error", "finished_at"])
            return redirect(f"{reverse('search_results', kwargs={'job_id': job.pk})}#search-results")
    return render(request, "core/search.html", {"form": form, "active_profile": profile, "profile_saved": bool(profile) and not form.is_bound, "profiles": SearchProfile.objects.filter(owner=request.user).order_by("-updated_at"), "recent": WebSearch.objects.filter(owner=request.user, specification__monitor_profile_id__isnull=True).order_by("-created_at")[:7]})


@login_required
@require_POST
def profile_delete(request, profile_id):
    get_object_or_404(SearchProfile, pk=profile_id, owner=request.user).delete()
    return redirect("search")


def result_context(request, lots):
    favorite_ids = set(Favorite.objects.filter(owner=request.user, lot_id__in=[lot.pk for lot in lots]).values_list("lot_id", flat=True))
    watched = set(AnnouncementWatch.objects.filter(owner=request.user,enabled=True,announcement_id__in=[lot.announcement_id for lot in lots]).values_list("announcement_id",flat=True))
    for lot in lots:
        try:
            lot.offer_start = utc_datetime(lot.raw.get("offer_start_date"))
        except (ValueError, TypeError, SchemaError):
            lot.offer_start = None
        lot.is_favorite = lot.pk in favorite_ids
        lot.is_status_watched = lot.announcement_id in watched


@login_required
@require_POST
def search_cancel(request, job_id):
    job = get_object_or_404(WebSearch, pk=job_id, owner=request.user)
    WebSearch.objects.filter(pk=job.pk, status__in=['queued', 'running']).update(
        status='cancelled', complete=False, error='', finished_at=timezone.now(), updated_at=timezone.now())
    return redirect('search_results', job_id=job.pk)


@login_required
def search_results(request, job_id):
    if request.GET.get("view") in {"table", "cards"}:
        request.session["result_view"] = request.GET["view"]
    job = get_object_or_404(WebSearch, pk=job_id, owner=request.user)
    if job.status in {"queued", "running"} and job.updated_at < timezone.now()-timedelta(minutes=25):
        job.status = "partial" if job.hits.exists() else "error"
        job.error = "Поиск не завершился вовремя. Сохранённые результаты доступны; можно запустить новый поиск."
        job.complete = False
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "complete", "finished_at"])
    hits = job.hits.select_related("lot__announcement", "lot__customer").annotate(
        publication=Cast(KeyTextTransform("announcement_publish_date", "lot__raw"), DateTimeField())
    ).order_by(F("publication").desc(nulls_last=True), "-lot__source_id")
    remaining_days = request.GET.get('remaining', '')
    if remaining_days in {'1', '2'}:
        now = timezone.now()
        hits = hits.filter(lot__announcement__deadline__gt=now,
                           lot__announcement__deadline__lte=now+timedelta(days=int(remaining_days)))
    else:
        remaining_days = ''
    excluded_terms = job.specification.get("exclude", [])
    if excluded_terms:
        excluded_ids = [identifier for identifier, name, description in hits.values_list("pk", "lot__name", "lot__description").iterator()
                        if any(matches(f"{name}\n{description}", term, "substring") for term in excluded_terms)]
        hits = hits.exclude(pk__in=excluded_ids)
    page = Paginator(hits, 25).get_page(request.GET.get("page"))
    result_context(request, [hit.lot for hit in page])
    template = "core/_results.html" if request.headers.get("HX-Request") else "core/results.html"
    spec = job.specification
    initial = {"terms": "; ".join(spec.get("terms", [])), "exclude": "; ".join(spec.get("exclude", [])),
               "mode": spec.get("mode", "any"), "kind": spec.get("kind", "contains"), "pages": spec.get("pages", 1), "depth": spec.get("depth", 500),
               "customer_bin": spec.get("customer_bin", ""),
               "only_advance": spec.get("filters", {}).get("only_advance", False),
               "only_medical": spec.get("filters", {}).get("only_medical", False),
               "subject": spec.get("filters", {}).get("subject", "")}
    for field, param in [("amount_min", "total_price__gte"), ("amount_max", "total_price__lte"),
                         ("deadline_from", "offer_end_date__gte"), ("deadline_to", "offer_end_date__lte"),
                         ("status", "status_id"), ("method", "purchase_method_id"), ("region", "delivery_address_id__in")]:
        value = spec.get("filters", {}).get(param, "")
        initial[field] = value[:10] if field.startswith("deadline") else value
    for field, param in [("status", "status_id"), ("method", "purchase_method_id")]:
        value = spec.get("filters", {}).get(param + "__in", spec.get("filters", {}).get(param, ""))
        initial[field] = str(value).split("__") if value else []
    if "status_selection" in spec:
        initial["status"] = spec["status_selection"]
    initial["_defaults_version"] = spec.get("defaults_version", 0)
    if "regions" in spec:
        initial["region"] = spec["regions"]
        initial["city"] = spec.get("cities", [])
    for number_kind in ["lot_number", "announcement_number"]:
        if number_kind in spec.get("filters", {}):
            initial.update(number=spec["filters"][number_kind], number_kind=number_kind)
    active_profile = SearchProfile.objects.filter(pk=spec.get("profile_id"), owner=request.user).first() if spec.get("profile_id") else None
    profile_saved = False
    if active_profile:
        current_spec = {key: value for key, value in spec.items() if key != "profile_id"}
        ignored = {'depth', 'pages', 'auto_pages', 'backend'}
        profile_saved = {k:v for k,v in current_spec.items() if k not in ignored} == {k:v for k,v in active_profile.filters.get('specification', {}).items() if k not in ignored}
    return render(request, template, {"active_profile": active_profile, "profile_saved": profile_saved, "job": job, "local_coverage": job.source_counts.get("__index__"), "page": page, "pending": job.status in {"queued", "running"},
        "remaining_days": remaining_days, "form": SearchForm(initial=initial), "profiles": SearchProfile.objects.filter(owner=request.user).order_by("-updated_at")})


def queue_refresh(lot):
    now = timezone.now()
    if lot.refresh_started_at and lot.refresh_started_at > now-timedelta(seconds=30):
        return
    updated = Lot.objects.filter(pk=lot.pk).exclude(refresh_state="running").update(
        refresh_state="running", refresh_started_at=now, refresh_error="")
    if updated:
        try:
            tasks.refresh_lot.delay(lot.pk)
        except Exception:
            Lot.objects.filter(pk=lot.pk).update(refresh_state="error", refresh_error="Очередь обновления недоступна.")


@login_required
def lot_detail(request, lot_id):
    lot = get_object_or_404(Lot.objects.select_related("announcement", "customer"), pk=lot_id)
    back_key = f"lot_return_{lot_id}"
    back_url = reverse('search')
    for candidate in (request.GET.get('next'), request.META.get('HTTP_REFERER'), request.session.get(back_key)):
        if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            parts = urlsplit(candidate)
            if not parts.path.startswith('/') or parts.path.startswith('//') or parts.path == request.path:
                continue
            back_url = parts.path + ('?' + parts.query if parts.query else '')
            request.session[back_key] = back_url
            break
    if lot.refresh_state == "running" and lot.refresh_started_at and lot.refresh_started_at < timezone.now()-timedelta(minutes=6):
        Lot.objects.filter(pk=lot.pk).update(refresh_state="error", refresh_error="Обновление не завершилось вовремя.")
        lot.refresh_from_db()
    if not lot.refresh_state or (lot.refresh_state == "done" and lot.checked_at and lot.checked_at < timezone.now()-timedelta(minutes=5)):
        queue_refresh(lot)
        lot.refresh_from_db()
    result_context(request, [lot])
    batch = LotDocumentDownload.objects.filter(lot=lot).first() if lot.is_favorite else None
    if batch and batch.state in {'queued', 'running'} and batch.updated_at < timezone.now()-timedelta(minutes=25):
        batch.state, batch.error = 'error', 'Загрузка не завершилась вовремя. Повторите попытку.'
        batch.save(update_fields=['state','error','updated_at'])
    documents = []
    if lot.is_favorite:
        for doc in Document.objects.filter(announcement=lot.announcement).filter(Q(lot=lot)|Q(lot__isnull=True)).prefetch_related('versions').order_by('name'):
            doc.saved_version = max(doc.versions.all(),key=lambda version:version.pk,default=None)
            documents.append(doc)
    return render(request, "core/_lot_detail.html" if request.headers.get("HX-Request") else "core/lot_detail.html", {"lot": lot, "back_url": back_url, "documents": documents, "document_batch": batch, "documents_pending": bool(batch and batch.state in {"queued", "running"})})


@login_required
@require_POST
def lot_refresh(request, lot_id):
    lot = get_object_or_404(Lot, pk=lot_id)
    queue_refresh(lot)
    return redirect("lot_detail", lot_id=lot.pk)


@login_required
@require_POST
def favorite(request, lot_id):
    lot = get_object_or_404(Lot, pk=lot_id)
    if request.POST.get("action") == "remove":
        Favorite.objects.filter(owner=request.user, lot=lot).delete()
    elif request.POST.get("action") == "add":
        Favorite.objects.get_or_create(owner=request.user, lot=lot)
    if request.headers.get("HX-Request"):
        result_context(request, [lot])
        return render(request, "core/_lot_actions.html", {"lot": lot, "update_favorite_count": True, "return_to": request.POST.get("next", "")})
    next_url = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(next_url)
    return redirect("lot_detail", lot_id=lot.pk)


@login_required
def favorites(request):
    ordering = {'amount': 'amount', 'added': 'favorite__created_at', 'status': 'status'}
    selected = request.GET.get('sort', '-added')
    key = selected.lstrip('-')
    if key not in ordering:
        selected, key = '-added', 'added'
    descending = selected.startswith('-')
    field = ordering[key]
    order = F(field).desc(nulls_last=True) if descending else F(field).asc(nulls_last=True)
    lots = Lot.objects.filter(favorite__owner=request.user).select_related('announcement', 'customer').annotate(
        favorite_added=F('favorite__created_at')).order_by(order, '-pk')
    page = Paginator(lots, 25).get_page(request.GET.get('page'))
    result_context(request, list(page))
    headers = []
    for label, sort_key in [('Лот и заказчик', ''), ('Сумма, ₸', 'amount'), ('Даты заявки', ''), ('Статус', 'status'), ('Дата добавления', 'added')]:
        active = key == sort_key
        headers.append({'label': label, 'sort': ('-' if active and not descending else '') + sort_key,
                        'sortable': bool(sort_key), 'arrow': ('↓' if descending else '↑') if active else '↕',
                        'aria': ('descending' if descending else 'ascending') if active else 'none'})
    return render(request, 'core/favorites.html', {'page': page, 'favorite_sort': selected, 'favorite_headers': headers})



def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)


@login_required
def notification_settings(request):
    from django.conf import settings
    from .telegram_service import connection_link
    sub = TelegramSubscription.objects.filter(owner=request.user).first()
    link = None
    if request.method == "POST":
        if request.POST.get("action") == "connect" and settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME:
            link = connection_link(request.user)
            sub = TelegramSubscription.objects.get(owner=request.user)
        elif request.POST.get("action") == "disconnect":
            TelegramSubscription.objects.filter(owner=request.user).update(enabled=False,chat_id=None,link_hash="",link_expires_at=None)
            SearchProfile.objects.filter(owner=request.user).update(enabled=False)
            StatusDelivery.objects.filter(watch__owner=request.user,state="pending").update(state="cancelled")
            AnnouncementWatch.objects.filter(owner=request.user).update(enabled=False,lease="",lease_until=None)
            TelegramDelivery.objects.filter(owner=request.user,state="pending").update(state="cancelled")
            return redirect("notification_settings")
    context = {"subscription":sub,"telegram_ready":bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME),"telegram_bot_username":settings.TELEGRAM_BOT_USERNAME,"connect_link":link}
    template = "core/_telegram_status.html" if request.headers.get("HX-Request") else "core/settings.html"
    return render(request,template,context)


@login_required
@require_POST
def profile_notifications(request, profile_id):
    from django.contrib import messages
    profile = get_object_or_404(SearchProfile,pk=profile_id,owner=request.user)
    error = ""
    from decimal import Decimal, InvalidOperation, ROUND_CEILING
    try:
        hours = Decimal(request.POST.get("hours", "1").replace(",", "."))
        if not hours.is_finite() or not Decimal("1") <= hours <= 12 or hours != hours.to_integral_value(): raise ValueError()
        minutes = max(1, int((hours*60).to_integral_value(rounding=ROUND_CEILING)))
    except (InvalidOperation, ValueError):
        minutes = max(1,profile.interval_minutes)
        hours = Decimal(minutes)/60
        error = "Недопустимое значение интервала."
    if not error:
        if request.POST.get("action") == "toggle":
            connected = TelegramSubscription.objects.filter(owner=request.user,enabled=True,chat_id__isnull=False).exists()
            if not profile.enabled and not connected:
                error = "Сначала подключите Telegram в настройках."
            else:
                profile.enabled = not profile.enabled
                profile.next_monitor_at = None
        if profile.interval_minutes != minutes:
            profile.next_monitor_at = timezone.now()+timedelta(minutes=minutes)
        profile.interval_minutes = minutes
        profile.save(update_fields=["enabled","interval_minutes","next_monitor_at","updated_at"])
    if request.headers.get("HX-Request"):
        return render(request,"core/_profile_notifications.html",{"profile":profile,"notification_error":error})
    if error: messages.error(request,error)
    return redirect("search")


@login_required
@require_POST
def status_watch(request,lot_id):
    import secrets
    from .status_monitor import cancel_watch
    lot=get_object_or_404(Lot.objects.select_related("announcement"),pk=lot_id)
    error=""
    if request.POST.get("action")=="off":
        cancel_watch(request.user.pk,lot.announcement_id)
    elif request.POST.get("action")=="on":
        if not TelegramSubscription.objects.filter(owner=request.user,enabled=True,chat_id__isnull=False).exists():
            error="Подключите Telegram в настройках для слежения за статусом."
        else:
            with transaction.atomic():
                watch,_=AnnouncementWatch.objects.select_for_update().get_or_create(owner=request.user,announcement=lot.announcement,defaults={"lot":lot})
                Favorite.objects.get_or_create(owner=request.user,lot=lot)
                if not watch.enabled:
                    watch.enabled=True;watch.lot=lot;watch.generation=secrets.token_hex(16)
                    watch.last_status="";watch.last_status_name="";watch.revision=0
                    watch.next_check_at=None;watch.lease="";watch.lease_until=None;watch.error=""
                    watch.save()
    result_context(request,[lot])
    if request.headers.get("HX-Request"):
        return render(request,"core/_lot_actions.html",{"lot":lot,"update_favorite_count":True,"watch_error":error,"return_to":request.POST.get("next","")})
    if error:return redirect("notification_settings")
    next_url=request.POST.get("next","")
    if url_has_allowed_host_and_scheme(next_url,allowed_hosts={request.get_host()},require_https=request.is_secure()):return redirect(next_url)
    return redirect("lot_detail",lot_id=lot.pk)


@login_required
@require_POST
def document_retry(request, lot_id):
    lot = get_object_or_404(Lot, pk=lot_id, favorite__owner=request.user)
    from .document_service import queue_documents
    queue_documents(lot.pk)
    return redirect('lot_detail',lot_id=lot.pk)


@login_required
def document_download(request, lot_id, version_id):
    from .document_service import stored_path
    lot = get_object_or_404(Lot, pk=lot_id, favorite__owner=request.user)
    version = get_object_or_404(DocumentVersion.objects.select_related('document'),pk=version_id,document__announcement_id=lot.announcement_id)
    if version.document.lot_id not in {None,lot.pk}:
        raise Http404
    try:
        stream = stored_path(version.storage_path).open('rb')
    except (OSError, ValueError):
        raise Http404
    response = FileResponse(stream,as_attachment=True,filename=version.document.name,content_type='application/octet-stream')
    response['Cache-Control'] = 'private, no-store'
    return response
