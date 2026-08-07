# SPDX-License-Identifier: MIT
"""In-memory TTL cache with single-flight coalescing."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class _CacheEntry[T]:
    value: T
    expires_at: float


class AsyncTTLCache:
    """Simple async TTL cache with per-key single-flight behavior."""

    def __init__(self) -> None:
        self._store: dict[Hashable, _CacheEntry[object]] = {}
        self._inflight: dict[Hashable, asyncio.Future[object]] = {}
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._store.clear()

    async def get_or_set(
        self,
        key: Hashable,
        factory: Callable[[], Awaitable[T]],
        *,
        ttl_s: float,
    ) -> T:
        if ttl_s <= 0:
            return await factory()

        now = time.monotonic()
        entry = self._store.get(key)
        if entry is not None and entry.expires_at > now:
            logger.debug("cache_hit key=%s", key)
            return entry.value  # type: ignore[return-value]

        async with self._lock:
            entry = self._store.get(key)
            now = time.monotonic()
            if entry is not None and entry.expires_at > now:
                logger.debug("cache_hit key=%s", key)
                return entry.value  # type: ignore[return-value]

            inflight = self._inflight.get(key)
            if inflight is None:
                loop = asyncio.get_running_loop()
                inflight = loop.create_future()
                self._inflight[key] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            logger.debug("cache_coalesce key=%s", key)
            return await inflight  # type: ignore[return-value]

        logger.debug("cache_miss key=%s", key)
        try:
            value = await factory()
            self._store[key] = _CacheEntry(value=value, expires_at=time.monotonic() + ttl_s)
            inflight.set_result(value)
            return value
        except Exception as exc:
            inflight.set_exception(exc)
            raise
        finally:
            async with self._lock:
                self._inflight.pop(key, None)
