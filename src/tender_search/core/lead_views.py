import csv
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .lead_service import set_collection_enabled
from .models import LeadCollectionState, LeadCompany, LeadEmail, LeadProcurement


def require_staff(request):
    if not request.user.is_staff:
        raise PermissionDenied


def collection_context():
    state, _ = LeadCollectionState.objects.get_or_create(pk=1)
    companies = LeadCompany.objects.aggregate(total=Count('pk', distinct=True),
        with_email=Count('pk', filter=Q(emails__current=True), distinct=True))
    sources = {row['state']: row['count'] for row in LeadProcurement.objects.values('state').annotate(count=Count('pk'))}
    return {'collection': state, 'lead_totals': companies, 'source_totals': sources,
            'source_count': sum(sources.values()), 'windows_left': len(state.windows),
            'collector_recent': bool(state.heartbeat_at and state.heartbeat_at >= timezone.now()-timedelta(minutes=3))}


def filtered_companies(request):
    companies = LeadCompany.objects.annotate(participation_count=Count('participations', distinct=True))
    query = request.GET.get('q', '').strip()[:200]
    if query:
        companies = companies.filter(Q(name__icontains=query) | Q(identifier__icontains=query) |
                                     Q(emails__email__icontains=query)).distinct()
    status = request.GET.get('status', '')
    if status in dict(LeadCompany.STATUS_CHOICES):
        companies = companies.filter(status=status)
    contact = request.GET.get('contact', '')
    if contact in {'pending', 'ready', 'missing', 'error'}:
        companies = companies.filter(contact_state=contact)
    return companies.order_by('-created_at', '-pk'), query


@login_required
def leads(request):
    require_staff(request)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action in {'start', 'pause'}:
            set_collection_enabled(action == 'start')
            messages.success(request, 'Фоновый сбор включён.' if action == 'start' else 'Фоновый сбор приостановлен.')
        elif action == 'retry':
            identifier = request.POST.get('procurement_id', '')
            if identifier.isdigit():
                LeadProcurement.objects.filter(pk=identifier, state__in=['retry', 'review']).update(
                    state='queued', next_attempt_at=timezone.now(), error='', updated_at=timezone.now())
                messages.success(request, 'Протокол поставлен в очередь повторной проверки.')
        return redirect('administration_leads')
    companies, query = filtered_companies(request)
    companies = companies.prefetch_related(Prefetch('emails', queryset=LeadEmail.objects.filter(current=True).order_by(
        '-verified_at', 'email'), to_attr='active_emails'))
    page = Paginator(companies, 25).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    context = collection_context()
    context.update(page=page, query=query, status_choices=LeadCompany.STATUS_CHOICES,
        contact_choices=LeadCompany._meta.get_field('contact_state').choices, page_query=params.urlencode(),
        issues=LeadProcurement.objects.filter(state__in=['retry', 'review']).order_by('-updated_at')[:10])
    return render(request, 'core/leads.html', context)


@login_required
def collection_status(request):
    require_staff(request)
    return render(request, 'core/_lead_collection_status.html', collection_context())


class LeadEditForm(forms.ModelForm):
    notes = forms.CharField(label='Заметки', required=False, max_length=4000, widget=forms.Textarea(attrs={'rows': 4}))

    class Meta:
        model = LeadCompany
        fields = ['status', 'notes']
        labels = {'status': 'Состояние клиента', 'notes': 'Заметки'}


@login_required
def lead_detail(request, company_id):
    require_staff(request)
    company = get_object_or_404(LeadCompany, pk=company_id)
    form = LeadEditForm(request.POST or None, instance=company)
    if request.method == 'POST':
        if request.POST.get('action') == 'refresh':
            if company.status == 'excluded':
                messages.info(request, 'Для перепроверки контактов сначала измените состояние «Не связываться».')
                return redirect('administration_lead_detail', company_id=company.pk)
            LeadCompany.objects.filter(pk=company.pk).update(next_contact_check_at=timezone.now(),
                contact_state='pending', contact_error='', updated_at=timezone.now())
            messages.success(request, 'Контакты будут перепроверены фоновым сборщиком.')
            return redirect('administration_lead_detail', company_id=company.pk)
        if form.is_valid():
            form.save()
            messages.success(request, 'Карточка клиента сохранена.')
            return redirect('administration_lead_detail', company_id=company.pk)
    page = Paginator(company.participations.select_related('procurement').order_by(
        '-procurement__results_published_at'), 25).get_page(request.GET.get('page'))
    return render(request, 'core/lead_detail.html', {'company': company, 'form': form,
        'emails': company.emails.order_by('-current', '-verified_at'), 'page': page})


def csv_cell(value):
    value = str(value or '')
    return "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) else value


class Echo:
    def write(self, value):
        return value


@login_required
def export_leads(request):
    require_staff(request)
    companies, _ = filtered_companies(request)
    companies = companies.exclude(status='excluded').filter(emails__current=True).distinct().prefetch_related(
        Prefetch('emails', queryset=LeadEmail.objects.filter(current=True).order_by('email'), to_attr='active_emails'))
    writer = csv.writer(Echo(), delimiter=';')

    def rows():
        yield '\ufeff'+writer.writerow(['Компания', 'БИН/ИИН', 'E-mail', 'Состояние', 'Источник', 'Проверено'])
        for company in companies.iterator(chunk_size=200):
            for contact in company.active_emails:
                yield writer.writerow([csv_cell(company.name), "'"+company.identifier, csv_cell(contact.email),
                    company.get_status_display(), contact.source_url, contact.verified_at.isoformat()])
    response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="tendersearch-clients.csv"'
    return response
