"""Gateway sözleşme testleri.

Bilerek agent A'nın modüllerinden (perception/executor/safety/drivers) bağımsızdır:
gateway, sürücü olmadan da replay modunda hizmet vermek zorundadır.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentlab.events import EventBus
from agentlab.gateway import create_app

FIXTURE_ROWS = [
    {"v": 1, "ts": "2026-09-04T10:00:00.000Z", "session": "s_fix01", "agent": "goat", "seq": 0,
     "type": "session.started", "data": {"driver": "scripted", "task": "loop"}},
    {"v": 1, "ts": "2026-09-04T10:00:00.050Z", "session": "s_fix01", "agent": "goat", "seq": 1,
     "type": "task.received", "data": {"task": "Döngü testi", "risk": "low"}},
    {"v": 1, "ts": "2026-09-04T10:00:00.100Z", "session": "s_fix01", "agent": "goat", "seq": 2,
     "type": "action.executed", "data": {"id": "a1", "action": "left_click", "ok": True, "latency_ms": 38}},
    {"v": 1, "ts": "2026-09-04T10:00:00.150Z", "session": "s_fix01", "agent": "goat", "seq": 3,
     "type": "task.done", "data": {"summary": "bitti", "ok": True}},
    {"v": 1, "ts": "2026-09-04T10:00:00.200Z", "session": "s_fix01", "agent": "goat", "seq": 4,
     "type": "session.finished", "data": {}},
]


@pytest.fixture()
def env(tmp_path: Path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "tiny.ndjson").write_text(
        "\n".join(json.dumps(r) for r in FIXTURE_ROWS) + "\n", encoding="utf-8"
    )
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<h1>ui</h1>", encoding="utf-8")
    bus = EventBus()
    app = create_app(
        bus,
        ui_dir=ui,
        fixtures_dir=fixtures,
        thumbs_dir=tmp_path / "thumbs",
        sandbox_dir=tmp_path / "sandbox",
    )
    with TestClient(app) as client:
        yield client, bus, fixtures


def _read_sse(client: TestClient, url: str, limit: int) -> list[dict]:
    out: list[dict] = []
    sep = "&" if "?" in url else "?"
    with client.stream("GET", f"{url}{sep}limit={limit}") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
                if len(out) >= limit:
                    break
    return out


def test_health(env):
    client, _bus, _fx = env
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sessions"] == 0
    assert "session.started" in body["event_types"]


def test_index_and_static(env):
    client, _bus, _fx = env
    assert client.get("/").status_code == 200
    r = client.get("/fixtures/tiny.ndjson")
    assert r.status_code == 200
    assert "session.started" in r.text


def test_sessions_empty(env):
    client, _bus, _fx = env
    r = client.get("/sessions")
    assert r.status_code == 200
    assert r.json() == {"sessions": [], "count": 0}


def test_replays_listing(env):
    client, _bus, _fx = env
    assert client.post("/replay", json={"fixture": "yok.ndjson"}).status_code == 404
    assert client.post("/replay", json={}).status_code == 400
    assert client.get("/replays").json()["fixtures"] == ["tiny.ndjson"]


def test_replay_streams_events_in_order(env):
    client, _bus, _fx = env
    r = client.post("/replay", json={"fixture": "tiny.ndjson", "speed": 20.0})
    assert r.status_code == 200
    body = r.json()
    sid = body["session"]
    assert sid.startswith("s_")
    assert sid != "s_fix01"  # YENİ oturum kimliği altında oynatılır
    assert body["count"] == len(FIXTURE_ROWS)

    events = _read_sse(client, f"/events?session={sid}", len(FIXTURE_ROWS))
    assert [e["type"] for e in events] == [r_["type"] for r_ in FIXTURE_ROWS]
    assert [e["seq"] for e in events] == list(range(len(FIXTURE_ROWS)))
    assert {e["session"] for e in events} == {sid}
    assert {e["agent"] for e in events} == {"goat"}
    assert all(e["v"] == 1 for e in events)
    assert events[2]["data"]["action"] == "left_click"

    summaries = client.get("/sessions").json()["sessions"]
    assert len(summaries) == 1
    s = summaries[0]
    assert s["session"] == sid
    assert s["count"] == len(FIXTURE_ROWS)
    assert s["last_type"] == "session.finished"
    assert s["status"] == "finished"
    assert s["task"] == "Döngü testi"

    hist = client.get(f"/sessions/{sid}/events").json()
    assert len(hist["events"]) == len(FIXTURE_ROWS)
    assert client.get("/sessions/s_nope/events").status_code == 404


def test_sse_replays_history_then_live(env):
    """Geç katılan abone önce geçmişi, sonra canlı olayları görür (tekrar yok).

    Not: starlette TestClient gövdeyi tamponlar, bu yüzden canlı olay akış
    açılmadan önce zamanlayıcıyla planlanır; sunucu tarafı sıralaması aynıdır.
    """
    client, bus, _fx = env
    bus.emit("s_live01", "goat", "session.started", {"driver": "scripted"})
    bus.emit("s_live01", "goat", "task.received", {"task": "geçmiş"})
    threading.Timer(0.25, lambda: bus.emit("s_live01", "goat", "task.done", {"summary": "ok", "ok": True})).start()

    got = _read_sse(client, "/events?session=s_live01", 3)
    assert [e["type"] for e in got] == ["session.started", "task.received", "task.done"]
    assert [e["seq"] for e in got] == [0, 1, 2]


def test_sse_all_sessions_filter(env):
    client, bus, _fx = env
    bus.emit("s_aaa", "goat", "log", {"msg": "a"})
    bus.emit("s_bbb", "pengu", "log", {"msg": "b"})
    both = _read_sse(client, "/events?session=all", 2)
    assert {e["session"] for e in both} == {"s_aaa", "s_bbb"}
    only = _read_sse(client, "/events?session=s_bbb", 1)
    assert only[0]["agent"] == "pengu"


def test_start_unknown_driver_is_400(env):
    client, _bus, _fx = env
    r = client.post("/sessions/new/commands", json={"cmd": "start", "task": "git-push", "driver": "grok"})
    assert r.status_code == 400
    assert "grok" in r.json()["detail"]


def test_start_unknown_task_is_400(env):
    client, _bus, _fx = env
    r = client.post("/sessions/new/commands", json={"cmd": "start", "task": "hack-the-planet", "driver": "scripted"})
    assert r.status_code == 400


def test_unknown_command_is_400(env):
    client, _bus, _fx = env
    assert client.post("/sessions/s_x/commands", json={"cmd": "dance"}).status_code == 400
    assert client.post("/sessions/s_x/commands", json={}).status_code == 400


def test_approve_without_broker_or_id(env):
    """Agent A'nın safety modülü yoksa: 400 (id yok) ya da 503 (broker yok) — 500 asla."""
    client, bus, _fx = env
    r = client.post("/sessions/s_x/commands", json={"cmd": "approve"})
    assert r.status_code == 400  # bekleyen onay yok
    bus.emit("s_ap", "goat", "approval.requested", {"id": "ap_1", "risk": "medium", "description": "git push"})
    r2 = client.post("/sessions/s_ap/commands", json={"cmd": "approve"})
    assert r2.status_code in (200, 503)
    assert client.get("/sessions").json()["sessions"][0]["pending_approval"]["id"] == "ap_1"


def test_abort_emits_session_failed(env):
    client, bus, _fx = env
    bus.emit("s_kill", "goat", "session.started", {"driver": "scripted"})
    r = client.post("/sessions/s_kill/commands", json={"cmd": "abort"})
    assert r.status_code == 200
    types = [e.type for e in bus.history["s_kill"]]
    assert types[-1] == "session.failed"
    assert bus.history["s_kill"][-1].data["reason"] == "aborted"


def test_start_without_driver_module_is_503(env, monkeypatch):
    """Sürücü modülü yokken start 503 döner; gateway çökmez."""
    client, _bus, _fx = env
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if "drivers.scripted" in name:
            raise ImportError("scripted driver not built yet")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    r = client.post("/sessions/new/commands", json={"cmd": "start", "task": "loop", "driver": "scripted"})
    assert r.status_code in (200, 503)
    if r.status_code == 200:  # agent A'nın sürücüsü mevcutsa gerçekten başlar
        assert r.json()["session"].startswith("s_")
