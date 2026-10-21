from __future__ import annotations

import time
from typing import Generic, TypeVar

T = TypeVar("T")


class TtlCache(Generic[T]):
    """Small in-memory cache so a repeated trip does not call outside services again."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._items: dict[object, tuple[float, T]] = {}

    def get(self, key: object) -> T | None:
        found = self._items.get(key)
        if found is None:
            return None
        expires, value = found
        if expires < time.monotonic():
            self._items.pop(key, None)
            return None
        return value

    def set(self, key: object, value: T) -> T:
        self._items[key] = (time.monotonic() + self.seconds, value)
        return value

    def clear(self) -> None:
        self._items.clear()
