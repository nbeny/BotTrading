"""`Cache.lock(blocking=False)` doit lever plutot que de laisser passer un
second appelant -- le correctif de a375390 (`LockNotAcquiredError`) n'avait
pas de test avant ce fichier.

Utilise `fakeredis` plutot qu'un vrai Redis : ce paquet ne supporte pas
`EVAL`/`EVALSHA` (pas de Lua embarque dans cet environnement), et
`Lock.release()` en depend -- seul `Lock.release` est donc neutralise ici, ce
qui laisse `Lock.acquire()` (un simple `SET NX PX`, sans Lua) s'executer
reellement contre le faux serveur, qui est ce que ce test verrouille.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import fakeredis
import pytest
from redis.asyncio.lock import Lock

from cmi_common.cache import Cache, LockNotAcquiredError


def _fake_cache() -> Cache:
    cache = Cache.__new__(Cache)
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return cache


async def test_non_blocking_acquire_raises_when_already_held() -> None:
    cache = _fake_cache()
    with patch.object(Lock, "release", AsyncMock(return_value=None)):
        async with cache.lock("threshold-scan", timeout=30.0, blocking=False):
            with pytest.raises(LockNotAcquiredError):
                async with cache.lock("threshold-scan", timeout=30.0, blocking=False):
                    pass  # pragma: no cover - must not be reached
    await cache.client.aclose()
