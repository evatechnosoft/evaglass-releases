"""ActionExecutorService — `computer_toolset_20260801`'in 17 üye aksiyonunu OS'e uygular.

pynput importları **tembel**: DISPLAY yokken modül seviyesinde import patlar, bu yüzden
her metot ihtiyaç anında import eder. Koordinatlar modelden ekran-görüntüsü piksel
uzayında gelir; gerçek ekrana `/scale` ile çevrilir (bkz. perception.scale_factor).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from .safety import SafetyError, SafetyGuard

#: `key`/`hold_key` metnindeki değiştirici tuşlar.
MODIFIERS: dict[str, str] = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt",
    "shift": "shift",
    "super": "cmd", "cmd": "cmd", "win": "cmd", "meta": "cmd",
}

#: X11 / Anthropic tuş adı → pynput Key adı.
KEY_ALIASES: dict[str, str] = {
    "return": "enter", "enter": "enter", "kp_enter": "enter",
    "tab": "tab", "escape": "esc", "esc": "esc",
    "backspace": "backspace", "delete": "delete", "insert": "insert",
    "space": "space",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end",
    "page_up": "page_up", "prior": "page_up",
    "page_down": "page_down", "next": "page_down",
    "caps_lock": "caps_lock", "num_lock": "num_lock",
    "print": "print_screen", "menu": "menu", "pause": "pause",
}

MAX_DURATION_S = 300.0


@dataclass(slots=True)
class ActionResult:
    """Executor çıktısı; orchestrator bunu `tool_result` bloğuna çevirir."""

    id: str
    action: str
    ok: bool
    latency_ms: float
    output: str = ""
    error: str = ""
    image_png: bytes | None = None
    risk: str = "low"


class ActionExecutorService:
    """SafetyGuard → pynput. Her aksiyon `action.requested` ile başlar."""

    def __init__(
        self,
        bus: Any,
        session: str,
        agent: str,
        *,
        guard: SafetyGuard,
        perception: Any | None = None,
        dry_run: bool = False,
    ) -> None:
        self.bus = bus
        self.session = session
        self.agent = agent
        self.guard = guard
        self.perception = perception
        self.dry_run = dry_run
        self._mouse: Any = None
        self._kbd: Any = None

    # ------------------------------------------------------------ pynput tembel

    def _mouse_ctl(self) -> Any:
        if self._mouse is None:
            from pynput.mouse import Controller  # tembel: DISPLAY gerekir
            self._mouse = Controller()
        return self._mouse

    def _kbd_ctl(self) -> Any:
        if self._kbd is None:
            from pynput.keyboard import Controller  # tembel
            self._kbd = Controller()
        return self._kbd

    @staticmethod
    def _button(name: str) -> Any:
        from pynput.mouse import Button
        return {"left": Button.left, "right": Button.right, "middle": Button.middle}[name]

    def _resolve_key(self, token: str) -> Any:
        """'ctrl' / 'Return' / 'f5' / 'a' → pynput Key veya karakter."""
        from pynput.keyboard import Key, KeyCode

        t = token.strip()
        low = t.lower()
        if low in MODIFIERS:
            return getattr(Key, MODIFIERS[low])
        if low in KEY_ALIASES:
            return getattr(Key, KEY_ALIASES[low])
        if len(low) > 1 and low[0] == "f" and low[1:].isdigit():
            return getattr(Key, f"f{int(low[1:])}")
        if hasattr(Key, low):
            return getattr(Key, low)
        if len(t) == 1:
            return KeyCode.from_char(t)
        raise ValueError(f"unknown key: {token!r}")

    @staticmethod
    def _tokens(text: str) -> list[str]:
        parts = [p.strip() for p in str(text).split("+") if p.strip()]
        if not parts:
            raise ValueError(f"empty key combination: {text!r}")
        return parts

    @staticmethod
    def _looks_like_key(token: str) -> bool:
        """pynput'a dokunmadan sözdizimsel geçerlilik (dry_run / DISPLAY yokken)."""
        low = token.lower()
        if low in MODIFIERS or low in KEY_ALIASES or len(token) == 1:
            return True
        return low[0] == "f" and low[1:].isdigit()

    def _parse_combo(self, text: str) -> list[Any]:
        """Kombinasyonu pynput tuşlarına çevirir; dry_run'da yalnızca doğrular."""
        tokens = self._tokens(text)
        if self.dry_run:
            for t in tokens:
                if not self._looks_like_key(t):
                    raise ValueError(f"unknown key: {t!r}")
            return []
        return [self._resolve_key(t) for t in tokens]

    # ------------------------------------------------------------- koordinatlar

    @property
    def scale(self) -> float:
        """Son ekran görüntüsünün ölçeği (perception yoksa 1.0)."""
        return getattr(self.perception, "scale", 1.0) or 1.0

    def to_screen(self, coordinate: Any) -> tuple[int, int]:
        """Ekran-görüntüsü koordinatını gerçek ekran koordinatına çevirir."""
        x, y = coordinate
        s = self.scale
        return int(round(float(x) / s)), int(round(float(y) / s))

    def _move(self, coordinate: Any | None) -> None:
        if coordinate is None:
            return
        if self.dry_run:
            return
        self._mouse_ctl().position = self.to_screen(coordinate)

    # ------------------------------------------------------------------ dispatch

    def execute(self, action: str, payload: dict[str, Any] | None = None,
                *, action_id: str | None = None) -> ActionResult:
        """Tek aksiyon: SafetyGuard → yürüt → `action.executed` / `action.failed`."""
        payload = dict(payload or {})
        aid = action_id or "ac_" + secrets.token_hex(4)
        self.bus.emit(self.session, self.agent, "action.requested",
                      {"id": aid, "action": action, "input": payload})
        started = time.perf_counter()
        risk = "low"
        try:
            risk = self.guard.check(self.session, self.agent, action, payload)
            handler = self._HANDLERS.get(action)
            if handler is None:
                raise ValueError(f"unsupported action: {action}")
            output, image = handler(self, payload)
        except Exception as exc:  # SafetyError dahil: modele hata olarak döner
            latency = (time.perf_counter() - started) * 1000.0
            err = f"{type(exc).__name__}: {exc}"
            self.bus.emit(self.session, self.agent, "action.failed",
                          {"id": aid, "action": action, "error": err, "latency_ms": round(latency, 2)})
            return ActionResult(id=aid, action=action, ok=False, latency_ms=latency,
                                error=err, risk=risk if isinstance(risk, str) else "low")
        latency = (time.perf_counter() - started) * 1000.0
        self.bus.emit(self.session, self.agent, "action.executed",
                      {"id": aid, "action": action, "ok": True,
                       "latency_ms": round(latency, 2), "output": output[:400]})
        return ActionResult(id=aid, action=action, ok=True, latency_ms=latency,
                            output=output, image_png=image, risk=risk)

    # ------------------------------------------------------------- üye aksiyonlar

    def _a_screenshot(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        if self.perception is None:
            raise RuntimeError("no perception service attached")
        shot = self.perception.capture()
        return f"screenshot {shot.width}x{shot.height} changed={shot.changed}", shot.png

    def _a_zoom(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        if self.perception is None:
            raise RuntimeError("no perception service attached")
        region = p.get("region")
        if not region:
            raise ValueError("zoom requires region")
        shot = self.perception.zoom(region)
        return f"zoom {list(region)} -> {shot.width}x{shot.height}", shot.png

    def _click(self, p: dict[str, Any], button: str, count: int) -> tuple[str, bytes | None]:
        self._move(p.get("coordinate"))
        mods = self._parse_combo(p["text"]) if p.get("text") else []
        if not self.dry_run:
            kbd, mouse = self._kbd_ctl(), self._mouse_ctl()
            for m in mods:
                kbd.press(m)
            try:
                mouse.click(self._button(button), count)
            finally:
                for m in reversed(mods):
                    kbd.release(m)
        where = p.get("coordinate") or "current"
        return f"{button}_click x{count} at {where}", None

    def _a_left_click(self, p): return self._click(p, "left", 1)
    def _a_right_click(self, p): return self._click(p, "right", 1)
    def _a_middle_click(self, p): return self._click(p, "middle", 1)
    def _a_double_click(self, p): return self._click(p, "left", 2)
    def _a_triple_click(self, p): return self._click(p, "left", 3)

    def _a_left_click_drag(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        start = p.get("start_coordinate")
        end = p.get("coordinate")
        if start is None or end is None:
            raise ValueError("left_click_drag requires start_coordinate and coordinate")
        if not self.dry_run:
            from pynput.mouse import Button
            mouse = self._mouse_ctl()
            mouse.position = self.to_screen(start)
            mouse.press(Button.left)
            mouse.position = self.to_screen(end)
            mouse.release(Button.left)
        return f"drag {list(start)} -> {list(end)}", None

    def _a_mouse_move(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        if p.get("coordinate") is None:
            raise ValueError("mouse_move requires coordinate")
        self._move(p["coordinate"])
        return f"move -> {list(p['coordinate'])}", None

    def _a_left_mouse_down(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        self._move(p.get("coordinate"))
        if not self.dry_run:
            from pynput.mouse import Button
            self._mouse_ctl().press(Button.left)
        return "left_mouse_down", None

    def _a_left_mouse_up(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        if not self.dry_run:
            from pynput.mouse import Button
            self._mouse_ctl().release(Button.left)
        return "left_mouse_up", None

    def _a_cursor_position(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        if self.dry_run:
            return "cursor_position [0, 0]", None
        x, y = self._mouse_ctl().position
        s = self.scale
        return f"cursor_position [{int(x * s)}, {int(y * s)}]", None

    def _a_scroll(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        direction = str(p.get("scroll_direction", "down")).lower()
        amount = int(p.get("scroll_amount", 3))
        if direction not in ("up", "down", "left", "right"):
            raise ValueError(f"bad scroll_direction: {direction}")
        self._move(p.get("coordinate"))
        dx, dy = {"up": (0, amount), "down": (0, -amount),
                  "left": (-amount, 0), "right": (amount, 0)}[direction]
        mods = self._parse_combo(p["text"]) if p.get("text") else []
        if not self.dry_run:
            kbd, mouse = self._kbd_ctl(), self._mouse_ctl()
            for m in mods:
                kbd.press(m)
            try:
                mouse.scroll(dx, dy)
            finally:
                for m in reversed(mods):
                    kbd.release(m)
        return f"scroll {direction} x{amount}", None

    def _a_type(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        text = str(p.get("text", ""))
        if not text:
            raise ValueError("type requires text")
        if not self.dry_run:
            self._kbd_ctl().type(text)
        return f"typed {len(text)} chars", None

    def _a_key(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        text = str(p.get("text", ""))
        if not text:
            raise ValueError("key requires text")
        repeat = max(1, min(100, int(p.get("repeat", 1))))
        keys = self._parse_combo(text)
        if keys:
            kbd = self._kbd_ctl()
            for _ in range(repeat):
                for k in keys[:-1]:
                    kbd.press(k)
                try:
                    kbd.press(keys[-1])
                    kbd.release(keys[-1])
                finally:
                    for k in reversed(keys[:-1]):
                        kbd.release(k)
        return f"key {text} x{repeat}", None

    def _a_hold_key(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        text = str(p.get("text", ""))
        duration = min(MAX_DURATION_S, float(p.get("duration", 1.0)))
        if not text:
            raise ValueError("hold_key requires text")
        keys = self._parse_combo(text)
        if keys:
            kbd = self._kbd_ctl()
            for k in keys:
                kbd.press(k)
            try:
                time.sleep(duration)
            finally:
                for k in reversed(keys):
                    kbd.release(k)
        return f"hold {text} for {duration}s", None

    def _a_wait(self, p: dict[str, Any]) -> tuple[str, bytes | None]:
        duration = min(MAX_DURATION_S, float(p.get("duration", 1.0)))
        if not self.dry_run:
            time.sleep(duration)
        return f"waited {duration}s", None

    #: aksiyon adı → handler (17 üye)
    _HANDLERS: dict[str, Callable[["ActionExecutorService", dict[str, Any]], tuple[str, bytes | None]]] = {
        "screenshot": _a_screenshot,
        "zoom": _a_zoom,
        "left_click": _a_left_click,
        "right_click": _a_right_click,
        "middle_click": _a_middle_click,
        "double_click": _a_double_click,
        "triple_click": _a_triple_click,
        "left_click_drag": _a_left_click_drag,
        "mouse_move": _a_mouse_move,
        "left_mouse_down": _a_left_mouse_down,
        "left_mouse_up": _a_left_mouse_up,
        "cursor_position": _a_cursor_position,
        "scroll": _a_scroll,
        "type": _a_type,
        "key": _a_key,
        "hold_key": _a_hold_key,
        "wait": _a_wait,
    }


#: Toolset üyeleri — orchestrator ve testler bunu doğrulama için kullanır.
COMPUTER_ACTIONS: frozenset[str] = frozenset(ActionExecutorService._HANDLERS)
