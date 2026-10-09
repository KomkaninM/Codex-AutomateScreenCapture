"""Validated desktop primitives; clipboard pasting avoids slow character typing."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from config import macro_name


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
    ):
        self.directory = Path(directory)
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
            if action not in ("click", "text", "hotkey", "press", "sleep"):
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
        stop_at = min(deadline or float("inf"), time.monotonic() + self.max_seconds)
        for step in steps:
            self._check(stop_at, cancel)
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
        self._check(stop_at, cancel)

    @staticmethod
    def _check(deadline, cancel):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Job cancelled.")
        if time.monotonic() >= deadline:
            raise TimeoutError("Macro deadline exceeded.")

    def _wait(self, seconds, deadline, cancel):
        self._check(deadline, cancel)
        if time.monotonic() + seconds >= deadline:
            raise TimeoutError("Insufficient time for macro delay.")
        if cancel is not None:
            if cancel.wait(seconds):
                raise RuntimeError("Job cancelled.")
        else:
            self.sleep(seconds)
