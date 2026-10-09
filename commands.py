"""Command grammar and reply-only interactive command execution."""

from __future__ import annotations

import logging
import shlex
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from automation_errors import AutomationError
from config import macro_name
from scheduler import parse_interval, ScheduleCancelled

log = logging.getLogger(__name__)
HELP = (
    "Commands: capture [target] [note] [--starttime HH:MM[:SS]], "
    "start-capture 30m [note] [--starttime HH:MM[:SS]], stop-capture, "
    "set-login macro.json, login, macro name.json, "
    "enable-autologout, disable-autologout, check-id, check-quota"
)


@dataclass(frozen=True)
class Command:
    name: str
    target: str | None = None
    note: str = ""
    starttime: str | None = None
    interval: float | None = None
    macro: str | None = None


def parse_command(text, targets):
    try:
        parts = shlex.split(text)
    except ValueError:
        raise ValueError("Unbalanced command quoting.") from None
    if not parts:
        return Command("unknown")
    name = parts.pop(0).lower()
    if name not in {
        "capture",
        "start-capture",
        "stop-capture",
        "set-login",
        "login",
        "macro",
        "enable-autologout",
        "disable-autologout",
        "check-id",
        "check-quota",
        "help",
    }:
        return Command("unknown")
    if name in ("capture", "start-capture"):
        start = None
        if "--starttime" in parts:
            i = parts.index("--starttime")
            if i + 1 >= len(parts) or parts.count("--starttime") != 1:
                raise ValueError("--starttime needs exactly one HH:MM[:SS] value.")
            start = parts[i + 1]
            try:
                datetime.strptime(
                    start, "%H:%M:%S" if start.count(":") == 2 else "%H:%M"
                )
            except ValueError:
                raise ValueError("Invalid --starttime; use HH:MM[:SS].") from None
            parts[i : i + 2] = []
        if any(p.startswith("--") for p in parts):
            raise ValueError("Unknown command option.")
        interval = None
        if name == "start-capture":
            if not parts:
                raise ValueError("start-capture requires an interval, e.g. 30m.")
            interval = parse_interval(parts.pop(0))
        target = None
        if parts:
            ids = {k.upper(): k for k in targets}
            if parts[0].upper() in ids:
                target = ids[parts.pop(0).upper()]
        return Command(name, target, " ".join(parts), start, interval)
    if name in ("set-login", "macro"):
        if len(parts) != 1:
            raise ValueError(f"{name} requires exactly one macro filename.")
        value = parts[0] if parts[0].endswith(".json") else parts[0] + ".json"
        return Command(name, macro=macro_name(value))
    if parts:
        raise ValueError(f"{name} does not accept arguments.")
    return Command(name)


def next_start(starttime, now):
    parsed = datetime.strptime(
        starttime, "%H:%M:%S" if starttime.count(":") == 2 else "%H:%M"
    ).time()
    due = now.replace(
        hour=parsed.hour, minute=parsed.minute, second=parsed.second, microsecond=0
    )
    return due if due > now else due + timedelta(days=1)


