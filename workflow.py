"""All desktop transactions share this process-wide reentrant lock."""

import logging
import threading
import time
from pathlib import Path

from automation_errors import AutomationError

UI_LOCK = threading.RLock()
log = logging.getLogger(__name__)


class Workflow:
    def __init__(self, runtime, player, guard, capture_engine, targets=None):
        self.runtime = runtime
        self.player = player
        self.guard = guard
        self.capture_engine = capture_engine
        self.targets = targets or {}

    @staticmethod
    def check(deadline=None, cancel=None):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Job cancelled.")
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Interactive reply deadline expired.")

    def _acquire(self, deadline=None, cancel=None):
        while True:
            self.check(deadline, cancel)
            if UI_LOCK.acquire(timeout=0.1):
                try:
                    self.check(deadline, cancel)
                except BaseException:
                    UI_LOCK.release()
                    raise
                return

    def prepare(self, target=None, *, deadline=None, cancel=None):
        self._acquire(deadline, cancel)
        try:
            cfg = self.runtime.snapshot()
            macro = self.targets[target]["macro"] if target else None
            options = {
                k: v
                for k, v in {"deadline": deadline, "cancel": cancel}.items()
                if v is not None
            }
            return self.guard.ensure(cfg.default_login_macro, macro, **options)
        finally:
            UI_LOCK.release()

    def capture(self, target=None, *, deadline=None, cancel=None):
        self._acquire(deadline, cancel)
        try:
            cfg = self.runtime.snapshot()
            macro = self.targets[target]["macro"] if target else None
            options = {
                k: v
                for k, v in {"deadline": deadline, "cancel": cancel}.items()
                if v is not None
            }
            session_validated = False
            try:
                from detector import SessionState

                for attempt in range(2):
                    used_target = self.guard.ensure(
                        cfg.default_login_macro, macro, **options
                    )
                    session_validated = True
                    if macro and not used_target:
                        self.player.play(macro, **options)
                    self.check(deadline, cancel)
                    if cfg.settle_delay:
                        stop_at = deadline or (time.monotonic() + cfg.settle_delay + 1)
                        self.player._wait(cfg.settle_delay, stop_at, cancel)
                    if not hasattr(self.guard, "detector"):
                        break
                    state = self.guard.detector.state()
                    if state == SessionState.LOGGED_IN:
                        break
                    session_validated = False
                    if state == SessionState.LOGGED_OUT and attempt == 0:
                        continue
                    raise AutomationError(
                        "Session was lost during navigation or became unknown."
                    )
                return self.capture_engine.capture(
                    target or Path(cfg.default_login_macro).stem
                )
            finally:
                if cfg.auto_logout and session_validated:
                    # Cleanup is a UI transaction even after cancellation/deadline expiry.
                    self.player.play(cfg.logout_macro)
                    if cfg.settle_delay:
                        time.sleep(cfg.settle_delay)
                    if hasattr(self.guard, "verify_logout"):
                        self.guard.verify_logout()
        finally:
            UI_LOCK.release()

    def macro(self, name, *, deadline=None, cancel=None):
        self._acquire(deadline, cancel)
        try:
            cfg = self.runtime.snapshot()
            self.guard.ensure(cfg.default_login_macro, deadline=deadline, cancel=cancel)
            self.player.play(name, deadline=deadline, cancel=cancel)
        finally:
            UI_LOCK.release()
