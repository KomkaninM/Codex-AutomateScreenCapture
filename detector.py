"""Detect BMS session timeouts using the login-page anchor alone."""

from enum import Enum
import logging
import time
from pathlib import Path

from automation_errors import AutomationError, AutomationTimeoutError

log = logging.getLogger(__name__)


class SessionState(Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    UNKNOWN = "unknown"


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
        from PIL import Image
        from capture import primary_screen
        import cv2
        import numpy as np

        screen = cv2.cvtColor(
            np.asarray((self.grab or primary_screen)().convert("RGB")),
            cv2.COLOR_RGB2GRAY,
        )
        with Image.open(self.logged_out) as image:
            anchor = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
        if anchor.shape[0] > screen.shape[0] or anchor.shape[1] > screen.shape[1]:
            return SessionState.LOGGED_IN
        if np.ptp(anchor) == 0:
            # Correlation is undefined for a flat template; compare pixel error.
            error = cv2.matchTemplate(screen, anchor, cv2.TM_SQDIFF_NORMED)
            matched = float(error.min()) <= (1 - self.confidence)
        else:
            # Match PyAutoGUI/PyScreeze's OpenCV grayscale correlation method.
            scores = cv2.matchTemplate(screen, anchor, cv2.TM_CCOEFF_NORMED)
            matched = float(scores.max()) >= self.confidence
        if matched:
            return SessionState.LOGGED_OUT
        # Single-anchor mode assumes the visible BMS session is active when
        # its login-page control is absent. The desktop must stay on the BMS.
        return SessionState.LOGGED_IN


class SessionGuard:
    def __init__(
        self,
        detector,
        player,
        *,
        settle_delay=2,
        wait_seconds=30,
        sleep=time.sleep,
    ):
        self.detector = detector
        self.player = player
        self.settle_delay = settle_delay
        self.wait_seconds = wait_seconds
        self.sleep = sleep

    def _pause(self, seconds, deadline, cancel):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Job cancelled.")
        if deadline is not None and time.monotonic() + seconds >= deadline:
            raise AutomationTimeoutError(
                "Login readiness wait would exceed the LINE reply deadline."
            )
        if cancel is not None:
            if cancel.wait(seconds):
                raise RuntimeError("Job cancelled.")
        else:
            self.sleep(seconds)

    def _wait_for_anchor_loss(self, *, deadline=None, cancel=None):
        stop_at = time.monotonic() + self.wait_seconds
        if deadline is not None:
            # Leave time for the configured settle delay and for capture to begin.
            stop_at = min(stop_at, deadline - self.settle_delay - 1)
        while True:
            state = self.detector.state()
            if state == SessionState.LOGGED_IN:
                log.info("Login anchor disappeared; continuing automation.")
                return True
            if state == SessionState.UNKNOWN:
                raise AutomationError(
                    "BMS session state became unknown after the login macro."
                )
            remaining = stop_at - time.monotonic()
            if remaining <= 0:
                log.warning(
                    "Login anchor is still visible when the readiness wait ended; continuing capture by configured fallback."
                )
                return False
            self._pause(min(0.5, remaining), deadline, cancel)

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
        self._wait_for_anchor_loss(deadline=deadline, cancel=cancel)
        return True