class Bot:
    def __init__(self, runtime, workflow, line, scheduler):
        self.runtime = runtime
        self.workflow = workflow
        self.line = line
        self.scheduler = scheduler

    def _image_messages(self, shot, note=""):
        urls = shot.urls(self.runtime.snapshot().public_tunnel_url)
        messages = [self.line.image(*urls)]
        if note:
            messages.insert(0, self.line.text(note))
        return messages

    def _scheduled(self, target, note, cancel):
        cfg = self.runtime.snapshot()
        if not cfg.public_tunnel_url or not cfg.delivery_id:
            raise RuntimeError(
                "Scheduled captures require GROUP_ID or USER_ID and a public HTTPS tunnel."
            )
        shot = self.workflow.capture(target, cancel=cancel)
        if not cancel.is_set():
            self.line.push(cfg.delivery_id, self._image_messages(shot, note))

    def cancel_schedules(self):
        return self.scheduler.stop_all()

    def schedule_generation(self):
        return self.scheduler.snapshot_generation()

    def handle(
        self, event, received_at, *, stopped_count=None, schedule_generation=None
    ):
        # Cancellation must take effect even if its acknowledgement expires.
        if (
            event.get("message", {}).get("text", "").strip().lower() == "stop-capture"
            and stopped_count is None
        ):
            stopped_count = self.cancel_schedules()
        token = event.get("replyToken")
        # A late command must never manipulate the BMS and then send a paid substitute.
        if not token or time.monotonic() - received_at >= 45:
            return
        deadline = received_at + 45
        if schedule_generation is None:
            schedule_generation = self.schedule_generation()
        cfg = self.runtime.snapshot()
        messages = None
        try:
            command = parse_command(event["message"]["text"], self.workflow.targets)
            if command.name == "unknown":
                if not cfg.reply_unknown:
                    return
                messages = [self.line.text(HELP)]
            elif command.name == "help":
                messages = [self.line.text(HELP)]
            elif command.name == "check-id":
                source = event.get("source", {})
                messages = [
                    self.line.text(
                        f"groupId: {source.get('groupId','unavailable')}\nuserId: {source.get('userId','unavailable')}"
                    )
                ]
            elif command.name == "check-quota":
                messages = [
                    self.line.text(self.line.status_text(cfg.group_id, cfg.user_id))
                ]
            elif not cfg.delivery_id:
                messages = [
                    self.line.text("Configure GROUP_ID or USER_ID before automation.")
                ]
            elif command.name == "stop-capture":
                messages = [self.line.text(f"Stopped {stopped_count} schedule(s).")]
            elif command.name == "set-login":
                self.workflow.player.load(command.macro)
                self.runtime.set_login(command.macro)
                messages = [self.line.text(f"Default login: {command.macro}")]
            elif command.name in ("enable-autologout", "disable-autologout"):
                enabled = command.name == "enable-autologout"
                if enabled:
                    self.workflow.player.load(cfg.logout_macro)
                self.runtime.set_autologout(enabled)
                messages = [self.line.text(f"Auto logout: {enabled}")]
            elif command.name == "login":
                self.workflow.prepare(deadline=deadline)
                messages = [self.line.text("BMS session is logged in.")]
            elif command.name == "macro":
                self.workflow.macro(command.macro, deadline=deadline)
                messages = [self.line.text("Macro completed.")]
            elif command.name in ("capture", "start-capture"):
                if not cfg.public_tunnel_url:
                    raise ValueError("Configure NGROK_DOMAIN before capture.")
                if command.starttime or command.name == "start-capture":
                    now = datetime.now(ZoneInfo(cfg.timezone))
                    due = (
                        next_start(command.starttime, now)
                        if command.starttime
                        else now + timedelta(seconds=command.interval)
                    )
                    job_id = self.scheduler.add(
                        command.interval,
                        due,
                        lambda cancel: self._scheduled(
                            command.target, command.note, cancel
                        ),
                        lambda cancel: self.workflow.prepare(
                            command.target, cancel=cancel
                        ),
                        expected_generation=schedule_generation,
                    )
                    messages = [
                        self.line.text(
                            f"Scheduled {job_id} at {due.isoformat()}. Delivery uses push quota; group membership scales consumption."
                        )
                    ]
                else:
                    shot = self.workflow.capture(command.target, deadline=deadline)
                    messages = self._image_messages(shot, command.note)
        except ScheduleCancelled:
            messages = [
                self.line.text("Schedule request canceled by a later stop-capture.")
            ]
        except (ValueError, FileNotFoundError) as exc:
            # Do not disclose absolute paths or filesystem details to the chat.
            log.warning("Command configuration/input failure: %s", type(exc).__name__)
            messages = [
                self.line.text(
                    "Invalid command or missing macro/configuration. Use help and check the host setup."
                )
            ]
        except AutomationError as exc:
            log.error("Command workflow failed: %s: %s", type(exc).__name__, exc)
            messages = [self.line.text(f"Automation failed: {exc}")]
        except Exception as exc:
            # Frame locations help debugging without printing exception contents,
            # source lines, or locals that may contain credentials or macro text.
            locations = " -> ".join(
                f"{Path(frame.filename).name}:{frame.lineno} ({frame.name})"
                for frame in traceback.extract_tb(exc.__traceback__)[-8:]
            )
            log.error(
                "Command workflow failed: %s at %s", type(exc).__name__, locations
            )
            messages = [
                self.line.text(
                    "Automation failed. Check the host logs, BMS session, and desktop configuration."
                )
            ]
        if messages and time.monotonic() < deadline:
            try:
                self.line.reply(token, messages)
            except Exception as exc:
                log.error(
                    "Interactive reply failed; no push fallback: %s", type(exc).__name__
                )
