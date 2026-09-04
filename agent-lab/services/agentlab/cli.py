"""AgentLab CLI — sürücüyü çalıştır, NDJSON replay üret.

    python -m agentlab.cli run --driver scripted --task git-push \
        --out fixtures/replay-git-push.ndjson
    xvfb-run -a -s "-screen 0 1280x800x24" python -m agentlab.cli run \
        --driver scripted --task loop --with-display --out fixtures/replay-loop.ndjson
    python -m agentlab.cli run --driver claude --task "hesap makinesini aç" --with-display
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from .events import EventBus, new_session_id
from .safety import ApprovalBroker, SafetyConfig, SafetyGuard

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentlab.cli", description="AgentLab runner")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="bir oturum çalıştır ve olayları NDJSON'a yaz")
    run.add_argument("--driver", choices=["scripted", "claude"], default="scripted")
    run.add_argument("--task", required=True, help="scripted görev adı ya da doğal dil görevi")
    run.add_argument("--out", type=Path, default=None, help="NDJSON çıktısının kopyalanacağı yol")
    run.add_argument("--session", default=None, help="oturum kimliği (varsayılan: rastgele s_xxx)")
    run.add_argument("--agent", default="goat")
    run.add_argument("--pace", type=float, default=0.3, help="scripted adım gecikmesi (sn)")
    run.add_argument("--with-display", action="store_true",
                     help="gerçek ekran görüntüsü al (DISPLAY / xvfb-run gerekir)")
    run.add_argument("--sandbox", type=Path, default=REPO_ROOT / "sandbox")
    run.add_argument("--thumbs", type=Path, default=REPO_ROOT / "thumbs")
    run.add_argument("--runs", type=Path, default=REPO_ROOT / "runs")
    run.add_argument("--model", default="claude-opus-5")
    run.add_argument("--max-iterations", type=int, default=25)
    run.add_argument("--manual-approval", action="store_true",
                     help="onayları otomatik verme (gateway/insan çözecek)")
    run.add_argument("--commands", nargs="*", default=None, help="`shell` görevi için komutlar")
    return p


def cmd_run(args: argparse.Namespace) -> int:
    """Oturumu çalıştırır; 0 = başarı."""
    session = args.session or new_session_id()
    bus = EventBus(store_dir=Path(args.runs))
    perception = None
    if args.with_display:
        if not os.environ.get("DISPLAY"):
            print("hata: --with-display için DISPLAY gerekir (xvfb-run kullanın)", file=sys.stderr)
            return 2
        from .perception import ScreenPerceptionService
        perception = ScreenPerceptionService(bus, session, args.agent, thumb_dir=Path(args.thumbs))

    approvals = ApprovalBroker(bus, auto_approve=not args.manual_approval, timeout_s=120.0)
    guard = SafetyGuard(bus, config=SafetyConfig(auto_approve=not args.manual_approval),
                        approvals=approvals)

    try:
        if args.driver == "scripted":
            from .drivers import run_task
            executor = None
            if perception is not None:
                from .executor import ActionExecutorService
                executor = ActionExecutorService(bus, session, args.agent, guard=guard,
                                                 perception=perception)
            result = run_task(bus, session, args.agent, args.task,
                              sandbox_dir=Path(args.sandbox), perception=perception,
                              executor=executor, approvals=approvals, pace=args.pace,
                              commands=args.commands)
        else:
            from .executor import ActionExecutorService
            from .orchestrator import LLMOrchestrator, OrchestratorConfig
            if perception is None:
                print("uyarı: --driver claude için --with-display önerilir", file=sys.stderr)
            executor = ActionExecutorService(bus, session, args.agent, guard=guard,
                                             perception=perception)
            bus.emit(session, args.agent, "session.started",
                     {"driver": "claude", "task": args.task, "agent": args.agent})
            orch = LLMOrchestrator(bus, executor, config=OrchestratorConfig(
                model=args.model, max_iterations=args.max_iterations))
            try:
                result = orch.run(session, args.agent, args.task)
            except Exception as exc:  # API anahtarı yok / ağ hatası: akışı bozmadan bitir
                reason = f"{type(exc).__name__}: {exc}"
                bus.emit(session, args.agent, "session.failed", {"reason": reason})
                print(f"hata: claude sürücüsü başarısız — {reason}", file=sys.stderr)
                result = {"ok": False, "summary": reason}
            else:
                bus.emit(session, args.agent, "session.finished",
                         {"ok": result.get("ok", False), "summary": result.get("summary", "")})
    finally:
        bus.close()

    src = Path(args.runs) / f"{session}.ndjson"
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)
        print(f"{session}: {len(bus.history.get(session, []))} events -> {out}")
    else:
        print(f"{session}: {len(bus.history.get(session, []))} events -> {src}")
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
