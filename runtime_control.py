"""Per-run local control files; no network shutdown endpoint or shared stop flag."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path


class ControlSession:
    def __init__(self, project, session_id=None):
        self.id = secrets.token_hex(16) if session_id is None else session_id
        if not re.fullmatch(r"[a-f0-9]{32}", self.id):
            raise ValueError("Invalid local control session.")
        self.directory = Path(project) / ".runtime" / "control"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stop_path = self.directory / f"{self.id}.stop"
        self.status_path = self.directory / f"{self.id}.json"

    def request_stop(self):
        self.stop_path.touch(mode=0o600, exist_ok=True)

    def stop_requested(self):
        return self.stop_path.is_file()

    def publish(self, stage, **details):
        draft = self.directory / f"{self.id}.{secrets.token_hex(4)}.tmp"
        try:
            with draft.open("x", encoding="utf-8") as file:
                os.chmod(draft, 0o600)
                json.dump({"stage": stage, "pid": os.getpid(), **details}, file)
            os.replace(draft, self.status_path)
        finally:
            draft.unlink(missing_ok=True)

    def read_status(self):
        try:
            if self.status_path.stat().st_size > 8192:
                return {}
            data = json.loads(self.status_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @contextmanager
    def watch(self, callback):
        closing = threading.Event()

        def monitor():
            while not closing.wait(0.1):
                if self.stop_requested():
                    callback()
                    return

        thread = threading.Thread(target=monitor, name="bms-local-stop", daemon=True)
        thread.start()
        try:
            yield
        finally:
            closing.set()
            thread.join(timeout=1)


def environment_control(project):
    session = os.environ.get("BMS_CONTROL_SESSION")
    return ControlSession(project, session) if session else None
