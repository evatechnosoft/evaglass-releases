"""Olay sözleşmesi v1 — tek gerçek kaynak: contracts/events.schema.json.

EventBus: süreç içi yayıncı. Aboneler asyncio.Queue alır; NDJSON store'a da yazar.
Orkestratör ve sidecar'lar yalnızca `emit()` çağırır; UI yalnızca SSE üzerinden okur.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

EVENT_TYPES = {
    "session.started", "session.finished", "session.failed",
    "task.received", "task.plan", "task.progress", "task.done",
    "perception.screenshot", "perception.zoom",
    "llm.thinking", "llm.decision",
    "action.requested", "action.executed", "action.failed",
    "approval.requested", "approval.resolved",
    "metrics.tick", "log",
}


def new_session_id() -> str:
    return "s_" + secrets.token_hex(3)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(slots=True)
class Event:
    session: str
    agent: str
    seq: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=now_iso)
    v: int = 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {"v": d["v"], "ts": d["ts"], "session": d["session"], "agent": d["agent"],
                "seq": d["seq"], "type": d["type"], "data": d["data"]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


class EventBus:
    """Thread-safe yayıncı. Senkron `emit()` her yerden çağrılabilir (sidecar thread'leri dahil)."""

    def __init__(self, store_dir: Path | None = None) -> None:
        self._subs: list[Callable[[Event], None]] = []
        self._lock = threading.Lock()
        self._seq: dict[str, int] = {}
        self._store_dir = store_dir
        self._files: dict[str, Any] = {}
        self.history: dict[str, list[Event]] = {}

    def subscribe(self, fn: Callable[[Event], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)
        return unsubscribe

    def emit(self, session: str, agent: str, type: str, data: dict[str, Any] | None = None) -> Event:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {type}")
        with self._lock:
            seq = self._seq.get(session, 0)
            self._seq[session] = seq + 1
            ev = Event(session=session, agent=agent, seq=seq, type=type, data=data or {})
            self.history.setdefault(session, []).append(ev)
            if self._store_dir is not None:
                f = self._files.get(session)
                if f is None:
                    self._store_dir.mkdir(parents=True, exist_ok=True)
                    f = (self._store_dir / f"{session}.ndjson").open("a", encoding="utf-8")
                    self._files[session] = f
                f.write(ev.to_json() + "\n")
                f.flush()
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(ev)
            except Exception:  # abone hatası yayıncıyı düşürmez
                pass
        return ev

    def close(self) -> None:
        with self._lock:
            for f in self._files.values():
                f.close()
            self._files.clear()


class AsyncQueueSubscriber:
    """Bir asyncio döngüsüne olayları güvenle taşır (SSE için)."""

    def __init__(self, bus: EventBus, loop: asyncio.AbstractEventLoop, session: str | None = None) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self._loop = loop
        self._session = session
        self._unsub = bus.subscribe(self._on)

    def _on(self, ev: Event) -> None:
        if self._session is None or ev.session == self._session:
            self._loop.call_soon_threadsafe(self.queue.put_nowait, ev)

    def close(self) -> None:
        self._unsub()


def read_ndjson(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
