"""AgentLab Gateway — UI ile orkestratör arasındaki tek sözleşme noktası.

Sorumluluklar (plan §3.1 "Harness/Gateway"):
  * Olay veriyolunu SSE olarak yayınla (`GET /events`), geç katılanlara geçmişi tekrar oynat.
  * Oturum kaydını listele (`GET /sessions`).
  * Komut yüzeyi (`POST /sessions/{id}/commands`): start / approve / reject / abort.
  * NDJSON replay (`POST /replay`) — canlı ajan olmadan da demo çalışır.
  * Statik varlıklar: `/` → ui/index.html, `/ui`, `/thumbs`, `/fixtures`.

UI asla orkestratöre doğrudan bağlanmaz; yazma yolu yalnızca komut API'sidir.
Sürücü/onay modülleri (agent A) henüz yoksa gateway replay modunda çalışmaya devam eder:
tüm importlar tembel ve try/except korumalıdır.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .events import (
    EVENT_TYPES,
    AsyncQueueSubscriber,
    Event,
    EventBus,
    new_session_id,
    now_iso,
    read_ndjson,
)

ROOT = Path(__file__).resolve().parents[2]

TASKS = ("git-push", "loop", "shell")
DRIVERS = ("scripted",)
HEARTBEAT_SECONDS = 15.0
REPLAY_MAX_GAP = 2.0  # orijinal ts farkı ne olursa olsun en fazla 2 sn bekle

TERMINAL_TYPES = {"session.finished", "session.failed"}


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def _ts_seconds(ts: Any) -> float | None:
    """ISO-8601 damgasını epoch saniyeye çevirir; ayrıştırılamazsa None."""
    if isinstance(ts, (int, float)):
        return float(ts)
    if not isinstance(ts, str) or not ts:
        return None
    txt = ts.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime

        return datetime.fromisoformat(txt).timestamp()
    except Exception:
        return None


def _sse(payload: str) -> bytes:
    return f"data: {payload}\n\n".encode("utf-8")



def _make_perception(bus: EventBus, session: str, agent: str, thumbs_dir: Any) -> Any:
    """DISPLAY varsa gerçek ekran algısı (mss) bağla; yoksa None (sürücü sahte hash üretir)."""
    if not os.environ.get("DISPLAY"):
        return None
    try:
        from .perception import ScreenPerceptionService  # type: ignore
        return ScreenPerceptionService(bus, session, agent, thumb_dir=thumbs_dir)
    except Exception:
        return None

def _filtered_kwargs(fn: Callable[..., Any], candidates: dict[str, Any]) -> dict[str, Any]:
    """Sürücünün imzasında gerçekten var olan kwargs'ları seçer.

    Agent A'nın `run_task` imzası değişse bile gateway kırılmasın diye savunmacı:
    **kwargs varsa hepsini geçiriyoruz, yoksa yalnızca bilinen adları.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return dict(candidates)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return dict(candidates)
    return {k: v for k, v in candidates.items() if k in sig.parameters}


class _NullBroker:
    """ApprovalBroker yokken (agent A modülü eksik) kullanılan yer tutucu."""

    def __init__(self, bus: EventBus, auto_approve: bool = False) -> None:
        self.bus = bus
        self.auto_approve = auto_approve
        self.available = False

    def resolve(self, approval_id: str, approved: bool, by: str = "ui") -> bool:  # pragma: no cover
        return False


def _make_broker(bus: EventBus, auto_approve: bool) -> Any:
    try:
        from .safety import ApprovalBroker  # type: ignore
    except Exception:
        return _NullBroker(bus, auto_approve)
    try:
        broker = ApprovalBroker(bus, auto_approve)
    except TypeError:  # imza farklıysa kwarg ile dene
        broker = ApprovalBroker(bus, auto_approve=auto_approve)  # type: ignore[call-arg]
    try:
        broker.available = True  # type: ignore[attr-defined]
    except Exception:
        pass
    return broker


