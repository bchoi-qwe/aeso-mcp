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
    """Simple async TTL cache with per-key single-flight behavior.

    Bounded by ``max_entries`` (FIFO of soonest-expiring keys after dropping
    already-expired entries) so long-lived servers cannot grow without limit.
    """

    def __init__(self, *, max_entries: int = 512) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._store: dict[Hashable, _CacheEntry[object]] = {}
        self._inflight: dict[Hashable, asyncio.Future[object]] = {}
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def _evict_if_needed(self, now: float) -> None:
        expired = [k for k, e in self._store.items() if e.expires_at <= now]
        for key in expired:
            self._store.pop(key, None)
        while len(self._store) >= self._max_entries:
            oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
            self._store.pop(oldest_key, None)
            logger.debug("cache_evict key=%s size=%s", oldest_key, len(self._store))

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
            async with self._lock:
                now = time.monotonic()
                self._evict_if_needed(now)
                self._store[key] = _CacheEntry(value=value, expires_at=now + ttl_s)
                if not inflight.done():
                    inflight.set_result(value)
            return value
        except asyncio.CancelledError:
            if not inflight.done():
                inflight.cancel()
            raise
        except Exception as exc:
            if not inflight.done():
                inflight.set_exception(exc)
            raise
        finally:
            async with self._lock:
                self._inflight.pop(key, None)
