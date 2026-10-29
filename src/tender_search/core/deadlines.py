"""Consistent remaining-time labels for the website and Telegram."""
from math import ceil
from django.utils import timezone


def remaining_time(deadline, now=None):
    if deadline is None:
        return '', False
    seconds = (deadline - (now or timezone.now())).total_seconds()
    if seconds <= 0:
        return 'Приём завершён', False
    if seconds < 86400:
        return 'Осталось меньше дня', True
    days = ceil(seconds / 86400)
    noun = 'дней' if 11 <= days % 100 <= 14 else ('день' if days % 10 == 1 else 'дня' if days % 10 in (2, 3, 4) else 'дней')
    return f'Осталось: {days} {noun}', seconds <= 86400


def telegram_remaining_time(deadline):
    text, urgent = remaining_time(deadline)
    if not text:
        return ''
    return f'🔴 <b>{text}</b>\n' if urgent else f'⏱ {text}\n'