# --------------------------------------------------------------------------- #
# Oturum özeti
# --------------------------------------------------------------------------- #
def _session_summary(session: str, events: list[Event]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    agents: list[str] = []
    task = None
    driver = None
    model = None
    progress = None
    ok = None
    pending: dict[str, Any] | None = None
    resolved: set[str] = set()
    requested: dict[str, dict[str, Any]] = {}

    for ev in events:
        counts[ev.type] = counts.get(ev.type, 0) + 1
        if ev.agent and ev.agent not in agents:
            agents.append(ev.agent)
        d = ev.data if isinstance(ev.data, dict) else {}
        if ev.type == "task.received":
            task = d.get("task", task)
        elif ev.type == "session.started":
            driver = d.get("driver", driver)
            task = d.get("task", task)
        elif ev.type in ("llm.thinking", "llm.decision"):
            model = d.get("model", model)
        elif ev.type == "task.progress":
            progress = {"done": d.get("done"), "total": d.get("total"), "label": d.get("label")}
        elif ev.type == "task.done":
            ok = bool(d.get("ok"))
        elif ev.type == "approval.requested":
            aid = str(d.get("id", ""))
            if aid:
                requested[aid] = d
        elif ev.type == "approval.resolved":
            aid = str(d.get("id", ""))
            if aid:
                resolved.add(aid)

    for aid, d in requested.items():
        if aid not in resolved:
            pending = {
                "id": aid,
                "risk": d.get("risk", "low"),
                "description": d.get("description", ""),
                "action": d.get("action"),
            }
            break

    last = events[-1] if events else None
    first = events[0] if events else None
    status = "idle"
    if last is not None:
        if last.type == "session.finished":
            status = "finished"
        elif last.type == "session.failed":
            status = "failed"
        elif pending is not None:
            status = "waiting-approval"
        else:
            status = "running"

    return {
        "session": session,
        "agent": agents[0] if agents else "?",
        "agents": agents,
        "count": len(events),
        "counts": counts,
        "last_type": last.type if last else None,
        "last_ts": last.ts if last else None,
        "started_ts": first.ts if first else None,
        "task": task,
        "driver": driver,
        "model": model,
        "progress": progress,
        "ok": ok,
        "status": status,
        "pending_approval": pending,
    }


# --------------------------------------------------------------------------- #
# Uygulama fabrikası
# --------------------------------------------------------------------------- #
def create_app(
    bus: EventBus,
    *,
    ui_dir: Path | str = ROOT / "ui",
    fixtures_dir: Path | str = ROOT / "fixtures",
    thumbs_dir: Path | str = ROOT / "thumbs",
    sandbox_dir: Path | str = ROOT / "sandbox",
    auto_approve: bool = False,
) -> FastAPI:
    ui_dir = Path(ui_dir).resolve()
    fixtures_dir = Path(fixtures_dir).resolve()
    thumbs_dir = Path(thumbs_dir).resolve()
    sandbox_dir = Path(sandbox_dir).resolve()
    for d in (ui_dir, fixtures_dir, thumbs_dir, sandbox_dir):
        d.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="AgentLab Gateway", version="1.0")
    app.state.bus = bus
    app.state.ui_dir = ui_dir
    app.state.fixtures_dir = fixtures_dir
    app.state.thumbs_dir = thumbs_dir
    app.state.sandbox_dir = sandbox_dir
    app.state.auto_approve = auto_approve
    app.state.approvals = _make_broker(bus, auto_approve)
    app.state.kill: dict[str, threading.Event] = {}
    app.state.threads: dict[str, threading.Thread] = {}

    # ---------------------------------------------------------------- health
    @app.get("/health")
    def health() -> dict[str, Any]:
        driver_ok = True
        try:
            __import__("agentlab.drivers.scripted")
        except Exception:
            driver_ok = False
        return {
            "ok": True,
            "ts": now_iso(),
            "sessions": len(bus.history),
            "driver_available": driver_ok,
            "approvals_available": bool(getattr(app.state.approvals, "available", False)),
            "auto_approve": app.state.auto_approve,
            "event_types": sorted(EVENT_TYPES),
        }

    # -------------------------------------------------------------- sessions
    @app.get("/sessions")
    def sessions() -> dict[str, Any]:
        items = [_session_summary(sid, list(evs)) for sid, evs in bus.history.items()]
        items.sort(key=lambda it: it.get("started_ts") or "", reverse=True)
        return {"sessions": items, "count": len(items)}

    @app.get("/sessions/{sid}/events")
    def session_events(sid: str) -> dict[str, Any]:
        evs = bus.history.get(sid)
        if evs is None:
            raise HTTPException(status_code=404, detail=f"unknown session: {sid}")
        return {"session": sid, "events": [e.to_dict() for e in evs]}

    @app.get("/replays")
    def replays() -> dict[str, Any]:
        names = sorted(p.name for p in fixtures_dir.glob("*.ndjson"))
        return {"fixtures": names}

    # ------------------------------------------------------------------ SSE
    @app.get("/events")
    async def events(request: Request, session: str = "all", limit: int = 0) -> StreamingResponse:
        want = None if session in ("", "all", "*") else session
        loop = asyncio.get_running_loop()
        sub = AsyncQueueSubscriber(bus, loop, want)

        # Geçmişi önce yolla: geç katılan UI durumu kaçırmasın.
        history: list[Event] = []
        if want is None:
            for evs in bus.history.values():
                history.extend(evs)
            history.sort(key=lambda e: (e.ts, e.seq))
        else:
            history.extend(bus.history.get(want, []))
        seen_seq: dict[str, int] = {}
        for ev in history:
            prev = seen_seq.get(ev.session, -1)
            if ev.seq > prev:
                seen_seq[ev.session] = ev.seq

        async def gen() -> Iterable[bytes]:
            sent = 0
            try:
                yield b": connected\n\n"
                for ev in history:
                    yield _sse(ev.to_json())
                    sent += 1
                    if limit and sent >= limit:
                        return
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        ev = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield f": ping {now_iso()}\n\n".encode("utf-8")
                        continue
                    if ev.seq <= seen_seq.get(ev.session, -1):
                        continue  # geçmişte zaten yollandı
                    seen_seq[ev.session] = ev.seq
                    yield _sse(ev.to_json())
                    sent += 1
                    if limit and sent >= limit:
                        return
            finally:
                sub.close()

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------- commands
    @app.post("/sessions/{sid}/commands")
    async def commands(sid: str, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        cmd = str(body.get("cmd") or body.get("command") or "").strip()

        if cmd == "start":
            return JSONResponse(_cmd_start(app, sid, body))
        if cmd in ("approve", "reject"):
            return JSONResponse(_cmd_approval(app, sid, body, approved=(cmd == "approve")))
        if cmd == "abort":
            return JSONResponse(_cmd_abort(app, sid, body))
        raise HTTPException(status_code=400, detail=f"unknown cmd: {cmd!r}")

    # --------------------------------------------------------------- replay
    @app.post("/replay")
    async def replay(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        name = str(body.get("fixture") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="fixture is required")
        path = (fixtures_dir / name).resolve()
        if fixtures_dir not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail=f"fixture not found: {name}")
        try:
            speed = float(body.get("speed", 1.0))
        except (TypeError, ValueError):
            speed = 1.0
        speed = min(max(speed, 0.05), 50.0)

        try:
            rows = [r for r in read_ndjson(path) if isinstance(r, dict)]
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"bad fixture: {exc}") from None
        if not rows:
            raise HTTPException(status_code=400, detail="fixture has no events")

        sid = new_session_id()
        agent = str(body.get("agent") or rows[0].get("agent") or "goat")
        kill = threading.Event()
        app.state.kill[sid] = kill
        t = threading.Thread(
            target=_replay_worker,
            args=(bus, sid, agent, rows, speed, kill),
            name=f"replay-{sid}",
            daemon=True,
        )
        app.state.threads[sid] = t
        t.start()
        return JSONResponse({"session": sid, "fixture": name, "count": len(rows), "speed": speed})

    # ------------------------------------------------------------- statikler
    app.mount("/ui", StaticFiles(directory=str(ui_dir)), name="ui")
    app.mount("/thumbs", StaticFiles(directory=str(thumbs_dir)), name="thumbs")
    app.mount("/fixtures", StaticFiles(directory=str(fixtures_dir)), name="fixtures")

    @app.get("/")
    def index() -> Any:
        page = ui_dir / "index.html"
        if not page.is_file():
            return PlainTextResponse("ui/index.html yok — gateway API modunda çalışıyor.", 200)
        return FileResponse(page, media_type="text/html")

    @app.get("/favicon.ico")
    def favicon() -> Any:
        return PlainTextResponse("", status_code=204)

    return app


