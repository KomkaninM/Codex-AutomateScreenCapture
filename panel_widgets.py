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
