"""One packaged entry point; internal automation roles never import the Qt UI."""

from __future__ import annotations

import os
import sys

from app_runtime import initialize_project


def report_startup_error(message):
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "BMS Control Panel", 0x10)
    elif sys.stderr:
        print(message, file=sys.stderr)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        initialize_project()
    except (OSError, ValueError):
        if args[:1] == ["--internal-role"]:
            raise
        report_startup_error(
            "Could not prepare the application data. Check BMS_PROJECT_DIR and folder permissions, then reopen the app."
        )
        return 1
    if args[:1] == ["--internal-role"]:
        if len(args) < 2 or args[1] not in {"launcher", "server", "macro"}:
            raise ValueError("Unknown internal application role.")
        role, remaining = args[1], args[2:]
        previous = sys.argv
        sys.argv = [previous[0], *remaining]
        try:
            if role == "launcher":
                from launcher import main as run

                return run()
            if role == "server":
                from server import main as run

                return run() or 0
            from macro_tool import main as run

            return run(remaining)
        finally:
            sys.argv = previous
    if args:
        raise ValueError("Unexpected application arguments.")
    try:
        from qt_panel import main as run
    except ImportError:
        message = "GUI dependencies are missing. Launch start_gui.bat to prepare PySide6, or install requirements.txt with your Python interpreter."
        report_startup_error(message)
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