# --------------------------------------------------------------------------- #
# Komut uygulayıcıları
# --------------------------------------------------------------------------- #
def _cmd_start(app: FastAPI, sid: str, body: dict[str, Any]) -> dict[str, Any]:
    bus: EventBus = app.state.bus
    task = str(body.get("task") or "git-push")
    driver = str(body.get("driver") or "scripted")
    agent = str(body.get("agent") or "goat")
    if driver not in DRIVERS:
        raise HTTPException(status_code=400, detail=f"unknown driver: {driver!r} (known: {list(DRIVERS)})")
    if task not in TASKS:
        raise HTTPException(status_code=400, detail=f"unknown task: {task!r} (known: {list(TASKS)})")
    try:
        pace = float(body.get("pace", 0.3))
    except (TypeError, ValueError):
        pace = 0.3
    pace = min(max(pace, 0.0), 5.0)

    try:
        from .drivers.scripted import run_task  # type: ignore
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"driver 'scripted' yüklenemedi ({exc}); /replay ile fixture oynatabilirsiniz",
        ) from None

    session = sid if sid and sid not in ("new", "auto", "-") else new_session_id()
    kill = threading.Event()
    app.state.kill[session] = kill

    perception = _make_perception(bus, session, agent, app.state.thumbs_dir)
    candidates: dict[str, Any] = {
        "sandbox_dir": app.state.sandbox_dir,
        "perception": perception,
        "executor": None,
        "approvals": app.state.approvals,
        "pace": pace,
        "kill": kill,
        "kill_switch": kill,
        "stop_event": kill,
        "cancel": kill,
        "commands": body.get("commands") or [],
    }
    kwargs = _filtered_kwargs(run_task, candidates)
    # Aynı kill switch'i birden çok adla geçirme.
    for dup in ("kill_switch", "stop_event", "cancel"):
        if "kill" in kwargs and dup in kwargs:
            kwargs.pop(dup)

    def worker() -> None:
        try:
            run_task(bus, session, agent, task, **kwargs)
        except Exception as exc:  # sürücü patlarsa UI sessiz kalmasın
            try:
                bus.emit(session, agent, "log", {"level": "error", "msg": f"driver error: {exc}"})
                bus.emit(session, agent, "session.failed", {"reason": str(exc)})
            except Exception:
                pass

    t = threading.Thread(target=worker, name=f"task-{session}", daemon=True)
    app.state.threads[session] = t
    t.start()
    return {"session": session, "task": task, "driver": driver, "agent": agent, "pace": pace}


