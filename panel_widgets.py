"""Reusable presentation helpers for the native control panel."""

from __future__ import annotations

import re


_STATE_TONES = {
    "online": "success",
    "error": "danger",
    "stopping": "warning",
    "starting": "info",
    "recording": "info",
    "playing": "info",
    "stopped": "neutral",
}


def status_tone(state: str) -> str:
    return _STATE_TONES.get(str(state).strip().lower(), "neutral")


def status_style(state: str) -> str:
    return f"Status.{status_tone(state).title()}.TLabel"


def classify_log_line(text: str) -> str:
    normalized = str(text).casefold()
    if re.search(r"\b(error|exception|traceback|failed|failure)\b", normalized):
        return "error"
    if "setup required" in normalized or re.search(
        r"\b(warn|warning|retry)\b", normalized
    ):
        return "warning"
    return "info"


def filter_log_lines(lines, query="", levels=None):
    needle = str(query).strip().casefold()
    allowed = None if levels is None else {str(level).casefold() for level in levels}
    return [
        line
        for line in lines
        if (not needle or needle in str(line).casefold())
        and (allowed is None or classify_log_line(line) in allowed)
    ]


def responsive_mode(width, breakpoint=820):
    return "compact" if int(width) < int(breakpoint) else "wide"


def item_count(noun, count):
    count = int(count)
    return f"{count} {noun if count == 1 else noun + 's'}"


class LogViewBuffer:
    def __init__(self, limit=800):
        if int(limit) < 1:
            raise ValueError("Log line limit must be positive.")
        self.limit = int(limit)
        self.lines = []

    def append(self, text):
        additions = str(text).splitlines() or [""]
        self.lines.extend(additions)
        if len(self.lines) > self.limit:
            del self.lines[: len(self.lines) - self.limit]

    def visible(self, query="", levels=None):
        return filter_log_lines(self.lines, query=query, levels=levels)

    def clear(self):
        self.lines.clear()
