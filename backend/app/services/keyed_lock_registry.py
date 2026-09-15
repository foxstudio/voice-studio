from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generic, Hashable, Iterator, TypeVar


KeyT = TypeVar("KeyT", bound=Hashable)


@dataclass
class _LockEntry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


class KeyedLockRegistry(Generic[KeyT]):
    """Serialize work per key without retaining idle keys."""

    def __init__(self) -> None:
        self._entries: dict[KeyT, _LockEntry] = {}
        self._guard = threading.Lock()

    @contextmanager
    def hold(self, key: KeyT) -> Iterator[None]:
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = _LockEntry()
                self._entries[key] = entry
            entry.users += 1

        acquired = False
        try:
            entry.lock.acquire()
            acquired = True
            yield
        finally:
            try:
                if acquired:
                    entry.lock.release()
            finally:
                with self._guard:
                    entry.users -= 1
                    if (
                        entry.users == 0
                        and self._entries.get(key) is entry
                    ):
                        self._entries.pop(key, None)

    @property
    def active_key_count(self) -> int:
        with self._guard:
            return len(self._entries)
