"""Explicit lot-level evidence read by the extension; never infer from KTRU."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from .models import CompanySettings, Lot, UserLotWorkflow


def save_restriction_report(owner, lot_id, report):
    observed = parse_datetime(str(report.get('observed_at', '')))
    if (report.get('schema') != 'goszakup-restriction-v1' or
            type(report.get('national_withdrawn')) is not bool or not observed or
            timezone.is_naive(observed) or observed > timezone.now() + timedelta(minutes=2) or
            observed < timezone.now() - timedelta(days=1)):
        raise ValueError('Некорректное подтверждение изъятия.')
    with transaction.atomic():
        lot = Lot.objects.select_for_update().select_related('announcement').get(pk=lot_id)
        company = CompanySettings.objects.filter(owner=owner).first()
        sources = {
            'lot_api': f'https://zakup.gov.kz/api/v1/announcements/{lot.announcement.external_id}/lots',
            'lot_card': f'https://zakup.gov.kz/cabinet/announcement/{lot.announcement.external_id}',
        }
        expected = sources.get(report.get('source_kind'))
        if (not company or company.company_bin != report.get('company_bin') or
                not UserLotWorkflow.objects.filter(owner=owner, lot=lot).exists() or
                lot.announcement.source != 'zakup' or
                report.get('announcement_number') != lot.announcement.number or
                report.get('lot_number') != lot.number or not expected or report.get('source_url', '').rstrip('/') != expected):
            raise ValueError('Отчёт не соответствует лоту и организации.')
        if lot.restriction_checked_at and observed <= lot.restriction_checked_at:
            raise ValueError('Отчёт устарел.')
        lot.national_withdrawn = report['national_withdrawn']
        lot.restriction_checked_at = observed
        lot.restriction_source_url = expected
        lot.save(update_fields=['national_withdrawn', 'restriction_checked_at', 'restriction_source_url'])
        return lot
