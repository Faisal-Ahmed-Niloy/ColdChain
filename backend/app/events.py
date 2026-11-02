"""Tiny in-process event bus. Ingest and the AI engine publish; the WebSocket
layer subscribes and pushes to browsers. Keeps the core free of web-framework code."""
from __future__ import annotations

import threading
from typing import Callable

_subs: list[Callable[[dict], None]] = []
_lock = threading.Lock()


def subscribe(fn: Callable[[dict], None]) -> None:
    with _lock:
        _subs.append(fn)


def publish(event: dict) -> None:
    with _lock:
        subs = list(_subs)
    for fn in subs:
        try:
            fn(event)
        except Exception:  # a broken subscriber must never break ingest
            pass
