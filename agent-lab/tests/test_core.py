"""AgentLab çekirdek testleri.

Kapsam: olay şeması doğrulaması, scripted sürücü (gerçek git commit + 10/10 döngü),
SafetyGuard risk sınıflandırma + onay akışı (auto ve manuel thread), orchestrator
döngüsü (FakeClient) ve ekran görüntüsü budama, perception ölçek matematiği.
Gerçek ekran gerektiren testler DISPLAY yoksa atlanır (`xvfb-run -a` ile koşun).
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from agentlab.drivers import run_task  # noqa: E402
from agentlab.events import EventBus  # noqa: E402
from agentlab.executor import COMPUTER_ACTIONS, ActionExecutorService  # noqa: E402
from agentlab.orchestrator import (  # noqa: E402
    BATCH_ABORT_TEXT, LLMOrchestrator, OrchestratorConfig, estimate_cost,
)
from agentlab.perception import MAX_WIDTH, scale_factor, scaled_size  # noqa: E402
from agentlab.safety import (  # noqa: E402
    ApprovalBroker, SafetyConfig, SafetyError, SafetyGuard, classify, needs_approval,
)

SCHEMA = json.loads((ROOT / "contracts" / "events.schema.json").read_text(encoding="utf-8"))
DEFS = SCHEMA["$defs"]
HAS_DISPLAY = bool(os.environ.get("DISPLAY"))
needs_display = pytest.mark.skipif(not HAS_DISPLAY, reason="DISPLAY yok (xvfb-run -a ile koşun)")


# --------------------------------------------------------------------- yardımcı

def validate_event(ev: dict[str, Any]) -> None:
    """Olayı üst şemaya, `data`'yı varsa `$defs[type]`'a doğrular."""
    jsonschema.validate(ev, SCHEMA)
    sub = DEFS.get(ev["type"])
    if sub is not None:
        jsonschema.validate(ev["data"], {"$schema": SCHEMA["$schema"], **sub})


def validate_stream(bus: EventBus, session: str) -> list[dict[str, Any]]:
    """Oturumun tüm olaylarını doğrular ve seq sürekliliğini kontrol eder."""
    events = [e.to_dict() for e in bus.history[session]]
    assert events, "no events emitted"
    for ev in events:
        validate_event(ev)
    assert [e["seq"] for e in events] == list(range(len(events)))
    return events


@pytest.fixture()
def bus(tmp_path: Path) -> EventBus:
    b = EventBus(store_dir=tmp_path / "runs")
    yield b
    b.close()


# ------------------------------------------------------------------- perception

def test_scale_factor_matches_anthropic_rule() -> None:
    """min(1, 1568/uzun_kenar, sqrt(1.15M/toplam)) + 1366 px genişlik tavanı."""
    assert scale_factor(1024, 768) == 1.0          # zaten limitlerin altında
    assert scale_factor(1280, 800) == 1.0
    s = scale_factor(1920, 1080)
    assert s == pytest.approx(MAX_WIDTH / 1920)     # genişlik tavanı bağlayıcı
    assert s < min(1568 / 1920, math.sqrt(1_150_000 / (1920 * 1080)))
    w, h, s4k = scaled_size(3840, 2160)
    assert (w, h) == (MAX_WIDTH, 768)
    assert 0 < s4k < 1


def test_scale_factor_never_upscales_and_rejects_zero() -> None:
    assert scale_factor(320, 200) == 1.0
    with pytest.raises(ValueError):
        scale_factor(0, 100)


@needs_display
def test_perception_capture_and_zoom(bus: EventBus, tmp_path: Path) -> None:
    """Gerçek Xvfb ekranında yakalama, hash, `changed` ve zoom kırpması."""
    from agentlab.perception import ScreenPerceptionService

    p = ScreenPerceptionService(bus, "s_percep", "goat", thumb_dir=tmp_path / "thumbs")
    first = p.capture()
    assert first.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert first.width <= MAX_WIDTH and first.changed is True
    assert first.thumb_url == "/thumbs/s_percep/0.png"
    thumb = tmp_path / "thumbs" / "s_percep" / "0.png"
    assert thumb.exists() and thumb.stat().st_size <= 40_000

    second = p.capture()
    assert second.hash == first.hash and second.changed is False  # ekran değişmedi

    z = p.zoom([10, 10, 110, 60])
    assert z.width >= 100 and z.height >= 50   # tam çözünürlükte kırpıldı

    events = validate_stream(bus, "s_percep")
    assert [e["type"] for e in events] == [
        "perception.screenshot", "perception.screenshot", "perception.zoom",
    ]
    assert events[-1]["data"]["region"] == [10, 10, 110, 60]


# ----------------------------------------------------------------------- safety

@pytest.mark.parametrize(("action", "payload", "expected"), [
    ("key", {"text": "alt+F4"}, "high"),
    ("key", {"text": "ctrl+alt+Delete"}, "high"),
    ("key", {"text": "cmd+q"}, "high"),
    ("key", {"text": "ctrl+s"}, "low"),
    ("type", {"text": "4111 1111 1111 1111"}, "high"),
    ("type", {"text": "cvv 123"}, "high"),
    ("type", {"text": "sudo apt install foo"}, "high"),
    ("type", {"text": "rm -rf /tmp/x"}, "high"),
    ("type", {"text": "git push --force origin main"}, "high"),
    ("type", {"text": "git push origin main"}, "medium"),
    ("type", {"text": "echo merhaba"}, "low"),
    ("left_click", {"coordinate": [1, 2]}, "low"),
])
def test_risk_classifier(action: str, payload: dict[str, Any], expected: str) -> None:
    risk, reason = classify(action, payload)
    assert risk == expected, reason


def test_needs_approval_threshold() -> None:
    assert needs_approval("medium", "medium") is True
    assert needs_approval("low", "medium") is False
    assert needs_approval("high", "medium") is True


def test_guard_auto_approve_flow(bus: EventBus) -> None:
    """auto_approve modunda medium risk otomatik onaylanır ve olaylar şemaya uyar."""
    guard = SafetyGuard(bus, config=SafetyConfig(auto_approve=True))
    risk = guard.check("s_auto", "goat", "type", {"text": "git push origin main"})
    assert risk == "medium"
    events = validate_stream(bus, "s_auto")
    types = [e["type"] for e in events]
    assert types == ["approval.requested", "approval.resolved"]
    assert events[0]["data"]["risk"] == "medium"
    assert events[1]["data"] == {"id": events[0]["data"]["id"], "approved": True, "by": "auto"}


def test_guard_manual_approval_resolved_from_another_thread(bus: EventBus) -> None:
    """İnsan onayı: check() bloke olur, ikinci thread resolve() ile serbest bırakır."""
    guard = SafetyGuard(bus, config=SafetyConfig(auto_approve=False, approval_timeout_s=5.0))
    result: dict[str, Any] = {}

    def worker() -> None:
        try:
            result["risk"] = guard.check("s_manual", "goat", "type", {"text": "git push origin main"})
        except SafetyError as exc:
            result["error"] = str(exc)

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.monotonic() + 5.0
    while not guard.approvals.pending_ids() and time.monotonic() < deadline:
        time.sleep(0.01)
    pending = guard.approvals.pending_ids()
    assert len(pending) == 1
    assert guard.approvals.resolve(pending[0], True, by="operator",
                                   session="s_manual", agent="goat") is True
    t.join(timeout=5.0)
    assert result.get("risk") == "medium", result

    events = validate_stream(bus, "s_manual")
    assert events[-1]["data"]["by"] == "operator"
    assert events[-1]["data"]["approved"] is True


def test_guard_denied_approval_blocks_action(bus: EventBus) -> None:
    guard = SafetyGuard(bus, config=SafetyConfig(auto_approve=False, approval_timeout_s=5.0))
    err: dict[str, Any] = {}

    def worker() -> None:
        try:
            guard.check("s_deny", "goat", "key", {"text": "alt+F4"})
        except SafetyError as exc:
            err["msg"] = str(exc)

    t = threading.Thread(target=worker)
    t.start()
    deadline = time.monotonic() + 5.0
    while not guard.approvals.pending_ids() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert guard.approvals.pending_ids()
    guard.approvals.resolve(guard.approvals.pending_ids()[0], False,
                            by="operator", session="s_deny", agent="goat")
    t.join(timeout=5.0)
    assert "approval denied" in err.get("msg", "")
    validate_stream(bus, "s_deny")


def test_approval_timeout_is_fail_closed(bus: EventBus) -> None:
    broker = ApprovalBroker(bus, auto_approve=False, timeout_s=0.05)
    assert broker.request("s_to", "goat", "type", "high", "risky") is False
    events = validate_stream(bus, "s_to")
    assert events[-1]["data"] == {"id": events[0]["data"]["id"], "approved": False, "by": "timeout"}


def test_kill_switch_and_rate_limit(bus: EventBus) -> None:
    guard = SafetyGuard(bus, config=SafetyConfig(max_actions_per_sec=2, auto_approve=True))
    guard.check("s_k", "goat", "left_click", {})
    guard.check("s_k", "goat", "left_click", {})
    with pytest.raises(SafetyError, match="rate limit"):
        guard.check("s_k", "goat", "left_click", {})
    guard.reset()
    guard.abort()
    with pytest.raises(SafetyError, match="kill switch"):
        guard.check("s_k", "goat", "left_click", {})


def test_allowlist_is_fail_closed(bus: EventBus) -> None:
    guard = SafetyGuard(bus, config=SafetyConfig(allowlist=("gedit",), auto_approve=True))
    guard.set_active_window("gedit — untitled")
    guard.check("s_al", "goat", "left_click", {})
    guard.set_active_window("Online Bank — Payments")
    with pytest.raises(SafetyError, match="allowlist"):
        guard.check("s_al", "goat", "left_click", {})


# --------------------------------------------------------------------- executor

class _FakePerception:
    """Perception yerine geçen sahte; scale ve PNG döner."""

    scale = 0.5

    def __init__(self) -> None:
        self.captures = 0
        self.zooms: list[Any] = []

    def capture(self) -> Any:
        self.captures += 1
        return type("S", (), {"png": b"\x89PNG-fake", "width": 640, "height": 400,
                              "changed": True, "hash": "h", "scale": self.scale})()

    def zoom(self, region: Any) -> Any:
        self.zooms.append(region)
        return type("S", (), {"png": b"\x89PNG-zoom", "width": 100, "height": 50,
                              "changed": True, "hash": "z", "scale": 1.0})()


def test_executor_covers_all_17_toolset_members() -> None:
    assert len(COMPUTER_ACTIONS) == 17
    assert COMPUTER_ACTIONS == {
        "screenshot", "zoom", "left_click", "right_click", "middle_click", "double_click",
        "triple_click", "left_click_drag", "mouse_move", "left_mouse_down", "left_mouse_up",
        "cursor_position", "scroll", "type", "key", "hold_key", "wait",
    }


def test_executor_dry_run_emits_contract_events(bus: EventBus) -> None:
    """dry_run: pynput'a dokunmadan dispatch + olay sözleşmesi doğrulanır."""
    guard = SafetyGuard(bus, config=SafetyConfig(auto_approve=True))
    percep = _FakePerception()
    ex = ActionExecutorService(bus, "s_exec", "goat", guard=guard,
                               perception=percep, dry_run=True)
    assert ex.execute("left_click", {"coordinate": [100, 200]}).ok
    assert ex.execute("screenshot", {}).image_png == b"\x89PNG-fake"
    assert ex.execute("zoom", {"region": [0, 0, 100, 50]}).image_png == b"\x89PNG-zoom"
    assert ex.execute("scroll", {"scroll_direction": "down", "scroll_amount": 3}).ok
    assert ex.execute("wait", {"duration": 0}).ok
    assert ex.execute("key", {"text": "ctrl+s", "repeat": 2}).ok
    bad = ex.execute("teleport", {})
    assert not bad.ok and "unsupported action" in bad.error

    events = validate_stream(bus, "s_exec")
    types = [e["type"] for e in events]
    assert types.count("action.requested") == 7
    assert types.count("action.executed") == 6
    assert types.count("action.failed") == 1
    assert percep.captures == 1 and percep.zooms == [[0, 0, 100, 50]]


