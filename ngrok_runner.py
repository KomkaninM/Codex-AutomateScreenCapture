"""Private ngrok authentication configuration and bounded startup diagnostics."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path

TOKEN_PAGE = "https://dashboard.ngrok.com/get-started/your-authtoken"


def runtime_path(project, filename):
    folder = Path(project) / ".runtime"
    if folder.is_symlink():
        raise RuntimeError(".runtime must be a local directory, not a symlink.")
    folder.mkdir(exist_ok=True)
    path = folder / filename
    if path.is_symlink():
        raise RuntimeError("ngrok runtime files must not be symlinks.")
    return path


def prepare_auth_config(cfg, project):
    if not cfg.ngrok_authtoken:
        # Preserve existing authenticated ngrok installations when .env is blank.
        return None
    destination = runtime_path(project, "ngrok.yml")
    descriptor, temporary = tempfile.mkstemp(
        prefix="ngrok_", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            # JSON is valid YAML; ngrok v3 continues to accept version-2 files.
            json.dump(
                {
                    "version": "2",
                    "authtoken": cfg.ngrok_authtoken,
                    "web_addr": "127.0.0.1:4040",
                },
                handle,
            )
            handle.write("\n")
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


class NgrokStartupError(RuntimeError):
    def __init__(self, code="", exit_code=None):
        self.code = code
        self.auth_problem = code in {"ERR_NGROK_4018", "ERR_NGROK_105", "ERR_NGROK_107"}
        if self.auth_problem:
            hint = "ngrok needs a verified account and a valid NGROK_AUTHTOKEN."
        elif code in {"ERR_NGROK_108", "ERR_NGROK_334"}:
            hint = "Another ngrok agent or endpoint is already active; check your existing ngrok sessions."
        else:
            hint = "Check the error above and .runtime/ngrok.log for authentication, domain, or network details."
        label = code or f"exit status {exit_code}"
        super().__init__(f"ngrok could not start ({label}). {hint}")


class NgrokDiagnostics:
    def __init__(self, project, *, secrets=()):
        self.log_path = runtime_path(project, "ngrok.log")
        self.secrets = tuple(s for s in secrets if s)
        self._code = ""
        self._thread = None
        self._lock = threading.Lock()
        with self.log_path.open("w", encoding="utf-8"):
            os.chmod(self.log_path, 0o600)

    @property
    def error_code(self):
        with self._lock:
            return self._code

    def _redact(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[redacted]")
        text = re.sub(
            r"""(?i)(\b(?:authtoken|auth[ _-]?token|token|authorization|password|secret)\b["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,}]+)""",
            r"\1[redacted]",
            text,
        )
        return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", text)

    def start(self, stream):
        self._thread = threading.Thread(
            target=self._consume, args=(stream,), name="ngrok-output", daemon=True
        )
        self._thread.start()

    def _consume(self, stream):
        written = 0
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                for raw in stream:
                    codes = re.findall(r"ERR_NGROK_\d+", raw)
                    if codes:
                        with self._lock:
                            self._code = codes[-1]
                    safe = self._redact(raw)
                    if written < 65536:
                        chunk = safe[: 65536 - written]
                        handle.write(chunk)
                        handle.flush()
                        written += len(chunk)
                    try:
                        event = json.loads(safe)
                    except ValueError:
                        event = {}
                    if not isinstance(event, dict):
                        event = {}
                    if (
                        codes
                        or str(event.get("lvl", "")).lower()
                        in ("error", "eror", "fatal")
                        or safe.lstrip().upper().startswith(("ERROR", "FATAL"))
                    ):
                        message = str(event.get("msg", safe.strip()))
                        if event.get("err"):
                            message += " " + str(event["err"])
                        print("ngrok: " + message[:1500], flush=True)
        except (OSError, TypeError, ValueError):
            print(
                "ngrok diagnostics could not read output; check .runtime/ngrok.log.",
                flush=True,
            )
        finally:
            if stream is not None:
                stream.close()

    def join(self):
        if self._thread is not None:
            self._thread.join(timeout=2)
