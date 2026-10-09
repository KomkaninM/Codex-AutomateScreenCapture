"""Operator-facing failures whose messages contain no credentials or macro text."""


class AutomationError(RuntimeError):
    """An actionable desktop setup/session error safe to report to the operator."""
