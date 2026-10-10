"""Explicit Windows recording sessions exporting the bot's input primitives."""

from __future__ import annotations

import copy
import sys
import threading
import time
from datetime import datetime

MODIFIERS = {
    "ctrl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "alt_gr": "altgr",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "cmd": "winleft",
    "cmd_l": "winleft",
    "cmd_r": "winleft",
    "winleft": "winleft",
}
KEY_NAMES = {
    "caps_lock": "capslock",
    "num_lock": "numlock",
    "scroll_lock": "scrolllock",
    "page_up": "pageup",
    "page_down": "pagedown",
    "print_screen": "printscreen",
    "media_volume_up": "volumeup",
    "media_volume_down": "volumedown",
    "media_volume_mute": "volumemute",
    "media_play_pause": "playpause",
    "media_next": "nexttrack",
    "media_previous": "prevtrack",
}


def translate_key(key, modifiers=()):
    name = getattr(key, "name", None)
    if isinstance(name, str):
        return KEY_NAMES.get(name, name), " " if name == "space" else None
    char = getattr(key, "char", None)
    vk = getattr(key, "vk", None)
    if isinstance(char, str) and len(char) == 1:
        normalized = char.lower()
        if any(m in modifiers for m in ("ctrl", "alt", "winleft")) and isinstance(
            vk, int
        ):
            if 48 <= vk <= 57 or 65 <= vk <= 90:
                normalized = chr(vk).lower()
        return normalized, char
    raise ValueError(
        "This keyboard key cannot be recorded as a supported macro primitive."
    )


class RecordedMacro:
    def __init__(
        self,
        width,
        height,
        *,
        name="Recorded BMS Macro",
        clock=time.monotonic,
        clipboard=None,
    ):
        self.width, self.height = width, height
        self.name = name
        self.clock = clock
        self.clipboard = clipboard
        self.recording = False
        self.finished = False
        self.cancelled = False
        self.started_at = None
        self._last_at = None
        self._steps = []
        self._held = set()
        self._controls = set()
        self._lock = threading.RLock()

    @property
    def modifiers(self):
        with self._lock:
            return {MODIFIERS[k] for k in self._held}

    def start(self):
        with self._lock:
            if self.finished:
                raise ValueError("A finished recording cannot be restarted.")
            self.started_at = self.clock()
            self.recording = True

    def stop(self):
        with self._lock:
            if self.recording and self._steps:
                self._steps[-1]["delay"] = round(
                    max(0, self.clock() - self._last_at), 3
                )
            self.recording = False
            self.finished = True

    def cancel(self):
        with self._lock:
            self.cancelled = True
            self.recording = False
            self.finished = True

    def _append(self, step, *, merge_text=False):
        now = self.clock()
        gap = max(0, now - self._last_at) if self._last_at is not None else 0
        if (
            merge_text
            and self._steps
            and self._steps[-1]["action"] == "text"
            and gap < 0.75
        ):
            text = self._steps[-1]["text"] + step["text"]
            if len(text) > 10000:
                raise ValueError("Recorded text exceeds the macro text limit.")
            self._steps[-1]["text"] = text
        else:
            if len(self._steps) >= 1000:
                raise ValueError(
                    "Recording exceeds 1000 steps; record a shorter macro."
                )
            if self._steps:
                self._steps[-1]["delay"] = round(gap, 3)
            self._steps.append({**step, "delay": 0.0})
        self._last_at = now

    def click(self, x, y, button):
        with self._lock:
            if not self.recording:
                return
            if self.modifiers:
                raise ValueError(
                    "Modifier-clicks are unsupported. Use ordinary clicks and keyboard hotkeys."
                )
            if not 0 <= x < self.width or not 0 <= y < self.height:
                raise ValueError("Record clicks on the primary monitor only.")
            if button not in ("left", "right", "middle"):
                raise ValueError("Only left, right, and middle clicks are supported.")
            self._append(
                {"action": "click", "x": int(x), "y": int(y), "button": button}
            )

    def key_press(self, key, *, text=None):
        with self._lock:
            if key in ("f8", "f9"):
                if key in self._controls:
                    return
                self._controls.add(key)
                if key == "f9":
                    self.cancel()
                elif not self.finished:
                    self.stop() if self.recording else self.start()
                return
            if not self.recording:
                return
            if key in MODIFIERS:
                self._held.add(key)
                return
            modifiers = self.modifiers
            if "altgr" not in modifiers and modifiers.intersection(
                {"ctrl", "alt", "winleft"}
            ):
                if (
                    key == "v"
                    and modifiers <= {"ctrl", "shift"}
                    and "ctrl" in modifiers
                ):
                    value = self.clipboard() if self.clipboard else None
                    if not isinstance(value, str) or len(value) > 10000:
                        raise ValueError(
                            "Clipboard paste requires text of at most 10000 characters."
                        )
                    self._append({"action": "text", "text": value})
                else:
                    keys = [
                        m for m in ("ctrl", "alt", "shift", "winleft") if m in modifiers
                    ]
                    self._append({"action": "hotkey", "keys": keys + [key]})
            elif key == "insert" and modifiers == {"shift"}:
                value = self.clipboard() if self.clipboard else None
                if not isinstance(value, str) or len(value) > 10000:
                    raise ValueError(
                        "Clipboard paste requires text of at most 10000 characters."
                    )
                self._append({"action": "text", "text": value})
            elif text is not None:
                if not isinstance(text, str) or len(text) != 1 or ord(text) < 32:
                    raise ValueError(
                        "Unsupported text key; use ordinary text or a recorded hotkey."
                    )
                self._append({"action": "text", "text": text}, merge_text=True)
            elif modifiers == {"shift"}:
                self._append({"action": "hotkey", "keys": ["shift", key]})
            else:
                self._append({"action": "press", "key": key})

    def key_release(self, key):
        with self._lock:
            self._held.discard(key)
            self._controls.discard(key)

    def document(self):
        with self._lock:
            if not self.finished or self.cancelled or not self._steps:
                raise ValueError("No completed recording to export.")
            return {
                "name": self.name,
                "description": "Recorded locally on "
                + datetime.now().astimezone().isoformat(timespec="seconds"),
                "desktop": {"width": self.width, "height": self.height},
                "steps": copy.deepcopy(self._steps),
            }


