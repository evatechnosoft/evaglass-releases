"""ScriptedDriver — Claude döngüsüyle *aynı* olay akışını üreten deterministik sürücü.

Neden: UI ekibi (Hat B) canlı ajana ve API anahtarına bağımlı olmadan çalışabilsin.
Sahte değil — kabuk komutları gerçekten `agent-lab/sandbox/<session>/` içinde koşar.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from ..safety import ApprovalBroker, classify, needs_approval

DEFAULT_TIMEOUT_S = 60.0
METRICS_EVERY = 8          # N olayda bir metrics.tick
OUTPUT_TAIL = 400          # action.executed içine giren çıktı kuyruğu
LOOP_TARGET = 10           # "salatalık dilimi" hedefi (plan §5, Görsel 1)


@dataclass(slots=True)
class Step:
    """Tek plan adımı: düşünce → karar → kabuk komutu."""

    think: str
    reason: str
    cmd: str
    cwd: str = "."


@dataclass(slots=True)
class ShellResult:
    """Kabuk komutu sonucu."""

    ok: bool
    code: int
    output: str


def run_shell(cmd: str, cwd: Path, timeout: float = DEFAULT_TIMEOUT_S) -> ShellResult:
    """Sandbox içinde `sh -c` ile çalıştırır; stdout+stderr kuyruğunu döner."""
    try:
        proc = subprocess.run(
            ["sh", "-c", cmd], cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return ShellResult(ok=False, code=124, output=f"timeout after {timeout}s")
    out = (proc.stdout + proc.stderr).strip()
    return ShellResult(ok=proc.returncode == 0, code=proc.returncode, output=out)


class _Ticker:
    """N olayda bir `metrics.tick` yayınlar (aksiyon/dk göstergesi için)."""

    def __init__(self, bus: Any, session: str, agent: str, every: int = METRICS_EVERY) -> None:
        self.bus, self.session, self.agent, self.every = bus, session, agent, every
        self.events = 0
        self.actions = 0
        self.started = time.monotonic()

    def bump(self, *, action: bool = False) -> None:
        self.events += 1
        if action:
            self.actions += 1
        if self.events % self.every == 0:
            self.tick()

    def tick(self) -> None:
        elapsed_min = max(1e-6, (time.monotonic() - self.started) / 60.0)
        self.bus.emit(self.session, self.agent, "metrics.tick", {
            "actions_per_min": round(self.actions / elapsed_min, 2),
            "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
        })


class ScriptedDriver:
    """Deterministik görev yürütücü; gateway bunu bir thread'de çağırır."""

    def __init__(
        self,
        bus: Any,
        session: str,
        agent: str,
        *,
        sandbox_dir: Path | str,
        perception: Any | None = None,
        executor: Any | None = None,
        approvals: ApprovalBroker | None = None,
        pace: float = 0.3,
    ) -> None:
        self.bus = bus
        self.session = session
        self.agent = agent
        self.sandbox = Path(sandbox_dir)
        self.perception = perception
        self.executor = executor
        self.approvals = approvals or ApprovalBroker(bus, auto_approve=True)
        self.pace = pace
        self.ticker = _Ticker(bus, session, agent)

    # ---------------------------------------------------------------- yardımcı

    def _emit(self, type_: str, data: dict[str, Any], *, action: bool = False) -> None:
        self.bus.emit(self.session, self.agent, type_, data)
        self.ticker.bump(action=action)

    def _sleep(self, factor: float = 1.0) -> None:
        if self.pace > 0:
            time.sleep(self.pace * factor)

    def _maybe_approve(self, action: str, payload: dict[str, Any]) -> bool:
        """Risk eşiği aşılırsa ApprovalBroker'a sorar; aksi halde True."""
        risk, reason = classify(action, payload)
        if not needs_approval(risk, "medium"):
            return True
        return self.approvals.request(
            self.session, self.agent, action, risk, f"{action}: {reason}",
        )

    def _shell_action(self, step: Step, work: Path) -> ShellResult:
        """llm.thinking → llm.decision → action.requested → subprocess → action.executed."""
        action_id = f"ac_{self.ticker.events:04d}"
        payload = {"text": step.cmd}
        self._emit("llm.thinking", {"model": "scripted", "effort": "low", "summary": step.think})
        self._sleep(0.5)
        self._emit("llm.decision", {"action": "type", "reason": step.reason, "model": "scripted"})
        self._emit("action.requested", {"id": action_id, "action": "type", "input": payload})

        if not self._maybe_approve("type", payload):
            self._emit("action.failed", {"id": action_id, "action": "type",
                                         "error": "approval denied"})
            return ShellResult(ok=False, code=126, output="approval denied")

        started = time.perf_counter()
        res = run_shell(step.cmd, (work / step.cwd).resolve())
        latency = (time.perf_counter() - started) * 1000.0
        if res.ok:
            self._emit("action.executed", {"id": action_id, "action": "type", "ok": True,
                                           "latency_ms": round(latency, 2),
                                           "output": res.output[-OUTPUT_TAIL:]},
                       action=True)
        else:
            self._emit("action.failed", {"id": action_id, "action": "type",
                                         "error": res.output[-OUTPUT_TAIL:] or f"exit {res.code}",
                                         "latency_ms": round(latency, 2)})
        return res

    def _click_action(self, action_id: str, coordinate: list[int]) -> None:
        """Tıklama olaylarını tek kaynaktan yayınlar: executor varsa o, yoksa sürücü."""
        payload = {"coordinate": coordinate}
        if self.executor is not None:
            self.executor.execute("left_click", payload, action_id=action_id)
            self.ticker.bump()
            self.ticker.bump(action=True)
            return
        self._emit("action.requested", {"id": action_id, "action": "left_click", "input": payload})
        self._emit("action.executed", {"id": action_id, "action": "left_click", "ok": True,
                                       "latency_ms": 12.0, "output": f"clicked {coordinate}"},
                   action=True)

    def _screenshot(self, index: int) -> None:
        """Perception varsa gerçek ekran görüntüsü; yoksa aynı şekle sahip sahte olay."""
        if self.perception is not None:
            self.perception.capture()
            self.ticker.bump()
            return
        self._emit("perception.screenshot", {
            "hash": f"fake{index:04d}", "width": 1366, "height": 768,
            "scale": 0.711458, "changed": True,
            "thumb_url": f"/thumbs/{self.session}/{index}.png",
        })

    # ------------------------------------------------------------------ görevler

    def run(self, task_name: str, *, commands: Sequence[str] | None = None) -> dict[str, Any]:
        """`session.started` … görev … `session.finished`."""
        work = self.sandbox / self.session
        work.mkdir(parents=True, exist_ok=True)
        self._emit("session.started", {"driver": "scripted", "task": task_name,
                                       "agent": self.agent, "sandbox": str(work)})
        try:
            handler = TASKS.get(task_name)
            if handler is None:
                raise ValueError(f"unknown scripted task: {task_name}")
            result = handler(self, work, commands)
        except Exception as exc:
            self.ticker.tick()
            self._emit("session.failed", {"reason": f"{type(exc).__name__}: {exc}"})
            return {"ok": False, "summary": str(exc)}
        self.ticker.tick()
        self._emit("session.finished", {"ok": result.get("ok", False),
                                        "summary": result.get("summary", "")})
        return result

    # -- (1) git-push ------------------------------------------------------

    def task_git_push(self, work: Path, _commands: Sequence[str] | None = None) -> dict[str, Any]:
        """Yerel bare repo'ya gerçek bir commit + push; push adımı onay ister (medium)."""
        origin = work / "origin.git"
        repo = work / "repo"
        if origin.exists():
            shutil.rmtree(origin)
        if repo.exists():
            shutil.rmtree(repo)
        origin.mkdir(parents=True)
        repo.mkdir(parents=True)
        run_shell("git init --bare -b main .", origin)

        self._emit("task.received", {"task": "commit and push a README to origin", "risk": "medium"})
        self._emit("task.plan", {"steps": ["git init", "write README.md", "git add",
                                           "git commit", "git push (approval)"]})

        steps = [
            Step(think="Depo yok; önce boş bir git deposu başlatmalıyım.",
                 reason="initialize repository",
                 cmd=("git init -b main . "
                      "&& git config user.email agent@agentlab.dev "
                      "&& git config user.name 'AgentLab Agent'"),
                 cwd="repo"),
            Step(think="README dosyası yazıp commit edilecek içerik oluşturuyorum.",
                 reason="write file",
                 cmd="printf '# AgentLab\\nscripted driver smoke test\\n' > README.md",
                 cwd="repo"),
            Step(think="Dosyayı stage'e alıyorum.",
                 reason="stage changes",
                 cmd="git add README.md",
                 cwd="repo"),
            Step(think="Değişikliği commit ediyorum.",
                 reason="commit",
                 cmd="git commit -m 'feat: agentlab scripted smoke'",
                 cwd="repo"),
            Step(think="Uzak depoyu ekleyip gönderiyorum — bu riskli, onay gerekecek.",
                 reason="push to origin",
                 cmd="git remote add origin ../origin.git && git push -u origin main",
                 cwd="repo"),
        ]
        total = len(steps)
        for i, step in enumerate(steps, start=1):
            res = self._shell_action(step, work)
            if self.perception is not None:
                self._screenshot(i)  # Anthropic tavsiyesi: her adımı screenshot ile bitir
            self._emit("task.progress", {"done": i, "total": total, "label": "steps"})
            if not res.ok:
                self._emit("task.done", {"ok": False,
                                         "summary": f"step {i}/{total} failed: {res.output[-200:]}"})
                return {"ok": False, "summary": f"step {i} failed"}
            self._sleep()
        head = run_shell("git --git-dir=origin.git rev-parse main", work)
        summary = f"pushed {head.output[:12]} to origin/main"
        self._emit("task.done", {"ok": True, "summary": summary})
        return {"ok": True, "summary": summary, "commit": head.output.strip()}

    # -- (2) loop ----------------------------------------------------------

    def task_loop(self, _work: Path, _commands: Sequence[str] | None = None) -> dict[str, Any]:
        """Plan §1'deki `cutCucumberLoop()`: 10 dilim, her turda tıkla + bak."""
        self._emit("task.received", {"task": f"cut {LOOP_TARGET} cucumber slices", "risk": "low"})
        self._emit("task.plan", {"steps": ["init", "move", "click", "increment", "check"]})
        for i in range(1, LOOP_TARGET + 1):
            self._emit("llm.thinking", {"model": "scripted", "effort": "low",
                                        "summary": f"slice {i}/{LOOP_TARGET}: hedefe yaklaş, kes."})
            self._emit("llm.decision", {"action": "left_click",
                                        "reason": f"cut slice {i}", "model": "scripted"})
            self._click_action(f"ac_{i:04d}", [412, 300 + (i % 5) * 6])
            self._screenshot(i)
            self._emit("task.progress", {"done": i, "total": LOOP_TARGET, "label": "slices"})
            self._sleep()
        self._emit("task.done", {"ok": True, "summary": f"{LOOP_TARGET}/{LOOP_TARGET} slices"})
        return {"ok": True, "summary": f"{LOOP_TARGET}/{LOOP_TARGET} slices", "done": LOOP_TARGET}

    # -- (3) shell ---------------------------------------------------------

    def task_shell(self, work: Path, commands: Sequence[str] | None = None) -> dict[str, Any]:
        """Dışarıdan verilen komut listesini sırayla çalıştırır."""
        cmds = list(commands or [])
        if not cmds:
            raise ValueError("shell task requires commands")
        self._emit("task.received", {"task": f"{len(cmds)} shell commands", "risk": "low"})
        for i, cmd in enumerate(cmds, start=1):
            step = Step(think=f"Komut {i}/{len(cmds)} çalıştırılacak.", reason=cmd[:80], cmd=cmd)
            res = self._shell_action(step, work)
            self._emit("task.progress", {"done": i, "total": len(cmds), "label": "commands"})
            if not res.ok:
                self._emit("task.done", {"ok": False, "summary": f"command {i} failed"})
                return {"ok": False, "summary": f"command {i} failed"}
            self._sleep()
        self._emit("task.done", {"ok": True, "summary": f"{len(cmds)} commands ok"})
        return {"ok": True, "summary": f"{len(cmds)} commands ok"}


#: görev adı → ScriptedDriver metodu
TASKS: dict[str, Callable[..., dict[str, Any]]] = {
    "git-push": ScriptedDriver.task_git_push,
    "loop": ScriptedDriver.task_loop,
    "shell": ScriptedDriver.task_shell,
}


def run_task(
    bus: Any,
    session: str,
    agent: str,
    task_name: str,
    *,
    sandbox_dir: Path | str,
    perception: Any | None = None,
    executor: Any | None = None,
    approvals: ApprovalBroker | None = None,
    pace: float = 0.3,
    commands: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Gateway'in bir thread'de çağırabileceği senkron giriş noktası."""
    driver = ScriptedDriver(bus, session, agent, sandbox_dir=sandbox_dir,
                            perception=perception, executor=executor,
                            approvals=approvals, pace=pace)
    return driver.run(task_name, commands=commands)
