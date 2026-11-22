"""Process-memory cache (nothing is written to disk). Bounded LRU with per-entry expiry and one computation per key."""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class TTLCache:
    def __init__(self, maxsize: int = 512):
        self.maxsize = maxsize
        self._d: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def peek(self, key: str):
        """Value even if expired (caller decides whether to show it as STALE), else None."""
        with self._lock:
            hit = self._d.get(key)
            return hit[1] if hit else None

    def get(self, key: str):
        with self._lock:
            hit = self._d.get(key)
            if hit and hit[0] > time.time():
                self._d.move_to_end(key)
                return hit[1]
            return None

    def put(self, key: str, value, ttl_s: float) -> None:
        with self._lock:
            self._d[key] = (time.time() + ttl_s, value)
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def get_or_compute(self, key: str, fn: Callable[[], tuple[Any, float]]):
        """fn returns (value, ttl_s). Concurrent callers for the same key wait for one computation."""
        v = self.get(key)
        if v is not None:
            return v
        with self._lock:
            kl = self._key_locks.setdefault(key, threading.Lock())
        with kl:
            v = self.get(key)
            if v is not None:
                return v
            value, ttl = fn()
            self.put(key, value, ttl)
            return value

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