def test_executor_scales_coordinates_back_to_screen(bus: EventBus) -> None:
    """Model koordinatı ekran-görüntüsü uzayında gelir; gerçek ekrana /scale ile gider."""
    guard = SafetyGuard(bus, config=SafetyConfig(auto_approve=True))
    ex = ActionExecutorService(bus, "s_scale", "goat", guard=guard,
                               perception=_FakePerception(), dry_run=True)
    assert ex.scale == 0.5
    assert ex.to_screen([683, 384]) == (1366, 768)


def test_executor_blocks_dangerous_key_without_approval(bus: EventBus) -> None:
    guard = SafetyGuard(bus, config=SafetyConfig(auto_approve=False, approval_timeout_s=0.05))
    ex = ActionExecutorService(bus, "s_block", "goat", guard=guard, dry_run=True)
    res = ex.execute("key", {"text": "ctrl+alt+Delete"})
    assert not res.ok and "approval denied" in res.error
    validate_stream(bus, "s_block")


@needs_display
def test_executor_real_pynput_under_xvfb(bus: EventBus, tmp_path: Path) -> None:
    """Gerçek Xvfb: pynput fareyi hareket ettirir, perception ölçeği uygulanır."""
    from agentlab.perception import ScreenPerceptionService

    percep = ScreenPerceptionService(bus, "s_real", "goat", thumb_dir=tmp_path / "thumbs")
    percep.capture()
    guard = SafetyGuard(bus, config=SafetyConfig(auto_approve=True))
    ex = ActionExecutorService(bus, "s_real", "goat", guard=guard, perception=percep)
    res = ex.execute("mouse_move", {"coordinate": [120, 90]})
    assert res.ok, res.error
    assert ex.execute("cursor_position", {}).ok
    assert ex.execute("type", {"text": "agentlab"}).ok
    assert ex.execute("key", {"text": "Return"}).ok
    validate_stream(bus, "s_real")


