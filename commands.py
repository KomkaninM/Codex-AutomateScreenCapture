"""Command grammar and reply-only interactive command execution."""

from __future__ import annotations

import logging
import re
import shlex
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from automation_errors import AutomationError, AutomationTimeoutError
from config import macro_name
from scheduler import parse_interval, ScheduleCancelled

log = logging.getLogger(__name__)
HELP = (
    "Commands: capture [target] [note] [-s HH:MM[:SS]], "
    "start-capture 30m [note] [-s HH:MM[:SS]] [-m count], "
    "set-login macro.json, login, macro name.json, "
    "enable-autologout, disable-autologout, check-id, check-quota, stop-capture. "
    "Long options --starttime and --max are also supported."
)


class CommandInputError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    name: str
    target: str | None = None
    note: str = ""
    starttime: str | None = None
    interval: float | None = None
    macro: str | None = None
    max_captures: int | None = None


@dataclass
class ScheduleProgress:
    # Updated inside the serialized capture delivery callback, after LINE acceptance.
    delivered: int = 0
    successful_captures: int = 0
    max_captures: int | None = None
    completion_attempted: bool = False

    @property
    def limit_reached(self):
        return (
            self.max_captures is not None
            and self.successful_captures >= self.max_captures
        )


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
        aliases = {
            "-s": "starttime",
            "--starttime": "starttime",
            "—starttime": "starttime",
            "–starttime": "starttime",
            "-m": "max",
            "--max": "max",
        }
        start = None
        max_captures = None
        positionals = []
        seen = set()
        i = 0
        while i < len(parts):
            option = aliases.get(parts[i])
            if option is None:
                if parts[i].startswith("--"):
                    raise CommandInputError(f"Unknown command option: {parts[i]}")
                positionals.append(parts[i])
                i += 1
                continue
            if option in seen:
                label = "Start time" if option == "starttime" else "Maximum"
                raise CommandInputError(f"{label} may be specified only once.")
            seen.add(option)
            if i + 1 >= len(parts) or parts[i + 1] in aliases:
                label = "Start time" if option == "starttime" else "Maximum"
                raise CommandInputError(f"{label} option requires a value.")
            value = parts[i + 1]
            if option == "starttime":
                try:
                    parsed = datetime.strptime(
                        value, "%H:%M:%S" if value.count(":") == 2 else "%H:%M"
                    )
                except ValueError:
                    raise CommandInputError(
                        "Invalid start time; use HH:MM or HH:MM:SS."
                    ) from None
                start = parsed.strftime("%H:%M:%S")
            elif not re.fullmatch(r"[1-9]\d*", value):
                raise CommandInputError("Maximum captures must be a positive integer.")
            else:
                max_captures = int(value)
            i += 2
        parts = positionals
        if name != "start-capture" and max_captures is not None:
            raise CommandInputError(
                "Maximum captures is supported only by start-capture."
            )
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
        return Command(
            name,
            target,
            " ".join(parts),
            start,
            interval,
            max_captures=max_captures,
        )
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


def readable_interval(seconds):
    for unit_seconds, singular, plural in (
        (3600, "hour", "hours"),
        (60, "minute", "minutes"),
    ):
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            value = seconds / unit_seconds
            return f"{value:g} {singular if value == 1 else plural}"
    return f"{seconds:g} {'second' if seconds == 1 else 'seconds'}"


