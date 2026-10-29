"""Shared request pacing for Django processes using the procurement adapter."""
import time

from django.conf import settings
from redis import Redis, RedisError

from tender_search.client import SourceError, ZakupClient


class RedisGate:
    # Reserve slots using the Redis server clock so workers do not need synchronized clocks.
    SCRIPT = """
    local t = redis.call('TIME')
    local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
    local previous = tonumber(redis.call('GET', KEYS[1]) or '0')
    local slot = math.max(now, previous)
    local delay = slot - now
    if delay > 30000 then return -1 end
    redis.call('SET', KEYS[1], slot + 1000, 'PX', delay + 60000)
    return delay
    """

    def __init__(self, redis=None, sleep=time.sleep):
        self.redis = redis or Redis.from_url(settings.REDIS_URL, socket_timeout=5, socket_connect_timeout=5)
        self.sleep = sleep

    def acquire(self):
        try:
            delay = self.redis.eval(self.SCRIPT, 1, "source:zakup:next-slot")
        except RedisError as exc:
            raise SourceError("Общий ограничитель запросов недоступен") from exc
        if delay < 0:
            raise SourceError("Очередь запросов к источнику перегружена")
        if delay:
            self.sleep(delay / 1000)


def source_client():
    return ZakupClient(gate=RedisGate())