# ----------------------------------------------------------------- scripted driver

def test_scripted_git_push_makes_a_real_commit(bus: EventBus, tmp_path: Path) -> None:
    """Gerçek `git push`: bare origin'de commit oluşur, approval akışı çalışır."""
    result = run_task(bus, "s_git", "goat", "git-push",
                      sandbox_dir=tmp_path / "sandbox", pace=0.0)
    assert result["ok"], result
    origin = tmp_path / "sandbox" / "s_git" / "origin.git"
    assert origin.is_dir()

    import subprocess
    sha = subprocess.run(["git", "--git-dir", str(origin), "rev-parse", "main"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert len(sha) == 40
    subject = subprocess.run(["git", "--git-dir", str(origin), "log", "-1", "--pretty=%s"],
                             capture_output=True, text=True, check=True).stdout.strip()
    assert subject == "feat: agentlab scripted smoke"
    files = subprocess.run(["git", "--git-dir", str(origin), "ls-tree", "--name-only", "main"],
                           capture_output=True, text=True, check=True).stdout.split()
    assert files == ["README.md"]

    events = validate_stream(bus, "s_git")
    types = [e["type"] for e in events]
    assert types[0] == "session.started" and types[-1] == "session.finished"
    assert types.count("action.executed") == 5
    assert "approval.requested" in types and "approval.resolved" in types
    approval = next(e for e in events if e["type"] == "approval.requested")
    assert approval["data"]["risk"] == "medium"
    done = next(e for e in events if e["type"] == "task.done")
    assert done["data"]["ok"] is True
    assert sha.startswith(done["data"]["summary"].split()[1])


def test_scripted_git_push_denied_approval_stops_the_task(bus: EventBus, tmp_path: Path) -> None:
    """Onay reddedilirse push adımı yürütülmez ve görev ok:false biter."""
    broker = ApprovalBroker(bus, auto_approve=False, timeout_s=0.05)
    result = run_task(bus, "s_deny2", "goat", "git-push",
                      sandbox_dir=tmp_path / "sandbox", pace=0.0, approvals=broker)
    assert result["ok"] is False
    events = validate_stream(bus, "s_deny2")
    assert any(e["type"] == "action.failed" for e in events)
    done = next(e for e in events if e["type"] == "task.done")
    assert done["data"]["ok"] is False


def test_scripted_loop_reaches_ten_of_ten(bus: EventBus, tmp_path: Path) -> None:
    """Salatalık döngüsü 10/10'a ulaşır ve her turda bir screenshot yayınlar."""
    result = run_task(bus, "s_loop", "goat", "loop",
                      sandbox_dir=tmp_path / "sandbox", pace=0.0)
    assert result == {"ok": True, "summary": "10/10 slices", "done": 10}
    events = validate_stream(bus, "s_loop")
    progress = [e["data"] for e in events if e["type"] == "task.progress"]
    assert [p["done"] for p in progress] == list(range(1, 11))
    assert all(p["total"] == 10 and p["label"] == "slices" for p in progress)
    assert sum(1 for e in events if e["type"] == "perception.screenshot") == 10
    assert sum(1 for e in events if e["type"] == "action.executed") == 10
    assert any(e["type"] == "metrics.tick" for e in events)
    assert events[-1]["type"] == "session.finished"


def test_scripted_shell_runs_commands_in_sandbox(bus: EventBus, tmp_path: Path) -> None:
    result = run_task(bus, "s_sh", "goat", "shell", sandbox_dir=tmp_path / "sandbox",
                      pace=0.0, commands=["echo hello > a.txt", "cat a.txt"])
    assert result["ok"] is True
    assert (tmp_path / "sandbox" / "s_sh" / "a.txt").read_text().strip() == "hello"
    validate_stream(bus, "s_sh")


def test_scripted_unknown_task_emits_session_failed(bus: EventBus, tmp_path: Path) -> None:
    result = run_task(bus, "s_bad", "goat", "nope", sandbox_dir=tmp_path / "sandbox", pace=0.0)
    assert result["ok"] is False
    events = validate_stream(bus, "s_bad")
    assert events[-1]["type"] == "session.failed"


@needs_display
def test_scripted_loop_with_real_perception(bus: EventBus, tmp_path: Path) -> None:
    """Perception verilirse gerçek ekran görüntüsü alınır; olay şekli değişmez."""
    from agentlab.perception import ScreenPerceptionService

    percep = ScreenPerceptionService(bus, "s_loop2", "goat", thumb_dir=tmp_path / "thumbs")
    result = run_task(bus, "s_loop2", "goat", "loop", sandbox_dir=tmp_path / "sandbox",
                      perception=percep, pace=0.0)
    assert result["ok"] is True
    events = validate_stream(bus, "s_loop2")
    shots = [e for e in events if e["type"] == "perception.screenshot"]
    assert len(shots) == 10
    assert all(len(s["data"]["hash"]) == 40 for s in shots)  # gerçek sha1


# ------------------------------------------------------------------ orchestrator

class FakeClient:
    """Anthropic istemcisi yerine geçen sahte; senaryodaki yanıtları sırayla döner."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.beta = type("Beta", (), {"messages": self})()

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        return self._responses.pop(0)


def _tool_use(tid: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name,
            "toolset_name": "computer", "input": payload}


def _resp(content: list[dict[str, Any]], stop: str = "tool_use", **extra: Any) -> dict[str, Any]:
    return {"content": content, "stop_reason": stop,
            "usage": {"input_tokens": 1000, "output_tokens": 200}, **extra}


def _orch(bus: EventBus, client: FakeClient, **cfg: Any) -> tuple[LLMOrchestrator, ActionExecutorService]:
    guard = SafetyGuard(bus, config=SafetyConfig(auto_approve=True))
    ex = ActionExecutorService(bus, "s_llm", "goat", guard=guard,
                               perception=_FakePerception(), dry_run=True)
    return LLMOrchestrator(bus, ex, client=client, config=OrchestratorConfig(**cfg)), ex


def test_orchestrator_runs_scripted_sequence(bus: EventBus) -> None:
    """screenshot → left_click → type → end_turn; aksiyonlar executor'dan geçer."""
    client = FakeClient([
        _resp([{"type": "thinking", "thinking": "Önce ekrana bakayım."},
               _tool_use("t1", "screenshot", {})]),
        _resp([{"type": "text", "text": "Arama kutusuna tıklıyorum."},
               _tool_use("t2", "left_click", {"coordinate": [400, 300]})]),
        _resp([{"type": "text", "text": "Metni yazıyorum."},
               _tool_use("t3", "type", {"text": "merhaba"}),
               _tool_use("t4", "screenshot", {})]),
        _resp([{"type": "text", "text": "Görev tamam."}], stop="end_turn"),
    ])
    orch, _ = _orch(bus, client)
    result = orch.run("s_llm", "goat", "arama kutusuna merhaba yaz")
    assert result["ok"] is True and result["iterations"] == 4

    events = validate_stream(bus, "s_llm")
    types = [e["type"] for e in events]
    assert types[0] == "task.received"
    assert types[-1] == "task.done"
    decisions = [e["data"]["action"] for e in events if e["type"] == "llm.decision"]
    assert decisions == ["screenshot", "left_click", "type", "screenshot"]
    executed = [e["data"]["action"] for e in events if e["type"] == "action.executed"]
    assert executed == ["screenshot", "left_click", "type", "screenshot"]
    thinking = next(e for e in events if e["type"] == "llm.thinking")
    assert thinking["data"]["model"] == "claude-opus-5"
    assert thinking["data"]["summary"] == "Önce ekrana bakayım."
    assert thinking["data"]["effort"] == "high"


def test_orchestrator_request_shape(bus: EventBus) -> None:
    """İstek gövdesi plan §4'e uyar: toolset, adaptive thinking, effort, fallback."""
    client = FakeClient([_resp([{"type": "text", "text": "bitti"}], stop="end_turn")])
    orch, _ = _orch(bus, client)
    orch.run("s_llm", "goat", "test")
    call = client.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["max_tokens"] == 16000
    assert call["tools"] == [{"type": "computer_toolset_20260801"}]
    assert call["thinking"]["type"] == "adaptive"
    assert call["output_config"] == {"effort": "high"}
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert "zoom" in call["system"] and "screenshot" in call["system"]


def test_orchestrator_tool_results_carry_toolset_name_and_images(bus: EventBus) -> None:
    client = FakeClient([
        _resp([_tool_use("t1", "screenshot", {}), _tool_use("t2", "left_click", {"coordinate": [1, 2]})]),
        _resp([{"type": "text", "text": "ok"}], stop="end_turn"),
    ])
    orch, _ = _orch(bus, client)
    orch.run("s_llm", "goat", "test")
    results = orch.messages[2]["content"]
    assert all(r["toolset_name"] == "computer" for r in results)
    assert results[0]["content"][0]["type"] == "image"
    assert results[0]["content"][0]["source"]["media_type"] == "image/png"
    assert results[1]["content"][0]["type"] == "text"


def test_orchestrator_marks_remaining_tools_after_first_failure(bus: EventBus) -> None:
    """İlk hata sonrası kalan tool_result'lar is_error + sabit metinle döner."""
    client = FakeClient([
        _resp([_tool_use("t1", "left_click", {"coordinate": [1, 2]}),
               _tool_use("t2", "nonexistent_action", {}),
               _tool_use("t3", "type", {"text": "asla yazılmamalı"}),
               _tool_use("t4", "screenshot", {})]),
        _resp([{"type": "text", "text": "toparlıyorum"}], stop="end_turn"),
    ])
    orch, ex = _orch(bus, client)
    orch.run("s_llm", "goat", "test")
    results = orch.messages[2]["content"]
    assert results[0].get("is_error") is None
    assert results[1]["is_error"] is True
    assert results[2] == {"type": "tool_result", "tool_use_id": "t3", "toolset_name": "computer",
                          "is_error": True,
                          "content": [{"type": "text", "text": BATCH_ABORT_TEXT}]}
    assert results[3]["content"][0]["text"] == BATCH_ABORT_TEXT
    executed = [e.data["action"] for e in bus.history["s_llm"] if e.type == "action.executed"]
    assert executed == ["left_click"]  # hatadan sonrakiler yürütülmedi
    assert ex.perception.captures == 0


def test_orchestrator_refusal_emits_session_failed(bus: EventBus) -> None:
    client = FakeClient([_resp([], stop="refusal", stop_details={"type": "refusal", "category": "cyber"})])
    orch, _ = _orch(bus, client)
    result = orch.run("s_llm", "goat", "kötü şeyler yap")
    assert result["ok"] is False and "refused" in result["summary"]
    events = validate_stream(bus, "s_llm")
    failed = next(e for e in events if e["type"] == "session.failed")
    assert failed["data"]["reason"] == "refusal:cyber"


def test_orchestrator_metrics_and_cost(bus: EventBus) -> None:
    client = FakeClient([
        _resp([_tool_use("t1", "left_click", {"coordinate": [1, 2]})]),
        _resp([{"type": "text", "text": "bitti"}], stop="end_turn"),
    ])
    orch, _ = _orch(bus, client)
    orch.run("s_llm", "goat", "test")
    ticks = [e["data"] for e in validate_stream(bus, "s_llm") if e["type"] == "metrics.tick"]
    assert len(ticks) == 2
    assert ticks[-1]["tokens_in"] == 2000 and ticks[-1]["tokens_out"] == 400
    assert ticks[-1]["cost_usd"] == pytest.approx(estimate_cost("claude-opus-5", 2000, 400))
    assert estimate_cost("claude-opus-5", 1_000_000, 1_000_000) == 30.0
    assert estimate_cost("claude-sonnet-5", 1_000_000, 1_000_000) == 12.0


def test_orchestrator_max_iterations_guard(bus: EventBus) -> None:
    client = FakeClient([_resp([_tool_use(f"t{i}", "left_click", {"coordinate": [1, 2]})])
                         for i in range(3)])
    orch, _ = _orch(bus, client, max_iterations=3)
    result = orch.run("s_llm", "goat", "sonsuz döngü")
    assert result["ok"] is False and result["iterations"] == 3
    done = next(e for e in validate_stream(bus, "s_llm") if e["type"] == "task.done")
    assert done["data"]["ok"] is False


def _image_result(tid: str) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tid, "toolset_name": "computer",
            "content": [{"type": "image", "source": {"type": "base64",
                                                     "media_type": "image/png", "data": "x"}}]}


def test_prune_images_keeps_last_six_in_batches_of_three(bus: EventBus) -> None:
    """Cache kararlılığı: fazlalık 3'e ulaşmadan budama yapılmaz, sonra toplu budanır."""
    orch, _ = _orch(bus, FakeClient([]), keep_images=6, prune_batch=3)
    orch.messages = [{"role": "user", "content": "task"}]

    def add(tid: str) -> None:
        orch.messages.append({"role": "assistant", "content": []})
        orch.messages.append({"role": "user", "content": [_image_result(tid)]})

    for i in range(8):
        add(f"t{i}")
    assert orch.prune_images() == 0          # 8 - 6 = 2 < 3 → henüz budama yok

    add("t8")
    assert orch.prune_images() == 3          # 9 - 6 = 3 → toplu budama
    texts = [b["content"][0] for m in orch.messages if m["role"] == "user"
             and isinstance(m["content"], list) for b in m["content"]]
    pruned = [t for t in texts if t.get("type") == "text"]
    images = [t for t in texts if t.get("type") == "image"]
    assert len(pruned) == 3 and len(images) == 6
    assert all(t["text"] == "[screenshot pruned]" for t in pruned)
    # en eski üçü budanmış olmalı
    remaining_ids = [b["tool_use_id"] for m in orch.messages if m["role"] == "user"
                     and isinstance(m["content"], list) for b in m["content"]
                     if b["content"][0].get("type") == "image"]
    assert remaining_ids == [f"t{i}" for i in range(3, 9)]
    assert orch.prune_images() == 0          # idempotent


def test_prune_images_runs_inside_the_loop(bus: EventBus) -> None:
    """10 tur screenshot sonrası geçmişte en fazla keep_images+prune_batch-1 görüntü kalır."""
    responses = [_resp([_tool_use(f"t{i}", "screenshot", {})]) for i in range(10)]
    responses.append(_resp([{"type": "text", "text": "bitti"}], stop="end_turn"))
    orch, _ = _orch(bus, FakeClient(responses), keep_images=6, prune_batch=3, max_iterations=20)
    orch.run("s_llm", "goat", "çok ekran görüntüsü")
    images = sum(1 for _ in orch._image_slots())
    assert 6 <= images <= 8
    validate_stream(bus, "s_llm")


# ---------------------------------------------------------------------- fixtures

@pytest.mark.parametrize("name", ["replay-git-push.ndjson", "replay-loop.ndjson"])
def test_committed_fixtures_validate_against_schema(name: str) -> None:
    """UI ekibi bu dosyalarla çalışıyor: her satır sözleşmeye uymalı."""
    path = ROOT / "fixtures" / name
    if not path.exists():
        pytest.skip(f"{name} henüz üretilmedi (CLI ile üretin)")
    seqs: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        validate_event(ev)
        seqs.append(ev["seq"])
    assert seqs == list(range(len(seqs)))
    assert len(seqs) > 20


def test_scripted_kill_switch_aborts_loop(tmp_path):
    """DURDUR: kill switch basılınca loop görevi session.failed{reason:aborted} ile biter."""
    import threading
    from agentlab.events import EventBus
    from agentlab.drivers.scripted import run_task

    bus = EventBus()
    kill = threading.Event()
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(e.type))

    def stop_after_first_progress(ev):
        if ev.type == "task.progress" and ev.data.get("done", 0) >= 2:
            kill.set()
    bus.subscribe(stop_after_first_progress)

    result = run_task(bus, "s_kill01", "goat", "loop", sandbox_dir=tmp_path, pace=0.01, kill=kill)
    assert result["ok"] is False and "abort" in result["summary"]
    assert "session.failed" in seen and "task.done" not in seen
    assert seen.index("session.failed") > max(i for i, t in enumerate(seen) if t == "task.progress")
    done = [e for e in bus.history["s_kill01"] if e.type == "task.progress"]
    assert done and max(e.data["done"] for e in done) < 10


def test_scripted_no_fake_thumb_url_without_perception(tmp_path):
    from agentlab.events import EventBus
    from agentlab.drivers.scripted import run_task
    bus = EventBus()
    run_task(bus, "s_nothumb", "goat", "loop", sandbox_dir=tmp_path, pace=0)
    shots = [e for e in bus.history["s_nothumb"] if e.type == "perception.screenshot"]
    assert shots and all("thumb_url" not in e.data for e in shots)
