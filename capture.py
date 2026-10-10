"""Framebuffer capture with private report JPEGs and full-resolution WebP delivery."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
from zoneinfo import ZoneInfo
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
    preview: Path
    root: Path
    captured_at: datetime

    @property
    def original(self):
        return self.webp

    def urls(self, base):
        from config import public_url

        base = public_url(base)
        return tuple(
            f"{base}/images/{p.relative_to(self.root).as_posix()}"
            for p in (self.original, self.preview)
        )


class CaptureEngine:
    def __init__(
        self,
        root: Path,
        *,
        grab=primary_screen,
        clock=None,
        timezone_name="Asia/Bangkok",
        webp_quality=90,
    ):
        self.root = Path(root).resolve()
        self.grab = grab
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timezone = ZoneInfo(timezone_name)
        if not 1 <= webp_quality <= 100:
            raise ValueError("WebP quality must be between 1 and 100.")
        self.webp_quality = webp_quality

    def capture(self, partition: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", partition):
            raise ValueError("Invalid screenshot partition.")
        folder = (self.root / partition).resolve()
        if folder.parent != self.root:
            raise ValueError("Screenshot directory escapes storage.")
        folder.mkdir(parents=True, exist_ok=True)
        image = self.grab().convert("RGB")
        captured_at = self.clock()
        stamp = captured_at.astimezone(self.timezone).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        stem = f"shot_{stamp}_{secrets.token_hex(16)}"
        paths = [
            folder / (stem + suffix)
            for suffix in (".jpg", "_line.webp", "_preview.webp")
        ]
        archive_saved = False
        try:
            image.save(paths[0], "JPEG", quality=100, subsampling=0, optimize=True)
            archive_saved = True
            image.save(paths[1], "WEBP", quality=self.webp_quality, method=6)
            preview = image.copy()
            preview.thumbnail((240, 240), Image.Resampling.LANCZOS)
            preview.save(paths[2], "WEBP", quality=80, method=6)
            if (
                paths[1].stat().st_size > 10_000_000
                or paths[2].stat().st_size > 1_000_000
            ):
                raise AutomationError("LINE image size limit exceeded.")
        except BaseException:
            for path in paths:
                if archive_saved and path == paths[0]:
                    continue
                path.unlink(missing_ok=True)
            raise
        return Screenshot(*paths, self.root, captured_at)