class Bot:
    def __init__(self, runtime, workflow, line, scheduler):
        self.runtime = runtime
        self.workflow = workflow
        self.line = line
        self.scheduler = scheduler

    def _target_macro(self, target, cfg=None):
        cfg = cfg or self.runtime.snapshot()
        return (
            self.workflow.targets[target]["macro"]
            if target
            else cfg.default_login_macro
        )

    def _image_messages(self, shot, note="", *, capture_number=None, target=None):
        cfg = self.runtime.snapshot()
        urls = shot.urls(cfg.public_tunnel_url)
        captured_at = shot.captured_at.astimezone(ZoneInfo(cfg.timezone))
        heading = (
            f"Scheduled capture #{capture_number}"
            if capture_number is not None
            else "Capture"
        )
        caption = (
            f"{heading}\nTimestamp: {captured_at:%Y-%m-%d %H:%M:%S} ({cfg.timezone})"
        )
        if target:
            caption += f"\nTarget: {target}\nMacro: {self._target_macro(target, cfg)}"
        if note:
            caption += f"\nNote: {note}"
        return [self.line.text(caption), self.line.image(*urls)]

    def _schedule_message(self, job_id, due, command, cfg):
        lines = [
            "Schedule started",
            f"Code: {job_id}",
            f"Starts: {due:%Y-%m-%d %H:%M:%S} ({cfg.timezone})",
            (
                f"Interval: {readable_interval(command.interval)}"
                if command.interval is not None
                else "Interval: Once"
            ),
            f"Target: {command.target or 'Default'}",
            f"Macro: {self._target_macro(command.target, cfg)}",
        ]
        if command.max_captures is not None:
            lines.append(f"Maximum captures: {command.max_captures}")
        if command.note:
            lines.append(f"Note: {command.note}")
        return "\n".join(lines)

    def _completion_message(self, note, progress):
        return "\n".join(
            [
                "✅ Capture Schedule Completed",
                "",
                f"Note: {note or 'None'}",
                (
                    f"Pictures Sent: {progress.successful_captures}/"
                    f"{progress.max_captures}"
                ),
                "Status: Completed",
            ]
        )

    def _complete_schedule(self, job_id, note, progress, cfg):
        if (
            job_id is None
            or not progress.limit_reached
            or progress.completion_attempted
        ):
            return False
        if not self.scheduler.cancel(job_id):
            return False
        progress.completion_attempted = True
        self.line.push(
            cfg.delivery_id,
            [self.line.text(self._completion_message(note, progress))],
        )
        return True

    def _scheduled(self, target, note, cancel, progress=None, job_id=None):
        progress = progress if progress is not None else ScheduleProgress()
        cfg = self.runtime.snapshot()
        if not cfg.public_tunnel_url or not cfg.delivery_id:
            raise RuntimeError(
                "Scheduled captures require GROUP_ID or USER_ID and a public HTTPS tunnel."
            )

        def deliver(shot):
            log.info("Sending scheduled image to LINE; waiting for acceptance.")
            self.line.push(
                cfg.delivery_id,
                self._image_messages(
                    shot,
                    note,
                    capture_number=progress.delivered + 1,
                    target=target,
                ),
            )
            progress.delivered += 1
            progress.successful_captures += 1
            log.info("LINE accepted the scheduled image message.")

        delivered_before = progress.successful_captures
        try:
            self.workflow.capture(target, cancel=cancel, deliver=deliver)
        finally:
            if progress.successful_captures > delivered_before:
                self._complete_schedule(job_id, note, progress, cfg)

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
        reply_attempted = False
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
                messages = [self.line.text("Login check completed.")]
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
                    progress = ScheduleProgress(max_captures=command.max_captures)
                    schedule_cancel = threading.Event()
                    immediate_initial = (
                        command.name == "start-capture" and not command.starttime
                    )
                    job_id = self.scheduler.add(
                        command.interval,
                        due,
                        lambda cancel: self._scheduled(
                            command.target,
                            command.note,
                            cancel,
                            progress,
                            job_id,
                        ),
                        lambda cancel: self.workflow.prepare(
                            command.target, cancel=cancel
                        ),
                        expected_generation=schedule_generation,
                        cancel=schedule_cancel,
                        paused=immediate_initial,
                    )
                    schedule_message = self.line.text(
                        self._schedule_message(job_id, due, command, cfg)
                    )
                    if immediate_initial:
                        initial_delivered = False

                        def deliver_initial(shot):
                            nonlocal initial_delivered, reply_attempted
                            initial_messages = [
                                schedule_message,
                                *self._image_messages(
                                    shot,
                                    command.note,
                                    capture_number=0,
                                    target=command.target,
                                ),
                            ]
                            self.workflow.check(deadline=deadline)
                            reply_attempted = True
                            log.info(
                                "Sending initial scheduled image to LINE; waiting for acceptance."
                            )
                            self.line.reply(token, initial_messages)
                            initial_delivered = True
                            progress.successful_captures += 1
                            log.info("LINE accepted scheduled capture #0.")

                        try:
                            self.workflow.capture(
                                command.target,
                                deadline=deadline,
                                cancel=schedule_cancel,
                                deliver=deliver_initial,
                            )
                        except BaseException:
                            if not initial_delivered:
                                self.scheduler.cancel(job_id)
                            raise
                        finally:
                            completed = self._complete_schedule(
                                job_id, command.note, progress, cfg
                            )
                            if initial_delivered and not completed:
                                self.scheduler.resume(job_id)
                    else:
                        messages = [schedule_message]
                else:

                    def deliver(shot):
                        nonlocal reply_attempted
                        image_messages = self._image_messages(
                            shot, command.note, target=command.target
                        )
                        self.workflow.check(deadline=deadline)
                        reply_attempted = True
                        log.info(
                            "Sending interactive image to LINE; waiting for acceptance."
                        )
                        self.line.reply(token, image_messages)
                        log.info("LINE accepted the interactive image message.")

                    self.workflow.capture(
                        command.target, deadline=deadline, deliver=deliver
                    )
        except ScheduleCancelled:
            messages = [
                self.line.text("Schedule request canceled by a later stop-capture.")
            ]
        except CommandInputError as exc:
            log.warning("Invalid command input: %s", type(exc).__name__)
            messages = [self.line.text(str(exc))]
        except (ValueError, FileNotFoundError) as exc:
            # Do not disclose absolute paths or filesystem details to the chat.
            log.warning("Command configuration/input failure: %s", type(exc).__name__)
            messages = [
                self.line.text(
                    "Invalid command or missing macro/configuration. Use help and check the host setup."
                )
            ]
        except (AutomationError, AutomationTimeoutError) as exc:
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
        if messages and not reply_attempted and time.monotonic() < deadline:
            try:
                self.line.reply(token, messages)
            except Exception as exc:
                log.error(
                    "Interactive reply failed; no push fallback: %s", type(exc).__name__
                )
