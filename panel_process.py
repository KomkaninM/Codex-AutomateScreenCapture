"""Own one launcher or macro-tool child and relay bounded, redacted output to Tk."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import dotenv_values

from config import Settings, macro_name
from panel_settings import ENV_FIELDS, SECRET_FIELDS
from runtime_control import ControlSession


class ProcessController:
    def __init__(self, project, *, python=None):
        self.project = Path(project).resolve()
        interpreter = Path(python or sys.executable)
        console = interpreter.with_name("python.exe")
        self.python = str(
            console
            if interpreter.name.lower() == "pythonw.exe" and console.is_file()
            else interpreter
        )
        self.events = queue.Queue(maxsize=500)
        self.lock = threading.RLock()
        self.process = None
        self.session = None
        self.state = "stopped"
        self.mode = "bot"
        self.status = {}
        self.worker = None
        self.secrets = ()

    @property
    def busy(self):
        with self.lock:
            return self.state in {
                "starting",
                "online",
                "stopping",
                "recording",
                "playing",
            }

    def _event(self, kind, value):
        item = (kind, value)
        try:
            self.events.put_nowait(item)
        except queue.Full:
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            try:
                self.events.put_nowait(item)
            except queue.Full:
                pass

    def _log(self, text):
        text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
        for value in self.secrets:
            text = text.replace(value, "[hidden]")
        text = text.rstrip()[:4000]
        self._event("log", text)
        path = self.project / ".runtime" / "control_panel.log"
        try:
            if path.exists() and path.stat().st_size > 1_000_000:
                path.replace(path.with_suffix(".previous.log"))
            with path.open("a", encoding="utf-8") as file:
                file.write(text + "\n")
        except OSError:
            pass

    def start_bot(self):
        self._launch("bot", [])

    def start_macro(self, command, name):
        if command not in {"record", "play"}:
            raise ValueError("Choose Record or Play.")
        self._launch(command, [macro_name(name)])

    def _launch(self, mode, args):
        with self.lock:
            if self.busy:
                raise RuntimeError("Stop the current bot or macro operation first.")
            self.session = ControlSession(self.project)
            self.mode = mode
            self.status = {}
            self.state = (
                "starting"
                if mode == "bot"
                else {"record": "recording", "play": "playing"}[mode]
            )
            raw = dotenv_values(self.project / ".env", interpolate=False)
            self.secrets = tuple(
                sorted(
                    {
                        raw.get(key)
                        for key in SECRET_FIELDS | {"LINE_CHANNEL_ACCESS_TOKEN"}
                        if raw.get(key)
                    },
                    key=len,
                    reverse=True,
                )
            )
            environment = os.environ.copy()
            for key in ENV_FIELDS.keys() | {
                "LINE_CHANNEL_ACCESS_TOKEN",
                "DEFAULT_LOGIN_MACRO",
                "PUBLIC_TUNNEL_URL",
            }:
                environment.pop(key, None)
            environment.update(
                {
                    "PYTHONUTF8": "1",
                    "PYTHONUNBUFFERED": "1",
                    "BMS_CONTROL_SESSION": self.session.id,
                }
            )
            command = (
                [
                    self.python,
                    str(self.project / "launcher.py"),
                    "--run",
                    "--control-session",
                    self.session.id,
                ]
                if mode == "bot"
                else [self.python, str(self.project / "macro_tool.py"), mode, *args]
            )
            try:
                self.process = subprocess.Popen(
                    command,
                    cwd=self.project,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                self.state = "error"
                raise RuntimeError(
                    "Could not start Python. Reopen the app using start_gui.bat."
                ) from None
            self._event("state", self.state)
            self.worker = threading.Thread(
                target=self._monitor,
                args=(self.process, self.session, mode),
                name="bms-panel-process",
                daemon=True,
            )
            self.worker.start()

    def _read_output(self, process):
        discarded = False
        try:
            while True:
                line = process.stdout.readline(65536)
                if not line:
                    break
                complete = line.endswith("\n")
                if not complete and len(line) == 65536:
                    if not discarded:
                        self._log("[Oversized output line omitted]")
                    discarded = True
                    continue
                if not discarded:
                    self._log(line)
                if complete:
                    discarded = False
        finally:
            process.stdout.close()

    def _monitor(self, process, session, mode):
        reader = threading.Thread(
            target=self._read_output,
            args=(process,),
            name="bms-panel-output",
            daemon=True,
        )
        reader.start()
        while process.poll() is None:
            status = session.read_status()
            with self.lock:
                self.status = status
                if (
                    mode == "bot"
                    and status.get("stage") == "online"
                    and self.state == "starting"
                ):
                    self.state = "online"
                    self._event("state", self.state)
            time.sleep(0.1)
        code = process.wait()
        reader.join(timeout=3)
        with self.lock:
            self.state = "stopped" if code in (0, 130) else "error"
            self._event("finished", {"mode": mode, "code": code})
            self._event("state", self.state)

    def stop(self):
        with self.lock:
            if not self.busy or self.session is None:
                return
            self.state = "stopping"
            self.session.request_stop()
            self._event("state", self.state)
            self._log(
                "Stop requested. Waiting for active work and owned ngrok cleanup..."
            )

    def close(self):
        self.stop()
        if self.worker:
            self.worker.join(timeout=10)
