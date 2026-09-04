"""LLMOrchestrator — `computer_toolset_20260801` ajan döngüsü (Claude).

Plan §4'ün şekli:
  1. `tools=[{"type":"computer_toolset_20260801"}]`, `thinking={"type":"adaptive"}`,
     `output_config={"effort":"high"}`, server-side fallback açık.
  2. Dönen TÜM `tool_use` bloklarını sırayla yürüt; ilk hatadan sonrakiler
     `is_error: true` + "Not executed: an earlier computer action in this turn failed."
  3. Her `tool_result` `toolset_name: "computer"` taşır; screenshot/zoom sonuçları image bloğu.
  4. Ekran görüntüsü geçmişini TOPLU buda (cache kararlılığı).
  5. `stop_reason == "refusal"` → `session.failed`.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Iterable

from .executor import ActionExecutorService

COMPUTER_TOOLSET = "computer_toolset_20260801"
TOOLSET_NAME = "computer"
BATCH_ABORT_TEXT = "Not executed: an earlier computer action in this turn failed."

#: USD / 1M token (giriş, çıkış).
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

SYSTEM_PROMPT = """Sen AgentLab'in bilgisayar kullanan ajanısın (computer-use agent).
Yalıtılmış bir Linux masaüstünde çalışıyorsun. Turkish/English mixed input is fine.

Çalışma kuralları / working rules:
- Her adımdan sonra `screenshot` al ve hedefe ulaşıp ulaşmadığını değerlendir.
  After each step take a screenshot and evaluate whether the goal was reached.
- Küçük yazı, ikon veya form alanlarını okumak için `zoom` kullan; tahmin etme.
  Use `zoom` for small text instead of guessing.
- Açılır menü / dropdown ve navigasyonda klavye kısayolunu tercih et (`key`), fareyi
  ancak gerektiğinde kullan. Prefer keyboard shortcuts for dropdowns and menus.
- Bir aksiyon batch'ini her zaman `screenshot` ile bitir.
- Koordinatlar sana verdiğim ekran görüntüsünün piksel uzayındadır.
- Riskli bir şey (ödeme, dosya silme, force push) gerekiyorsa önce açıkça söyle;
  sistem insan onayı isteyecektir.