def _resolve_approval(broker: Any, approval_id: str, approved: bool, by: str, session: str, agent: str) -> bool:
    """ApprovalBroker.resolve'u imzasına göre çağırır.

    Agent A'nın brokeri `approval.resolved` olayını yalnızca `session` verilirse
    yayınlar; UI'nın "ONAY BEKLİYOR" rozetini düşürebilmesi için onu geçiyoruz.
    """
    kwargs = _filtered_kwargs(broker.resolve, {"session": session, "agent": agent})
    try:
        return bool(broker.resolve(approval_id, approved, by, **kwargs))
    except TypeError:
        return bool(broker.resolve(approval_id, approved))


def _cmd_approval(app: FastAPI, sid: str, body: dict[str, Any], *, approved: bool) -> dict[str, Any]:
    broker = app.state.approvals
    bus: EventBus = app.state.bus
    raw = body.get("approval_id") or body.get("id")
    if isinstance(raw, dict):  # {"pending_approval": {...}} nesnesi de kabul edilir
        raw = raw.get("id")
    approval_id = str(raw or "").strip()
    session, agent = sid, str(body.get("agent") or "goat")
    found = _latest_pending_approval(bus, sid, approval_id or None) or _latest_pending_approval(bus, sid)
    if found is not None:
        session, agent, approval_id = found
    if not approval_id:
        raise HTTPException(status_code=400, detail="approval_id is required (no pending approval found)")
    if not getattr(broker, "available", False):
        raise HTTPException(status_code=503, detail="ApprovalBroker yok (agentlab.safety yüklenemedi)")
    by = str(body.get("by") or "ui")
    ok = _resolve_approval(broker, approval_id, approved, by, session, agent)
    return {"session": session, "approval_id": approval_id, "approved": approved, "resolved": ok, "by": by}


