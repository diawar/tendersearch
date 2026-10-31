"""User-facing platform health; each platform appears only once."""
from datetime import timedelta
from django.utils import timezone
from .index_service import date_value
from .models import MedicalIndex

# Verified against the source's systems dictionary. FMS (7) uses its direct
# connector's health below and is not repeated in this list.
PORTAL_PLATFORMS = (('egz', 'Госзакуп (ЭГЗ)'), ('mitwork', 'Mitwork'),
                    ('skk', 'Самрук-Казына'), ('epz', 'EPZ'), ('sk_farm', 'SK Farm'))
MEDICAL_PLATFORMS = (('med_ecc', 'med.ecc.kz'), ('fms_ecc', 'fms.ecc.kz'))


def health(*, completed_at, heartbeat_at, building, error=False, paused=False, ready=True, now):
    completed = date_value(completed_at)
    heartbeat = date_value(heartbeat_at)
    activity = heartbeat or completed
    if error:
        state, label = 'error', 'Ошибка обновления'
    elif paused:
        state, label = 'paused', 'Приостановлена'
    elif building:
        if activity and activity >= now-timedelta(minutes=5):
            state, label = 'loading', 'Обновляется'
        else:
            state, label = 'stale', 'Требует обновления'
    elif not completed:
        state, label = 'waiting', 'Ожидает загрузки'
    elif ready and completed >= now-timedelta(hours=6) and activity >= now-timedelta(minutes=20):
        state, label = 'ready', 'Обновлена'
    else:
        state, label = 'stale', 'Требует обновления'
    return {'state': state, 'label': label, 'completed_at': completed}


def platform_statuses(coverage):
    now = timezone.now()
    shared = health(completed_at=coverage.get('completed_at'), heartbeat_at=coverage.get('heartbeat_at'),
                    building=coverage.get('building'), error=coverage.get('error'),
                    paused=coverage.get('paused'), ready=coverage.get('ready'), now=now)
    result = [dict(shared, key=key, name=name) for key, name in PORTAL_PLATFORMS]
    medical = {s.source: s for s in MedicalIndex.objects.filter(source__in=dict(MEDICAL_PLATFORMS))}
    for key, name in MEDICAL_PLATFORMS:
        source = medical.get(key)
        status = health(completed_at=source.completed_at if source else None,
                        heartbeat_at=source.heartbeat_at if source else None,
                        building=bool(source and source.cursor), error=bool(source and source.error), now=now)
        result.append(dict(status, key=key, name=name))
    return result
