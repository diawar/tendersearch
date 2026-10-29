from unittest.mock import Mock

import pytest
from redis.exceptions import ConnectionError

from tender_search.client import SourceError
from tender_search.core.source import RedisGate


def test_global_gate_honors_reserved_slot():
    backend, sleep = Mock(), Mock()
    backend.eval.return_value = 1500
    RedisGate(redis=backend, sleep=sleep).acquire()
    sleep.assert_called_once_with(1.5)


def test_global_gate_does_not_bypass_redis_failure():
    backend = Mock()
    backend.eval.side_effect = ConnectionError("offline")
    with pytest.raises(SourceError):
        RedisGate(redis=backend).acquire()