def record_macro(name, width, height, *, max_seconds=35):
    if sys.platform != "win32":
        raise RuntimeError("Live recording requires your Windows desktop.")
    from macro_player import enable_dpi_awareness

    enable_dpi_awareness()
    from pynput import keyboard, mouse
    import pyperclip

    rec = RecordedMacro(width, height, name=name, clipboard=pyperclip.paste)
    errors = []
    pressed_buttons = {}

    def guarded(action):
        try:
            action()
        except Exception as exc:
            errors.append(exc)
            rec.cancel()
        return False if rec.finished else None

    def on_press(key):
        def handle():
            value, text = translate_key(key, rec.modifiers)
            was_recording = rec.recording
            rec.key_press(value, text=text)
            if not was_recording and rec.recording:
                print("Recording started. F8 stops and saves; F9 cancels.", flush=True)

        return guarded(handle)

    def on_release(key):
        return guarded(lambda: rec.key_release(translate_key(key, rec.modifiers)[0]))

    def on_click(x, y, button, pressed):
        def handle():
            if not rec.recording:
                return
            if pressed:
                if rec.modifiers:
                    raise ValueError(
                        "Modifier-clicks are unsupported. Use ordinary clicks and keyboard hotkeys."
                    )
                pressed_buttons[button] = (x, y)
            elif button in pressed_buttons:
                start = pressed_buttons.pop(button)
                if max(abs(x - start[0]), abs(y - start[1])) > 5:
                    raise ValueError(
                        "Dragging is unsupported; use a click-only navigation sequence."
                    )
                rec.click(start[0], start[1], button.name)

        return guarded(handle)

    def on_move(x, y):
        def handle():
            if rec.recording and any(
                max(abs(x - origin[0]), abs(y - origin[1])) > 5
                for origin in pressed_buttons.values()
            ):
                raise ValueError(
                    "Dragging is unsupported; use a click-only navigation sequence."
                )

        return guarded(handle)

    def on_scroll(*args):
        def handle():
            if rec.recording:
                raise ValueError("Scrolling is unsupported by the bot macro format.")

        return guarded(handle)

    print(
        "Switch to the BMS and press F8 to start. F9 cancels. Text/passwords are saved locally.",
        flush=True,
    )
    with mouse.Listener(
        on_click=on_click, on_move=on_move, on_scroll=on_scroll
    ), keyboard.Listener(on_press=on_press, on_release=on_release):
        while not rec.finished:
            if rec.recording and time.monotonic() - rec.started_at >= max_seconds:
                rec.stop()
                print("Recording reached the configured time limit.", flush=True)
            time.sleep(0.05)
    if errors:
        raise errors[0]
    return rec.document()
