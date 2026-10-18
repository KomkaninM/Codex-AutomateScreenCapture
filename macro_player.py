"""Validated desktop primitives; clipboard pasting avoids slow character typing."""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

from automation_errors import AutomationError, AutomationTimeoutError
from config import macro_name, resolve_project_resource

log = logging.getLogger(__name__)


def enable_dpi_awareness():
    import sys

    if sys.platform == "win32":
        import ctypes

        # Per-monitor V2 keeps input coordinates and framebuffer pixels aligned.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))


class MacroPlayer:
    def __init__(
        self,
        directory: Path,
        *,
        gui=None,
        clipboard=None,
        sleep=time.sleep,
        max_seconds=35,
        project_dir=None,
    ):
        self.directory = Path(directory)
        resolved = self.directory.resolve()
        inferred_project = (
            resolved.parent.parent
            if resolved.name == "macros" and resolved.parent.name == "scripts"
            else resolved.parent
        )
        self.project_dir = Path(project_dir or inferred_project).resolve()
        self._gui = gui
        self._clipboard = clipboard
        self.sleep = sleep
        self.max_seconds = max_seconds

    @property
    def gui(self):
        if self._gui is None:
            enable_dpi_awareness()
            import pyautogui

            pyautogui.FAILSAFE = True
            self._gui = pyautogui
        return self._gui

    @property
    def clipboard(self):
        if self._clipboard is None:
            import pyperclip

            self._clipboard = pyperclip
        return self._clipboard

    def _read(self, name: str):
        path = (self.directory / macro_name(name)).resolve()
        if path.parent != self.directory.resolve():
            raise ValueError("Macro path escapes the macro directory.")
        if path.stat().st_size > 1_000_000:
            raise ValueError("Macro exceeds 1 MB.")
        return json.loads(path.read_text(encoding="utf-8"))

    def load(self, name: str):
        return self._validate(self._read(name))

    def validate(self, data):
        """Validate an in-memory macro without loading desktop input libraries."""
        return self._validate(data)

    def _image_path(self, value):
        try:
            path = resolve_project_resource(self.project_dir, value)
        except ValueError as exc:
            raise AutomationError(f"Invalid wait_image path: {exc}") from None
        if not path.is_file():
            raise AutomationError(f"wait_image file was not found: {value}")
        try:
            from PIL import Image

            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            raise AutomationError(
                f"wait_image file is not a readable image: {value} ({type(exc).__name__})"
            ) from None
        return path

    def _validate(self, data):
        if isinstance(data, dict) and "desktop" in data:
            desktop = data["desktop"]
            if not isinstance(desktop, dict) or any(
                type(desktop.get(k)) is not int or desktop[k] <= 0
                for k in ("width", "height")
            ):
                raise ValueError(
                    "Macro desktop metadata needs positive integer width and height."
                )
        steps = data.get("steps") if isinstance(data, dict) else data
        if not isinstance(steps, list) or not 1 <= len(steps) <= 1000:
            raise ValueError("Macro must contain 1–1000 steps.")
        duration = 0.0
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("Every macro step must be an object.")
            action = step.get("action", step.get("type"))
            if action not in (
                "click",
                "text",
                "hotkey",
                "press",
                "sleep",
                "wait_image",
            ):
                raise ValueError("Unsupported macro action.")
            delay = step.get("delay", 0)
            seconds = step.get("seconds", 0) if action == "sleep" else 0
            for number in (delay, seconds):
                if (
                    not isinstance(number, (int, float))
                    or not math.isfinite(number)
                    or not 0 <= number <= self.max_seconds
                ):
                    raise ValueError("Invalid macro delay.")
            duration += delay + seconds + 0.15
            if action == "click":
                if any(type(step.get(k)) is not int or step[k] < 0 for k in ("x", "y")):
                    raise ValueError("Click coordinates must be nonnegative integers.")
                if (
                    type(step.get("clicks", 1)) is not int
                    or not 1 <= step.get("clicks", 1) <= 10
                ):
                    raise ValueError("Click count must be 1–10.")
                if step.get("button", "left") not in ("left", "right", "middle"):
                    raise ValueError("Invalid mouse button.")
            if action == "text" and (
                not isinstance(step.get("text"), str) or len(step["text"]) > 10000
            ):
                raise ValueError("Invalid text input.")
            if action == "hotkey" and (
                not isinstance(step.get("keys"), list)
                or not 1 <= len(step["keys"]) <= 8
                or any(not isinstance(k, str) or not k for k in step["keys"])
            ):
                raise ValueError("Hotkey requires a nonempty list of keys.")
            if action == "press" and (
                not isinstance(step.get("key"), str) or not step["key"]
            ):
                raise ValueError("Press requires a key.")
            if action == "wait_image":
                if step.get("on_timeout", "stop") not in ("stop", "continue"):
                    raise AutomationError("wait_image on_timeout must be stop or continue.")
                image = step.get("image")
                if not isinstance(image, str) or not image.strip():
                    raise AutomationError(
                        "wait_image requires a relative image path."
                    )
                if step.get("condition") not in ("appear", "disappear"):
                    raise AutomationError(
                        "wait_image condition must be appear or disappear."
                    )
                for key, default in (
                    ("timeout", 60),
                    ("poll_interval", 0.5),
                    ("confidence", 0.9),
                ):
                    value = step.get(key, default)
                    if (
                        type(value) not in (int, float)
                        or not math.isfinite(value)
                        or value <= 0
                    ):
                        raise AutomationError(
                            f"wait_image {key} must be positive."
                        )
                if step.get("confidence", 0.9) > 1:
                    raise AutomationError(
                        "wait_image confidence must be at most 1."
                    )
                confirmations = step.get("consecutive_matches", 2)
                if type(confirmations) is not int or confirmations <= 0:
                    raise AutomationError(
                        "wait_image consecutive_matches must be a positive integer."
                    )
                self._image_path(image)
        if duration > self.max_seconds:
            raise ValueError(
                "Macro exceeds MAX_MACRO_SECONDS; shorten it for reply-token validity."
            )
        return steps

    def play(self, name: str, *, deadline=None, cancel=None):
        data = self._read(name)
        steps = self._validate(data)
        gui = self.gui
        for step in steps:
            action = step.get("action", step.get("type"))
            keys = (
                step.get("keys", [])
                if action == "hotkey"
                else [step["key"]] if action == "press" else []
            )
            allowed = getattr(gui, "KEYBOARD_KEYS", None)
            if isinstance(allowed, (list, tuple)) and any(
                k not in allowed for k in keys
            ):
                raise ValueError("Unknown keyboard key.")
        started = time.monotonic()
        macro_deadline = started + self.max_seconds
        outer_deadline = deadline if deadline is not None else float("inf")
        stop_at = min(outer_deadline, macro_deadline)
        limit = (
            "LINE reply deadline"
            if deadline is not None and deadline <= macro_deadline
            else "MAX_MACRO_SECONDS"
        )
        remaining = stop_at - started
        required_waits = sum(
            step.get("delay", 0)
            + (
                step.get("seconds", 0)
                if step.get("action", step.get("type")) == "sleep"
                else 0
            )
            + (0.15 if step.get("action", step.get("type")) == "text" else 0)
            for step in steps
        )
        if remaining <= required_waits:
            raise AutomationTimeoutError(
                f"Macro stopped before input: recorded waits need {required_waits:.2f}s, "
                f"but {limit} leaves {max(0, remaining):.2f}s."
            )
        log.info(
            "Macro started: %d steps; recorded waits %.2fs; %s leaves %.2fs.",
            len(steps),
            required_waits,
            limit,
            remaining,
        )
        try:
            for index, step in enumerate(steps, 1):
                stop_at = min(outer_deadline, macro_deadline)
                self._check(stop_at, cancel)
                action = step.get("action", step.get("type"))
                if action == "wait_image":
                    wait_started = time.monotonic()
                    self._wait_image(step, outer_deadline, cancel)
                    macro_deadline += time.monotonic() - wait_started
                    self._wait(
                        step.get("delay", 0),
                        min(outer_deadline, macro_deadline),
                        cancel,
                    )
                else:
                    self._play_step(step, stop_at, cancel)
            self._check(min(outer_deadline, macro_deadline), cancel)
        except AutomationTimeoutError as exc:
            elapsed = time.monotonic() - started
            action = step.get("action", step.get("type"))
            raise AutomationTimeoutError(
                f"Macro timed out at step {index}/{len(steps)} ({action}); "
                f"elapsed {elapsed:.2f}s; {limit} allowed {max(0, remaining):.2f}s. {exc}"
            ) from None
        log.info("Macro completed in %.2fs.", time.monotonic() - started)

    def _wait_image(self, step, outer_deadline, cancel):
        from detector import image_visible

        relative = step["image"]
        path = self._image_path(relative)
        condition = step["condition"]
        timeout = step.get("timeout", 60)
        poll = step.get("poll_interval", 0.5)
        confidence = step.get("confidence", 0.9)
        required = step.get("consecutive_matches", 2)
        started = time.monotonic()
        own_deadline = started + timeout
        stop_at = min(own_deadline, outer_deadline)
        streak = 0
        checks = 0

        def raise_timeout(now):
            elapsed = now - started
            limiting = (
                "LINE reply deadline"
                if outer_deadline <= own_deadline
                else f"{timeout:g}s wait timeout"
            )
            message = (
                f"wait_image timed out waiting for {relative} to {condition}; "
                f"elapsed {elapsed:.2f}s; checks {checks}; {limiting} reached."
            )
            if (
                step.get("on_timeout", "stop") == "continue"
                and own_deadline < outer_deadline
                and now < outer_deadline
            ):
                log.warning(message + " Continuing as configured.")
                return
            log.error(message)
            raise AutomationTimeoutError(message)

        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Job cancelled.")
            now = time.monotonic()
            if now >= stop_at:
                return raise_timeout(now)
            try:
                visible = image_visible(path, confidence=confidence)
            except Exception as exc:
                log.error(
                    "wait_image detection failed for %s (%s): %s: %s",
                    relative,
                    condition,
                    type(exc).__name__,
                    exc,
                )
                raise AutomationError(
                    f"wait_image detection failed for {relative} ({condition}): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            checks += 1
            now = time.monotonic()
            if now >= stop_at:
                return raise_timeout(now)
            satisfied = visible if condition == "appear" else not visible
            streak = streak + 1 if satisfied else 0
            if streak >= required:
                log.info(
                    "wait_image satisfied for %s (%s) after %.2fs and %d checks.",
                    relative,
                    condition,
                    time.monotonic() - started,
                    checks,
                )
                return
            remaining = stop_at - time.monotonic()
            if remaining <= 0:
                continue
            pause = min(poll, remaining)
            if cancel is not None:
                if cancel.wait(pause):
                    raise RuntimeError("Job cancelled.")
            else:
                self.sleep(pause)

    def _play_step(self, step, stop_at, cancel):
        gui = self.gui
        action = step.get("action", step.get("type"))
        if action == "click":
            gui.click(
                x=step["x"],
                y=step["y"],
                clicks=step.get("clicks", 1),
                button=step.get("button", "left"),
                interval=0.05,
            )
        elif action == "text":
            previous = self.clipboard.paste()
            try:
                self.clipboard.copy(step["text"])
                gui.hotkey("ctrl", "v")
                self._wait(0.15, stop_at, cancel)
            finally:
                self.clipboard.copy(previous)
        elif action == "hotkey":
            gui.hotkey(*step["keys"])
        elif action == "press":
            gui.press(step["key"])
        elif action == "sleep":
            self._wait(step.get("seconds", 0), stop_at, cancel)
        self._wait(step.get("delay", 0), stop_at, cancel)

    @staticmethod
    def _check(deadline, cancel):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Job cancelled.")
        if time.monotonic() >= deadline:
            raise AutomationTimeoutError("Macro time limit expired.")

    def _wait(self, seconds, deadline, cancel):
        self._check(deadline, cancel)
        if time.monotonic() + seconds >= deadline:
            raise AutomationTimeoutError(
                f"Next delay needs {seconds:.2f}s; only {max(0, deadline - time.monotonic()):.2f}s remain."
            )
        if cancel is not None:
            if cancel.wait(seconds):
                raise RuntimeError("Job cancelled.")
        else:
            self.sleep(seconds)