def _latest_pending_approval(
    bus: EventBus, sid: str, approval_id: str | None = None
) -> tuple[str, str, str] | None:
    """(session, agent, approval_id) — çözülmemiş en son onay isteği."""
    sessions = [sid] if sid in bus.history else list(bus.history)
    for s in sessions:
        resolved: set[str] = set()
        req: list[tuple[str, str]] = []
        for ev in bus.history.get(s, []):
            d = ev.data if isinstance(ev.data, dict) else {}
            if ev.type == "approval.requested" and d.get("id"):
                req.append((str(d["id"]), ev.agent))
            elif ev.type == "approval.resolved" and d.get("id"):
                resolved.add(str(d["id"]))
        for aid, agent in reversed(req):
            if aid in resolved:
                continue
            if approval_id and aid != approval_id:
                continue
            return s, agent, aid
    return None


def _cmd_abort(app: FastAPI, sid: str, body: dict[str, Any]) -> dict[str, Any]:
    """Kill switch: replay thread'lerini durdur, bekleyen onayları reddet, oturumu düşür."""
    bus: EventBus = app.state.bus
    broker = app.state.approvals
    targets = [sid] if sid in app.state.kill else list(app.state.kill)
    hit = 0
    for s in targets:
        ev = app.state.kill.get(s)
        if ev is not None and not ev.is_set():
            ev.set()
            hit += 1

    evs = bus.history.get(sid) or []
    agent = evs[-1].agent if evs else "goat"
    rejected = 0
    if getattr(broker, "available", False):
        while True:
            found = _latest_pending_approval(bus, sid)
            if found is None:
                break
            s, a, aid = found
            if not _resolve_approval(broker, aid, False, "abort", s, a):
                break
            rejected += 1
            if rejected > 8:
                break

    alive = app.state.threads.get(sid)
    running = bool(alive is not None and alive.is_alive())
    if sid in bus.history:
        last = evs[-1].type if evs else None
        try:
            bus.emit(sid, agent, "log", {"level": "warn", "msg": "kill switch: abort istendi"})
            if not running and last not in TERMINAL_TYPES:
                bus.emit(sid, agent, "session.failed", {"reason": "aborted"})
        except Exception:
            pass
    return {"session": sid, "aborted": hit, "rejected_approvals": rejected, "driver_running": running}


# --------------------------------------------------------------------------- #
# Replay işçisi
# --------------------------------------------------------------------------- #
def _replay_worker(
    bus: EventBus,
    session: str,
    agent: str,
    rows: list[dict[str, Any]],
    speed: float,
    kill: threading.Event,
) -> None:
    """Fixture olaylarını YENİ bir oturum kimliği altında veriyoluna geri basar."""
    prev: float | None = None
    for row in rows:
        if kill.is_set():
            try:
                bus.emit(session, agent, "session.failed", {"reason": "aborted"})
            except Exception:
                pass
            return
        cur = _ts_seconds(row.get("ts"))
        if prev is not None and cur is not None:
            delay = max(0.0, (cur - prev)) / max(speed, 0.01)
            if delay > 0:
                kill.wait(min(delay, REPLAY_MAX_GAP))
        if cur is not None:
            prev = cur
        etype = str(row.get("type") or "")
        data = row.get("data")
        if not isinstance(data, dict):
            data = {}
        row_agent = str(row.get("agent") or agent)
        if etype not in EVENT_TYPES:
            try:
                bus.emit(session, row_agent, "log", {"level": "warn", "msg": f"replay: bilinmeyen tür {etype!r}"})
            except Exception:
                pass
            continue
        try:
            bus.emit(session, row_agent, etype, data)
        except Exception:
            continue


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agentlab.gateway", description="AgentLab gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--auto-approve", action="store_true", help="riskli aksiyonları otomatik onayla (demo)")
    parser.add_argument("--ui-dir", default=str(ROOT / "ui"))
    parser.add_argument("--fixtures-dir", default=str(ROOT / "fixtures"))
    parser.add_argument("--thumbs-dir", default=str(ROOT / "thumbs"))
    parser.add_argument("--sandbox-dir", default=str(ROOT / "sandbox"))
    parser.add_argument("--store-dir", default=str(ROOT / "sessions"), help="NDJSON transcript dizini ('' → kapalı)")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    import uvicorn

    store = Path(args.store_dir) if args.store_dir else None
    bus = EventBus(store_dir=store)
    app = create_app(
        bus,
        ui_dir=args.ui_dir,
        fixtures_dir=args.fixtures_dir,
        thumbs_dir=args.thumbs_dir,
        sandbox_dir=args.sandbox_dir,
        auto_approve=args.auto_approve,
    )
    print(f"[agentlab] gateway → http://{args.host}:{args.port}/  (auto_approve={args.auto_approve})")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
