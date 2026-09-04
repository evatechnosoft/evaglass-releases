"""ScreenPerceptionService — ekranı yakala, ölçekle, hash'le, olay yayınla.

Anthropic `computer_toolset_20260801` kuralı: koordinat uzayı, modele gönderdiğimiz
ekran görüntüsünün piksel uzayıdır. Bu yüzden ölçek faktörünü burada hesaplayıp
Executor'a veriyoruz (o da modelin koordinatını `/scale` ile gerçek ekrana çevirir).
"""
from __future__ import annotations

import hashlib
import io
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Anthropic görüntü limitleri (eski nesil güvenli değerler) + ürün kararı: en fazla 1366 px genişlik.
MAX_LONG_EDGE = 1568
MAX_TOTAL_PIXELS = 1_150_000
MAX_WIDTH = 1366

# Thumbnail: UI'ya gidecek küçük önizleme (≤40 KB hedefi).
THUMB_MAX_WIDTH = 320
THUMB_COLORS = 64


def scale_factor(width: int, height: int) -> float:
    """Anthropic ölçek kuralı + genişlik tavanı. Asla 1.0'ın üstüne çıkmaz."""
    if width <= 0 or height <= 0:
        raise ValueError("width/height must be positive")
    long_edge = max(width, height)
    total = width * height
    s = min(
        1.0,
        MAX_LONG_EDGE / long_edge,
        math.sqrt(MAX_TOTAL_PIXELS / total),
        MAX_WIDTH / width,
    )
    return s


def scaled_size(width: int, height: int) -> tuple[int, int, float]:
    """(yeni_genişlik, yeni_yükseklik, ölçek) döner."""
    s = scale_factor(width, height)
    return max(1, int(width * s)), max(1, int(height * s)), s


@dataclass(slots=True, frozen=True)
class Screenshot:
    """Modele gönderilecek ekran görüntüsü paketi."""

    png: bytes
    width: int
    height: int
    scale: float
    hash: str
    changed: bool
    thumb_url: str | None = None

    def as_event_data(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "hash": self.hash,
            "width": self.width,
            "height": self.height,
            "scale": round(self.scale, 6),
            "changed": self.changed,
        }
        if self.thumb_url:
            d["thumb_url"] = self.thumb_url
        return d


class ScreenPerceptionService:
    """mss ile yakalar, Pillow ile küçültür, PNG + hash + `changed` döner."""

    def __init__(
        self,
        bus: Any,
        session: str,
        agent: str,
        *,
        thumb_dir: Path | str | None = None,
        monitor: int = 1,
    ) -> None:
        self.bus = bus
        self.session = session
        self.agent = agent
        self.monitor = monitor
        self.thumb_dir = Path(thumb_dir) if thumb_dir else None
        self._last_hash: str | None = None
        self._scale: float = 1.0
        self._shot_seq: int = 0
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- yardımcı

    @property
    def scale(self) -> float:
        """Son yakalamanın ölçek faktörü (Executor koordinatı buna böler)."""
        return self._scale

    def _grab(self):  # pragma: no cover - gerçek ekran gerektirir
        """Ham ekranı PIL.Image olarak döner."""
        import mss  # tembel import: DISPLAY yoksa modül yüklenmesi patlamasın
        from PIL import Image

        factory = getattr(mss, "MSS", None) or mss.mss  # mss>=10 MSS, eski sürümlerde mss()
        with factory() as sct:
            mon = sct.monitors[self.monitor]
            raw = sct.grab(mon)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    @staticmethod
    def _to_png(img) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def _write_thumb(self, img, seq: int) -> str | None:
        """Küçük PNG önizleme yazar; `/thumbs/{session}/{seq}.png` URL'i döner."""
        if self.thumb_dir is None:
            return None
        from PIL import Image

        out_dir = self.thumb_dir / self.session
        out_dir.mkdir(parents=True, exist_ok=True)
        thumb = img.copy()
        if thumb.width > THUMB_MAX_WIDTH:
            ratio = THUMB_MAX_WIDTH / thumb.width
            thumb = thumb.resize((THUMB_MAX_WIDTH, max(1, int(thumb.height * ratio))))
        thumb = thumb.convert("P", palette=Image.Palette.ADAPTIVE, colors=THUMB_COLORS)
        thumb.save(out_dir / f"{seq}.png", format="PNG", optimize=True)
        return f"/thumbs/{self.session}/{seq}.png"

    # ------------------------------------------------------------------ public

    def capture(self, *, emit: bool = True) -> Screenshot:
        """Tam ekranı yakala, ölçekle, hash'le ve `perception.screenshot` yayınla."""
        img = self._grab()
        return self._package(img, emit=emit)

    def _package(self, img, *, emit: bool) -> Screenshot:
        w, h, s = scaled_size(img.width, img.height)
        if s < 1.0:
            img = img.resize((w, h))
        png = self._to_png(img)
        digest = hashlib.sha1(png).hexdigest()
        with self._lock:
            changed = digest != self._last_hash
            self._last_hash = digest
            self._scale = s
            seq = self._shot_seq
            self._shot_seq += 1
        thumb_url = self._write_thumb(img, seq)
        shot = Screenshot(png=png, width=w, height=h, scale=s, hash=digest,
                          changed=changed, thumb_url=thumb_url)
        if emit and self.bus is not None:
            self.bus.emit(self.session, self.agent, "perception.screenshot", shot.as_event_data())
        return shot

    def zoom(self, region: list[int] | tuple[int, int, int, int], *, emit: bool = True) -> Screenshot:
        """Ekran görüntüsü koordinatındaki bölgeyi ekran koordinatına çevirip tam çözünürlük kırpar."""
        x0, y0, x1, y1 = (int(v) for v in region)
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"invalid zoom region: {region!r}")
        img = self._grab()
        s = self._scale if self._scale > 0 else 1.0
        box = (
            max(0, int(x0 / s)),
            max(0, int(y0 / s)),
            min(img.width, int(math.ceil(x1 / s))),
            min(img.height, int(math.ceil(y1 / s))),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError(f"zoom region outside screen: {region!r}")
        crop = img.crop(box)
        # Tam çözünürlük; yalnızca model limitini aşarsa küçült.
        cs = scale_factor(crop.width, crop.height)
        if cs < 1.0:
            crop = crop.resize((max(1, int(crop.width * cs)), max(1, int(crop.height * cs))))
        png = self._to_png(crop)
        digest = hashlib.sha1(png).hexdigest()
        with self._lock:
            seq = self._shot_seq
            self._shot_seq += 1
        thumb_url = self._write_thumb(crop, seq)
        shot = Screenshot(png=png, width=crop.width, height=crop.height, scale=cs,
                          hash=digest, changed=True, thumb_url=thumb_url)
        if emit and self.bus is not None:
            data = shot.as_event_data()
            data["region"] = [x0, y0, x1, y1]
            self.bus.emit(self.session, self.agent, "perception.zoom", data)
        return shot
