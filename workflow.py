"""All desktop transactions share this process-wide reentrant lock."""

import logging
import threading
import time
from pathlib import Path

from automation_errors import AutomationTimeoutError

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
            raise AutomationTimeoutError(
                "Interactive reply deadline expired before image delivery."
            )

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

    def capture(self, target=None, *, deadline=None, cancel=None, deliver=None):
        self._acquire(deadline, cancel)
        try:
            cfg = self.runtime.snapshot()
            macro = self.targets[target]["macro"] if target else None
            options = {
                k: v
                for k, v in {"deadline": deadline, "cancel": cancel}.items()
                if v is not None
            }
            log.info("Capture started; checking the login anchor once.")
            macro_played = self.guard.ensure(cfg.default_login_macro, macro, **options)
            if macro and not macro_played:
                self.player.play(macro, **options)
                macro_played = True
            self.check(deadline, cancel)
            if macro_played and cfg.settle_delay:
                stop_at = deadline or (time.monotonic() + cfg.settle_delay + 1)
                self.player._wait(cfg.settle_delay, stop_at, cancel)
            shot = self.capture_engine.capture(
                target or Path(cfg.default_login_macro).stem
            )
            if deliver is not None:
                self.check(deadline, cancel)
                # The delivery callback returns only after LINE accepts the request.
                # Holding UI_LOCK prevents another transaction from racing logout.
                deliver(shot)
                if cfg.auto_logout:
                    log.info("Image delivery confirmed; starting auto-logout.")
                    self.player.play(cfg.logout_macro)
                    if cfg.settle_delay:
                        time.sleep(cfg.settle_delay)
                    log.info("Auto-logout macro completed.")
            return shot
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
