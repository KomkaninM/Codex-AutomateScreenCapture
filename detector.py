"""Reusable screen-image matching and login-page session detection."""

from enum import Enum
import logging
from pathlib import Path

from automation_errors import AutomationError

log = logging.getLogger(__name__)


class SessionState(Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    UNKNOWN = "unknown"


def image_visible(image_path: Path, *, confidence=0.9, grab=None) -> bool:
    """Return whether an image matches the primary screen; detection errors raise."""
    image_path = Path(image_path)
    if not image_path.is_file():
        raise AutomationError(f"Reference image was not found: {image_path}")
    try:
        from PIL import Image
        from capture import primary_screen
        import cv2
        import numpy as np

        screen = cv2.cvtColor(
            np.asarray((grab or primary_screen)().convert("RGB")),
            cv2.COLOR_RGB2GRAY,
        )
        with Image.open(image_path) as image:
            anchor = cv2.cvtColor(
                np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY
            )
        if anchor.shape[0] > screen.shape[0] or anchor.shape[1] > screen.shape[1]:
            return False
        if np.ptp(anchor) == 0:
            error = cv2.matchTemplate(screen, anchor, cv2.TM_SQDIFF_NORMED)
            return float(error.min()) <= (1 - confidence)
        scores = cv2.matchTemplate(screen, anchor, cv2.TM_CCOEFF_NORMED)
        return float(scores.max()) >= confidence
    except AutomationError:
        raise
    except Exception as exc:
        raise AutomationError(
            f"Could not read or match reference image: {image_path} ({type(exc).__name__})"
        ) from exc


class VisualDetector:
    def __init__(self, logged_out: Path, *, confidence=0.8, grab=None):
        self.logged_out = Path(logged_out)
        self.confidence = confidence
        self.grab = grab

    def state(self) -> SessionState:
        if not self.logged_out.is_file():
            raise AutomationError(
                "Configure assets/login_anchor.png (or assets/login-anchor.png) before desktop automation."
            )
        matched = image_visible(
            self.logged_out, confidence=self.confidence, grab=self.grab
        )
        if matched:
            return SessionState.LOGGED_OUT
        # Single-anchor mode assumes the visible BMS session is active when
        # its login-page control is absent. The desktop must stay on the BMS.
        return SessionState.LOGGED_IN


class SessionGuard:
    def __init__(self, detector, player):
        self.detector = detector
        self.player = player

    def ensure(self, default_macro, target_macro=None, *, deadline=None, cancel=None):
        state = self.detector.state()
        if state == SessionState.UNKNOWN:
            raise AutomationError(
                "BMS session state is unknown; check desktop and reference images."
            )
        if state == SessionState.LOGGED_IN:
            log.info("Login anchor not detected; skipping login macro.")
            return False
        name = target_macro or default_macro
        options = {
            k: v
            for k, v in {"deadline": deadline, "cancel": cancel}.items()
            if v is not None
        }
        log.info("Login anchor detected; running %s once.", name)
        self.player.play(name, **options)
        return True
