"""Lossless macro documents and private file/detection services for the Qt editor."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path

from automation_errors import AutomationError
from config import Settings, macro_name
from macro_player import MacroPlayer
from panel_settings import ProjectStore, project_idle

DEFAULT_STEPS = {
    "click": {"action": "click", "x": 0, "y": 0, "button": "left", "clicks": 1},
    "press": {"action": "press", "key": "enter"},
    "hotkey": {"action": "hotkey", "keys": ["ctrl", "a"]},
    "text": {"action": "text", "text": ""},
    "sleep": {"action": "sleep", "seconds": 1},
    "wait_image": {
        "action": "wait_image",
        "image": "",
        "condition": "appear",
        "timeout": 60,
        "on_timeout": "stop",
    },
}


def action_of(step):
    return step.get("action", step.get("type")) if isinstance(step, dict) else None


class MacroDocument:
    def __init__(self, payload, filename=None, version=None):
        steps = payload.get("steps") if isinstance(payload, dict) else payload
        if not isinstance(steps, list):
            raise ValueError("Macro JSON must contain a steps array.")
        if len(steps) > 1000:
            raise ValueError("A macro can contain at most 1000 steps.")
        self.payload = copy.deepcopy(payload)
        self.filename, self.version = filename, version
        self.saved = copy.deepcopy(payload)

    @classmethod
    def new(cls):
        return cls({"name": "New macro", "description": "", "steps": []})

    @property
    def steps(self):
        return self.payload["steps"] if isinstance(self.payload, dict) else self.payload

    @property
    def dirty(self):
        return self.payload != self.saved

    def editable(self, index):
        action = action_of(self.steps[index])
        return isinstance(action, str) and action in DEFAULT_STEPS

    def update_step(self, index, changes):
        if not self.editable(index):
            raise ValueError(
                "This existing step is read-only. Its original properties are preserved."
            )
        self.steps[index].update(copy.deepcopy(changes))

    def add(self, action):
        if action not in DEFAULT_STEPS or len(self.steps) >= 1000:
            raise ValueError(
                "Choose a supported action; a macro can contain at most 1000 steps."
            )
        self.steps.append(copy.deepcopy(DEFAULT_STEPS[action]))
        return len(self.steps) - 1

    def duplicate(self, index):
        if len(self.steps) >= 1000:
            raise ValueError("A macro can contain at most 1000 steps.")
        self.steps.insert(index + 1, copy.deepcopy(self.steps[index]))
        return index + 1

    def delete(self, index):
        self.steps.pop(index)
        return min(index, len(self.steps) - 1)

    def move(self, index, offset):
        destination = index + offset
        if not 0 <= destination < len(self.steps):
            return index
        self.steps[index], self.steps[destination] = (
            self.steps[destination],
            self.steps[index],
        )
        return destination

    def summary(self, index):
        step = self.steps[index]
        action = action_of(step)
        if action == "click":
            return f"Click: X={step.get('x', '?')}, Y={step.get('y', '?')}"
        if action == "wait_image":
            state = "disappears" if step.get("condition") == "disappear" else "appears"
            return f"Wait: {Path(str(step.get('image', 'Choose image'))).name} {state}"
        if action == "press":
            return f"Keyboard: {step.get('key', '?')}"
        if action == "hotkey":
            keys = step.get("keys", [])
            return "Shortcut: " + (
                " + ".join(keys)
                if isinstance(keys, list) and all(isinstance(k, str) for k in keys)
                else "Needs repair"
            )
        if action == "text":
            return "Type Text: [hidden]"
        if action == "sleep":
            return f"Delay: {step.get('seconds', 0)} seconds"
        return "Existing unsupported step (read-only)"


class MacroRepository(ProjectStore):
    def configuration(self):
        return Settings.load(self.project, environ={})

    def path(self, name):
        directory = self.configuration().macros_dir.resolve()
        candidate = directory / macro_name(name)
        if candidate.resolve().parent != directory:
            raise ValueError("Macro path escapes the macro directory.")
        return candidate

    def open(self, name):
        path = self.path(name)
        if path.stat().st_size > 1_000_000:
            raise ValueError("Macro files must be smaller than 1 MB.")
        payload = path.read_bytes()
        try:
            data = json.loads(payload.decode("utf-8-sig"))
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError(
                "Invalid JSON. The current editor contents have been kept."
            ) from None
        return MacroDocument(data, name, hashlib.sha256(payload).hexdigest())

    def player(self):
        cfg = self.configuration()
        return MacroPlayer(
            cfg.macros_dir, max_seconds=cfg.max_macro_seconds, project_dir=self.project
        )

    def validate(self, payload):
        document = MacroDocument(payload)
        player = self.player()
        for index, step in enumerate(document.steps, 1):
            try:
                player.validate([step])
            except (
                ValueError,
                AutomationError,
                OverflowError,
                RecursionError,
            ) as error:
                text = (
                    str(error)
                    if isinstance(error, (ValueError, AutomationError))
                    else "Invalid numeric or nested value."
                )
                raise ValueError(f"Step {index}: {text}") from None
        try:
            player.validate(payload)
        except (ValueError, AutomationError, OverflowError, RecursionError) as error:
            raise ValueError(
                str(error)
                if isinstance(error, (ValueError, AutomationError))
                else "Invalid macro values."
            ) from None

    def save(self, name, payload, expected):
        destination = self.path(name)
        self.validate(payload)
        try:
            encoded = (
                json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
                + "\n"
            ).encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            raise ValueError(
                "The macro contains values that cannot be saved as JSON."
            ) from None
        if len(encoded) > 1_000_000:
            raise ValueError("Macro files must be smaller than 1 MB.")
        with project_idle(self.project):
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._check_version(destination, expected)
            draft = self._draft(".json")
            try:
                draft.write_bytes(encoded)
                self._replace(draft, destination, expected)
            finally:
                draft.unlink(missing_ok=True)
        return hashlib.sha256(encoded).hexdigest()

    def relative_image(self, source):
        try:
            return Path(source).resolve().relative_to(self.project).as_posix()
        except ValueError:
            raise ValueError(
                "Choose an image inside the project folder. To use an outside image, explicitly copy it into assets first; the executor cannot resolve outside paths."
            ) from None

    def preview_image(self, relative):
        from PIL import Image

        try:
            path = self.player()._image_path(relative)
        except (ValueError, AutomationError) as error:
            raise ValueError(str(error)) from None
        try:
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((360, 180))
                buffer = io.BytesIO()
                image.save(buffer, "PNG")
                return buffer.getvalue()
        except Exception:
            raise ValueError("Unable to load reference image.") from None

    def test_image(self, step):
        if action_of(step) != "wait_image":
            raise ValueError("Select a Wait Image step first.")
        try:
            player = self.player()
            player.validate([step])
            path = player._image_path(step["image"])
            from detector import image_visible

            present = image_visible(path, confidence=step.get("confidence", 0.9))
        except (AutomationError, ValueError, OverflowError) as error:
            raise ValueError(str(error)) from None
        return {
            "present": present,
            "satisfied": present if step["condition"] == "appear" else not present,
        }
