"""Source/bundle commands and writable operator data, without application imports."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROLE_FILES = {
    "launcher": "launcher.py",
    "server": "server.py",
    "macro": "macro_tool.py",
}


def project_root(*, frozen=None, environ=None, module_file=__file__):
    environ = os.environ if environ is None else environ
    override = environ.get("BMS_PROJECT_DIR", "")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError(
                "BMS_PROJECT_DIR must be an absolute writable folder path."
            )
        return path.resolve()
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        local = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local).resolve() / "BMSAutomation"
    return Path(module_file).resolve().parent


def bundle_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()


def initialize_project(project=None):
    """Copy only missing public defaults; never overwrite operator-owned files."""
    project = project_root() if project is None else Path(project)
    project.mkdir(parents=True, exist_ok=True)
    for relative in (".env.example", "targets.json", "scripts/macros/example.json"):
        destination = project / relative
        source = bundle_root() / relative
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = source.read_bytes()
        try:
            with destination.open("xb") as target:
                target.write(payload)
        except FileExistsError:
            pass
    for relative in ("assets", "screenshots", ".runtime"):
        (project / relative).mkdir(exist_ok=True)
    return project


def child_command(role, args=(), *, python=None, project=None):
    if role not in ROLE_FILES:
        raise ValueError("Unknown internal application role.")
    if getattr(sys, "frozen", False):
        # The worker has a console bootloader for reliable piped logs; it is
        # launched with CREATE_NO_WINDOW. Both executables share one bundle.
        worker = Path(sys.executable).with_name("BMSWorker.exe")
        return [str(worker), "--internal-role", role, *args]
    root = bundle_root()
    if project is not None and (Path(project) / ROLE_FILES[role]).is_file():
        root = Path(project)
    return [str(python or sys.executable), str(root / ROLE_FILES[role]), *args]
