"""SafetyGuard + ApprovalBroker — fail-closed güvenlik katmanı.

Sıra: kill switch → allowlist → hız limiti → risk sınıflandırma → (gerekirse) insan onayı.
Sınıflandırma deterministik kural motorudur; LLM'e sorulmaz (plan §4).
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

Risk = Literal["low", "medium", "high", "financial"]

RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "financial": 3}

#: Sistemi kilitleyen / oturumu kapatan tuş kombinasyonları.
DANGEROUS_KEYS: frozenset[str] = frozenset({"alt+f4", "ctrl+alt+delete", "cmd+q", "super+q"})

#: Kart numarası (13-19 hane, boşluk/tire ayraçlı) veya ödeme anahtar kelimeleri.
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
PAYMENT_RE = re.compile(
    r"\b(cvv|cvc|card\s*number|kart\s*numaras[ıi]|iban|sort\s*code|routing\s*number|"
    r"expiry|son\s*kullanma|credit\s*card|kredi\s*kart)\b",
    re.IGNORECASE,
)

#: Yıkıcı kabuk komutları (yazılan metin içinde kelime sınırıyla aranır).
HIGH_RISK_TEXT: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"rm\s+-[a-z]*r[a-z]*f|rm\s+-[a-z]*f[a-z]*r"), "recursive delete"),
    (re.compile(r"git\s+push\s+(--force|-f)\b"), "force push"),
    (re.compile(r"\bsudo\b"), "privilege escalation"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "filesystem format"),
    (re.compile(r"\bshutdown\b|\breboot\b"), "system shutdown"),
    (re.compile(r"\bdrop\s+table\b"), "destructive sql"),
)


class SafetyError(RuntimeError):
    """Aksiyon güvenlik katmanında durduruldu."""

    def __init__(self, message: str, *, risk: Risk = "high") -> None:
        super().__init__(message)
        self.risk: Risk = risk


class Aborted(SafetyError):
    """Kill switch tetiklendi."""


@dataclass(slots=True)
class SafetyConfig:
    """Politika ayarları; boş allowlist = kısıtlama yok (yalıtılmış konteyner varsayımı)."""

    allowlist: tuple[str, ...] = ()
    max_actions_per_sec: float = 12.0
    approval_threshold: Risk = "medium"
    auto_approve: bool = False
    approval_timeout_s: float = 120.0


def classify(action: str, payload: dict[str, Any] | None = None) -> tuple[Risk, str]:
    """(risk, gerekçe) döner. Bilinmeyen her şey `low`; kural motoru deterministiktir."""
    payload = payload or {}
    text = str(payload.get("text") or "")
    low = text.lower()

    if action in ("key", "hold_key"):
        combo = low.replace(" ", "")
        for bad in DANGEROUS_KEYS:
            if bad in combo:
                return "high", f"dangerous key combo: {bad}"

    if action == "type" and text:
        if PAYMENT_RE.search(text) or CARD_RE.search(text):
            return "high", "payment/card data in typed text"
        for pattern, why in HIGH_RISK_TEXT:
            if pattern.search(low):
                return "high", why
        if "git push" in low:
            return "medium", "remote push"

    if action in ("left_click_drag",) and payload.get("text"):
        combo = str(payload["text"]).lower().replace(" ", "")
        if combo in DANGEROUS_KEYS:
            return "high", f"dangerous modifier: {combo}"

    return "low", "no rule matched"


def needs_approval(risk: Risk, threshold: Risk) -> bool:
    """Risk eşiğe eşit veya üstündeyse insan onayı gerekir."""
    return RISK_ORDER[risk] >= RISK_ORDER[threshold]


@dataclass(slots=True)
class _Pending:
    """Bekleyen onay: oturumu da tutar ki `resolve()` çağıranın bilmesine gerek kalmasın."""

    event: threading.Event
    session: str
    agent: str
    approved: bool = False
    by: str = ""


class ApprovalBroker:
    """`approval.requested` yayınlar, insan (veya auto mod) `resolve()` çağırana kadar bloke olur."""

    def __init__(self, bus: Any, *, auto_approve: bool = False, timeout_s: float = 120.0) -> None:
        self.bus = bus
        self.auto_approve = auto_approve
        self.timeout_s = timeout_s
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def pending_ids(self) -> list[str]:
        with self._lock:
            return list(self._pending)

    def request(
        self,
        session: str,
        agent: str,
        action: str,
        risk: Risk,
        description: str,
        *,
        timeout_s: float | None = None,
    ) -> bool:
        """Onay ister; True=onaylandı. Zaman aşımı fail-closed (False)."""
        approval_id = "ap_" + secrets.token_hex(4)
        pend = _Pending(event=threading.Event(), session=session, agent=agent)
        with self._lock:
            self._pending[approval_id] = pend
        self.bus.emit(session, agent, "approval.requested", {
            "id": approval_id, "risk": risk, "description": description, "action": action,
        })
        if self.auto_approve:
            self.resolve(approval_id, True, by="auto", session=session, agent=agent)
        timeout = self.timeout_s if timeout_s is None else timeout_s
        got = pend.event.wait(timeout)
        with self._lock:
            self._pending.pop(approval_id, None)
        if not got:
            self.bus.emit(session, agent, "approval.resolved",
                          {"id": approval_id, "approved": False, "by": "timeout"})
            return False
        return pend.approved

    def resolve(
        self,
        approval_id: str,
        approved: bool,
        by: str = "human",
        *,
        session: str | None = None,
        agent: str | None = None,
    ) -> bool:
        """Bekleyen onayı çözer ve `approval.resolved` yayınlar. Bilinmeyen id → False.

        `session`/`agent` isteğe bağlıdır: verilmezse `request()` sırasındaki oturum
        kullanılır, böylece gateway/UI çağrısı her hâlükârda olay yayınlar.
        """
        with self._lock:
            pend = self._pending.get(approval_id)
        if pend is None:
            return False
        pend.approved = approved
        pend.by = by
        self.bus.emit(session or pend.session, agent or pend.agent, "approval.resolved",
                      {"id": approval_id, "approved": approved, "by": by})
        pend.event.set()
        return True


class SafetyGuard:
    """Her aksiyonun geçtiği kapı. `check()` ya sessizce geçer ya `SafetyError` fırlatır."""

    def __init__(
        self,
        bus: Any,
        *,
        config: SafetyConfig | None = None,
        approvals: ApprovalBroker | None = None,
    ) -> None:
        self.bus = bus
        self.config = config or SafetyConfig()
        self.aborted = threading.Event()  # kill switch
        self.approvals = approvals or ApprovalBroker(
            bus, auto_approve=self.config.auto_approve, timeout_s=self.config.approval_timeout_s
        )
        self._active_window: str = ""
        self._recent: list[float] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ durum

    def abort(self) -> None:
        """Kill switch: bundan sonraki tüm aksiyonlar reddedilir."""
        self.aborted.set()

    def reset(self) -> None:
        self.aborted.clear()
        with self._lock:
            self._recent.clear()

    def set_active_window(self, name: str) -> None:
        """Allowlist kontrolü için aktif pencere/uygulama adını günceller."""
        self._active_window = name or ""

    # ----------------------------------------------------------------- kapılar

    def _check_allowlist(self) -> None:
        allow = self.config.allowlist
        if not allow:
            return  # yalıtılmış hedef: kısıt yok
        win = self._active_window.lower()
        if not any(a.lower() in win for a in allow):
            raise SafetyError(f"window not allowlisted: {self._active_window!r}")

    def _check_rate(self) -> None:
        limit = self.config.max_actions_per_sec
        if limit <= 0:
            return
        now = time.monotonic()
        with self._lock:
            self._recent = [t for t in self._recent if now - t < 1.0]
            if len(self._recent) >= limit:
                raise SafetyError(f"rate limit exceeded ({limit}/s)", risk="medium")
            self._recent.append(now)

    def check(self, session: str, agent: str, action: str, payload: dict[str, Any] | None = None) -> Risk:
        """Kill switch → allowlist → hız limiti → risk → onay. Riski döner."""
        if self.aborted.is_set():
            raise Aborted("kill switch active")
        self._check_allowlist()
        self._check_rate()
        risk, reason = classify(action, payload)
        if needs_approval(risk, self.config.approval_threshold):
            desc = f"{action}: {reason}"
            ok = self.approvals.request(session, agent, action, risk, desc)
            if not ok:
                raise SafetyError(f"approval denied for {action} ({reason})", risk=risk)
        if self.aborted.is_set():
            raise Aborted("kill switch active")
        return risk
