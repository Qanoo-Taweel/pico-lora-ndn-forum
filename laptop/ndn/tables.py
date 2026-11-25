"""The three classic NDN node tables.

  * ContentStore (CS): a cache of Data packets. Any Interest that matches the
    cache is answered from here, without asking the application again.
  * PendingInterestTable (PIT): Interests we sent and are still waiting for.
  * NonceLog: recently seen Interest nonces, to drop duplicates/loops.

Each class has exactly one job (Single Responsibility).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque

from .packets import Data


class ContentStore:
    def __init__(self, capacity: int = 2000):
        self._items: OrderedDict[str, tuple[Data, float | None]] = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    def put(self, data: Data, ttl: float | None = None) -> None:
        """ttl=None means the data never changes (immutable, cache forever)."""
        expires = time.monotonic() + ttl if ttl is not None else None
        with self._lock:
            self._items[data.name] = (data, expires)
            self._items.move_to_end(data.name)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)

    def get(self, name: str) -> Data | None:
        with self._lock:
            entry = self._items.get(name)
            if not entry:
                return None
            data, expires = entry
            if expires is not None and time.monotonic() > expires:
                del self._items[name]
                return None
            self._items.move_to_end(name)
            return data

    def __len__(self) -> int:
        return len(self._items)


class PendingEntry:
    def __init__(self):
        self.event = threading.Event()
        self.data: Data | None = None


class PendingInterestTable:
    def __init__(self):
        self._entries: dict[str, PendingEntry] = {}
        self._lock = threading.Lock()

    def expect(self, name: str) -> PendingEntry:
        with self._lock:
            return self._entries.setdefault(name, PendingEntry())

    def satisfy(self, data: Data) -> bool:
        with self._lock:
            entry = self._entries.pop(data.name, None)
        if entry is None:
            return False
        entry.data = data
        entry.event.set()
        return True

    def forget(self, name: str) -> None:
        with self._lock:
            self._entries.pop(name, None)

    def __len__(self) -> int:
        return len(self._entries)


class NonceLog:
    def __init__(self, size: int = 256):
        self._seen: deque[bytes] = deque(maxlen=size)
        self._lock = threading.Lock()

    def seen_before(self, nonce: bytes) -> bool:
        with self._lock:
            if nonce in self._seen:
                return True
            self._seen.append(nonce)
            return False
