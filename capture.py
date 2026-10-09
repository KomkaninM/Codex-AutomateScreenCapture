"""Framebuffer capture and partitioned archival, WebP, and LINE JPEG storage."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import secrets
from PIL import Image

from automation_errors import AutomationError
from macro_player import enable_dpi_awareness


def primary_screen():
    enable_dpi_awareness()
    import mss

    with mss.mss() as display:
        monitors = display.monitors[1:]
        if not monitors:
            raise AutomationError("No active desktop monitor is available.")
        primary = next(
            (m for m in monitors if m["left"] == 0 and m["top"] == 0), monitors[0]
        )
        shot = display.grab(primary)
        return Image.frombytes("RGB", shot.size, shot.rgb)


@dataclass(frozen=True)
class Screenshot:
    archive: Path
    webp: Path
    original: Path
    preview: Path
    root: Path

    def urls(self, base):
        from config import public_url

        base = public_url(base)
        return tuple(
            f"{base}/images/{p.relative_to(self.root).as_posix()}"
            for p in (self.original, self.preview)
        )


class CaptureEngine:
    def __init__(self, root: Path, *, grab=primary_screen):
        self.root = Path(root).resolve()
        self.grab = grab

    def capture(self, partition: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", partition):
            raise ValueError("Invalid screenshot partition.")
        folder = (self.root / partition).resolve()
        if folder.parent != self.root:
            raise ValueError("Screenshot directory escapes storage.")
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        stem = f"shot_{stamp}_{secrets.token_hex(16)}"
        paths = [
            folder / (stem + suffix)
            for suffix in (".jpg", "_line.webp", "_line.jpg", "_preview.jpg")
        ]
        image = self.grab().convert("RGB")
        try:
            image.save(paths[0], "JPEG", quality=100, subsampling=0, optimize=True)
            image.save(paths[1], "WEBP", quality=82, method=6)
            delivery = image.copy()
            delivery.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
            delivery.save(paths[2], "JPEG", quality=85, optimize=True)
            preview = image.copy()
            preview.thumbnail((240, 240), Image.Resampling.LANCZOS)
            preview.save(paths[3], "JPEG", quality=70, optimize=True)
            if (
                paths[2].stat().st_size > 10_000_000
                or paths[3].stat().st_size > 1_000_000
            ):
                raise AutomationError("LINE image size limit exceeded.")
        except BaseException:
            for path in paths:
                path.unlink(missing_ok=True)
            raise
        return Screenshot(*paths, self.root)
