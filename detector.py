"""Conservative visual login guard; ambiguous screens never imply a valid session."""

from enum import Enum
import time
from pathlib import Path


class SessionState(Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    UNKNOWN = "unknown"


class VisualDetector:
    def __init__(self, logged_in: Path, logged_out: Path, *, confidence=0.8, grab=None):
        self.logged_in = Path(logged_in)
        self.logged_out = Path(logged_out)
        self.confidence = confidence
        self.grab = grab

    def state(self) -> SessionState:
        if not self.logged_in.is_file() or not self.logged_out.is_file():
            raise RuntimeError(
                "Configure assets/logout_anchor.png and assets/login_anchor.png before desktop automation."
            )
        from PIL import Image
        from capture import primary_screen
        import cv2
        import numpy as np

        screen = np.asarray((self.grab or primary_screen)().convert("RGB"))
        matches = []
        for path in (self.logged_in, self.logged_out):
            with Image.open(path) as image:
                anchor = np.asarray(image.convert("RGB"))
            if anchor.shape[0] > screen.shape[0] or anchor.shape[1] > screen.shape[1]:
                matches.append(False)
                continue
            # Normalized squared error works with uniform-color anchors too.
            error = cv2.matchTemplate(screen, anchor, cv2.TM_SQDIFF_NORMED)
            matches.append(float(error.min()) <= (1 - self.confidence))
        if matches == [True, False]:
            return SessionState.LOGGED_IN
        if matches == [False, True]:
            return SessionState.LOGGED_OUT
        return SessionState.UNKNOWN


class SessionGuard:
    def __init__(self, detector, player, *, settle_delay=2, wait_seconds=5):
        self.detector = detector
        self.player = player
        self.settle_delay = settle_delay
        self.wait_seconds = wait_seconds

    def ensure(self, default_macro, target_macro=None, *, deadline=None, cancel=None):
        state = self.detector.state()
        if state == SessionState.UNKNOWN:
            raise RuntimeError(
                "BMS session state is unknown; check desktop and reference images."
            )
        if state == SessionState.LOGGED_IN:
            return False
        name = target_macro or default_macro
        options = {
            k: v
            for k, v in {"deadline": deadline, "cancel": cancel}.items()
            if v is not None
        }
        self.player.play(name, **options)
        stop_at = min(
            deadline or float("inf"),
            time.monotonic() + self.wait_seconds + self.settle_delay,
        )
        if self.settle_delay:
            self.player._wait(self.settle_delay, stop_at, cancel)
        while True:
            if self.detector.state() == SessionState.LOGGED_IN:
                return bool(target_macro)
            if time.monotonic() >= stop_at:
                raise RuntimeError(
                    "Login macro completed but the logged-in reference was not detected."
                )
            self.player._wait(
                min(0.25, max(0.001, stop_at - time.monotonic() - 0.001)),
                stop_at,
                cancel,
            )

    def verify_logout(self):
        if self.detector.state() != SessionState.LOGGED_OUT:
            raise RuntimeError(
                "Logout macro completed but the logged-out reference was not detected."
            )