- Emin değilsen dur ve nedenini yaz; uydurma.
"""


def _get(block: Any, key: str, default: Any = None) -> Any:
    """SDK pydantic bloğu ya da düz dict — ikisinden de alan okur."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """USD maliyet tahmini; bilinmeyen model opus-5 fiyatıyla sayılır."""
    price_in, price_out = PRICING.get(model, PRICING["claude-opus-5"])
    return tokens_in / 1_000_000 * price_in + tokens_out / 1_000_000 * price_out


@dataclass(slots=True)
class OrchestratorConfig:
    """Döngü ayarları."""

    model: str = "claude-opus-5"
    max_tokens: int = 16000
    effort: str = "high"
    max_iterations: int = 25
    keep_images: int = 6
    prune_batch: int = 3
    betas: tuple[str, ...] = ("server-side-fallback-2026-07-01",)
    fallbacks: Any = "default"


class LLMOrchestrator:
    """Claude döngüsü; her karar/aksiyon EventBus'a yayınlanır."""

    def __init__(
        self,
        bus: Any,
        executor: ActionExecutorService,
        *,
        client: Any | None = None,
        config: OrchestratorConfig | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.bus = bus
        self.executor = executor
        self.config = config or OrchestratorConfig()
        self.system_prompt = system_prompt
        self._client = client
        self.messages: list[dict[str, Any]] = []
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0

    @property
    def client(self) -> Any:
        """Enjekte edilmemişse gerçek Anthropic istemcisini tembel kurar."""
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------------------ istek

    def _create(self) -> Any:
        cfg = self.config
        return self.client.beta.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=self.system_prompt,
            messages=self.messages,
            tools=[{"type": COMPUTER_TOOLSET}],
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": cfg.effort},
            betas=list(cfg.betas),
            fallbacks=cfg.fallbacks,
        )

    # ------------------------------------------------------------------ döngü

    def run(self, session: str, agent: str, task: str) -> dict[str, Any]:
        """Görevi çalıştırır; `{ok, summary, iterations}` döner."""
        cfg = self.config
        self.messages = [{"role": "user", "content": task}]
        self.bus.emit(session, agent, "task.received", {"task": task, "risk": "low"})

        summary = ""
        for iteration in range(1, cfg.max_iterations + 1):
            self.bus.emit(session, agent, "task.progress",
                          {"done": iteration, "total": cfg.max_iterations, "label": "iteration"})
            response = self._create()
            self._emit_metrics(session, agent, response)

            if _get(response, "stop_reason") == "refusal":
                details = _get(response, "stop_details") or {}
                reason = _get(details, "category", "refusal") or "refusal"
                self.bus.emit(session, agent, "session.failed",
                              {"reason": f"refusal:{reason}", "model": cfg.model})
                return {"ok": False, "summary": f"model refused ({reason})", "iterations": iteration}

            content = list(_get(response, "content", []) or [])
            text = self._emit_thinking(session, agent, content)
            if text:
                summary = text

            tool_uses = [b for b in content
                         if _get(b, "type") == "tool_use" and _get(b, "toolset_name") == TOOLSET_NAME]
            if not tool_uses:
                self.bus.emit(session, agent, "task.done",
                              {"ok": True, "summary": summary or "no further actions"})
                return {"ok": True, "summary": summary, "iterations": iteration}

            self.messages.append({"role": "assistant", "content": content})
            results = self._run_batch(session, agent, tool_uses, text)
            self.messages.append({"role": "user", "content": results})
            self.prune_images()

        self.bus.emit(session, agent, "task.done",
                      {"ok": False, "summary": f"max iterations ({cfg.max_iterations}) reached"})
        return {"ok": False, "summary": "max iterations reached", "iterations": cfg.max_iterations}

    # --------------------------------------------------------------- parçalar

    def _emit_metrics(self, session: str, agent: str, response: Any) -> None:
        usage = _get(response, "usage") or {}
        t_in = int(_get(usage, "input_tokens", 0) or 0)
        t_out = int(_get(usage, "output_tokens", 0) or 0)
        self.tokens_in += t_in
        self.tokens_out += t_out
        self.cost_usd += estimate_cost(self.config.model, t_in, t_out)
        self.bus.emit(session, agent, "metrics.tick", {
            "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 6), "model": self.config.model,
        })

    def _emit_thinking(self, session: str, agent: str, content: Iterable[Any]) -> str:
        """`llm.thinking` yayınlar ve serbest metni (karar gerekçesi) döner."""
        thoughts: list[str] = []
        texts: list[str] = []
        for b in content:
            btype = _get(b, "type")
            if btype == "thinking":
                t = _get(b, "thinking") or ""
                if t:
                    thoughts.append(str(t))
            elif btype == "text":
                t = _get(b, "text") or ""
                if t:
                    texts.append(str(t))
        data: dict[str, Any] = {"model": self.config.model, "effort": self.config.effort}
        summary = " ".join(thoughts).strip() or " ".join(texts).strip()
        if summary:
            data["summary"] = summary[:500]
        self.bus.emit(session, agent, "llm.thinking", data)
        return " ".join(texts).strip()

    def _run_batch(self, session: str, agent: str, tool_uses: list[Any], reason: str) -> list[dict[str, Any]]:
        """Batch'i sırayla yürüt; ilk hatadan sonrakileri `is_error` ile işaretle."""
        results: list[dict[str, Any]] = []
        failed = False
        for block in tool_uses:
            name = str(_get(block, "name"))
            tool_id = str(_get(block, "id"))
            payload = _get(block, "input") or {}
            self.bus.emit(session, agent, "llm.decision", {
                "action": name, "reason": (reason or "")[:200], "model": self.config.model,
            })
            if failed:
                results.append(self._error_result(tool_id, BATCH_ABORT_TEXT))
                continue
            res = self.executor.execute(name, dict(payload), action_id=tool_id)
            if not res.ok:
                failed = True
                results.append(self._error_result(tool_id, res.error))
                continue
            results.append(self._ok_result(tool_id, res))
        return results

    @staticmethod
    def _error_result(tool_id: str, message: str) -> dict[str, Any]:
        return {"type": "tool_result", "tool_use_id": tool_id, "toolset_name": TOOLSET_NAME,
                "is_error": True, "content": [{"type": "text", "text": message}]}

    @staticmethod
    def _ok_result(tool_id: str, res: Any) -> dict[str, Any]:
        if res.image_png:
            content: list[dict[str, Any]] = [{
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png",
                           "data": base64.standard_b64encode(res.image_png).decode("ascii")},
            }]
        else:
            content = [{"type": "text", "text": res.output or "OK"}]
        return {"type": "tool_result", "tool_use_id": tool_id,
                "toolset_name": TOOLSET_NAME, "content": content}

    # ------------------------------------------------------------------ budama

    def prune_images(self) -> int:
        """Eski `user` mesajlarındaki görüntüleri toplu buda; budanan sayısını döner.

        Cache kararlılığı için yalnızca fazlalık `prune_batch`'e ulaştığında budarız
        (her turda bir görüntü atmak prefix'i sürekli bozar).
        """
        keep, batch = self.config.keep_images, self.config.prune_batch
        slots = list(self._image_slots())
        excess = len(slots) - keep
        if excess < batch:
            return 0
        pruned = 0
        for msg_idx, block_idx, content_idx in slots[:excess]:
            msg = self.messages[msg_idx]
            block = msg["content"][block_idx]
            block_content = block["content"]
            block_content[content_idx] = {"type": "text", "text": "[screenshot pruned]"}
            pruned += 1
        return pruned

    def _image_slots(self) -> Iterable[tuple[int, int, int]]:
        """Geçmişteki görüntü bloklarının (mesaj, blok, içerik) indekslerini eskiden yeniye verir."""
        for m_i, msg in enumerate(self.messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for b_i, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                inner = block.get("content")
                if not isinstance(inner, list):
                    continue
                for c_i, item in enumerate(inner):
                    if isinstance(item, dict) and item.get("type") == "image":
                        yield (m_i, b_i, c_i)
