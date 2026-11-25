"""Lightweight in-process async Server-Sent Events (SSE) broker for real-time flood updates."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Dict, Optional, Set, Tuple


def format_sse(event: str, data: Dict[str, Any], retry_ms: Optional[int] = None) -> str:
    """Serialize a Server-Sent Events frame with optional reconnect delay."""
    lines = []
    if retry_ms is not None and retry_ms > 0:
        lines.append(f"retry: {int(retry_ms)}")
    if event:
        lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"


class RealtimeEventBus:
    """Thread-safe async pub/sub queue fan-out for low-latency SSE streaming."""

    def __init__(self, max_queue_size: int = 32) -> None:
        self._lock = threading.Lock()
        self._subscribers: Set[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._max_queue_size = max_queue_size
        self._event_seq = 0
        self._last_event_ts = 0.0

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue:
        """Register an active request queue bound to the current running event loop."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        with self._lock:
            self._subscribers.add((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue cleanly when the HTTP stream closes."""
        with self._lock:
            self._subscribers = {pair for pair in self._subscribers if pair[1] is not q}

    @staticmethod
    def _push_nowait(q: asyncio.Queue, item: Dict[str, Any]) -> None:
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass

    def publish(self, event_type: str, data: Dict[str, Any]) -> int:
        """Broadcast an event immediately to all connected SSE subscribers."""
        with self._lock:
            self._event_seq += 1
            seq = self._event_seq
            self._last_event_ts = time.time()
            targets = list(self._subscribers)

        envelope = {
            "event": event_type,
            "data": {**data, "seq": seq},
        }
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        delivered = 0
        for loop, q in targets:
            if loop.is_closed():
                continue
            try:
                if current_loop is loop:
                    self._push_nowait(q, envelope)
                else:
                    loop.call_soon_threadsafe(self._push_nowait, q, envelope)
                delivered += 1
            except RuntimeError:
                pass
        return delivered


realtime_bus = RealtimeEventBus()
