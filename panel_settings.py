"""Private, validated project-file editing for the desktop control panel."""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from dotenv import dotenv_values, set_key

from config import Settings, macro_name
from launcher import ensure_env_file, launcher_lock
from macro_player import MacroPlayer
from macro_tool import convert_legacy, save_macro
from server import load_targets, single_instance, validate_targets

ENV_FIELDS = {
    "CHANNEL_ACCESS_TOKEN": "channel_access_token",
    "LINE_CHANNEL_SECRET": "channel_secret",
    "GROUP_ID": "group_id",
    "USER_ID": "user_id",
    "NGROK_AUTHTOKEN": "ngrok_authtoken",
    "NGROK_DOMAIN": "ngrok_domain",
    "NGROK_EXE_PATH": "ngrok_exe_path",
    "BMS_USERNAME": "bms_username",
    "BMS_PASSWORD": "bms_password",
    "PORT": "port",
    "LOGIN_MACRO_SCRIPT": "default_login_macro",
    "LOGOUT_MACRO_SCRIPT": "logout_macro",
    "ENABLE_AUTO_LOGOUT": "auto_logout",
    "REPLY_UNKNOWN_COMMANDS": "reply_unknown",
    "MACRO_SETTLE_DELAY": "settle_delay",
    "MAX_MACRO_SECONDS": "max_macro_seconds",
    "TIMEZONE": "timezone",
    "LINE_WEBP_QUALITY": "line_webp_quality",
    "IMAGE_TTL_SECONDS": "image_ttl_seconds",
    "CONFIDENCE_THRESHOLD": "confidence",
    "INTERNAL_API_TOKEN": "internal_api_token",
    "DETECTOR_INTERVAL_SEC": "detector_interval",
    "LOGIN_WAIT_SECONDS": "login_wait_seconds",
    "RECORDED_STEP_DELAY_SECONDS": "recorded_step_delay_seconds",
    "DESKTOP_WIDTH": "expected_width",
    "DESKTOP_HEIGHT": "expected_height",
}
SECRET_FIELDS = {
    "CHANNEL_ACCESS_TOKEN",
    "LINE_CHANNEL_SECRET",
    "NGROK_AUTHTOKEN",
    "INTERNAL_API_TOKEN",
    "BMS_PASSWORD",
}
ALIASES = {
    "CHANNEL_ACCESS_TOKEN": "LINE_CHANNEL_ACCESS_TOKEN",
    "LOGIN_MACRO_SCRIPT": "DEFAULT_LOGIN_MACRO",
    "NGROK_DOMAIN": "PUBLIC_TUNNEL_URL",
}


class StaleConfiguration(ValueError):
    pass


def fingerprint(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@contextmanager
def project_idle(project):
    stack = ExitStack()
    try:
        stack.enter_context(launcher_lock(project))
        stack.enter_context(single_instance(project))
    except RuntimeError:
        stack.close()
        raise RuntimeError(
            "Stop the bot and close any recording/playback or other launcher before saving."
        ) from None
    with stack:
        yield


class ProjectStore:
    def __init__(self, project):
        self.project = Path(project).resolve()

    def load_settings(self):
        ensure_env_file(self.project)
        payload = (self.project / ".env").read_bytes()
        raw = dotenv_values(
            stream=io.StringIO(payload.decode("utf-8")), interpolate=False
        )
        defaults = Settings(project_dir=self.project)
        values = {key: str(getattr(defaults, attr)) for key, attr in ENV_FIELDS.items()}
        values.update({key: value or "" for key, value in raw.items() if key in values})
        for canonical, alias in ALIASES.items():
            if not raw.get(canonical) and raw.get(alias):
                values[canonical] = raw[alias]
        if values["NGROK_DOMAIN"].startswith("https://"):
            values["NGROK_DOMAIN"] = values["NGROK_DOMAIN"][8:].rstrip("/")
        return values, hashlib.sha256(payload).hexdigest()

    def _check_version(self, path, expected):
        if path.is_symlink():
            raise ValueError("Configuration files cannot be symbolic links.")
        if fingerprint(path) != expected:
            raise StaleConfiguration(
                "This file changed in LINE or another window. Reload it before saving."
            )

    def _draft(self, suffix):
        directory = self.project / ".runtime"
        directory.mkdir(exist_ok=True)
        handle = tempfile.NamedTemporaryFile(dir=directory, suffix=suffix, delete=False)
        handle.close()
        path = Path(handle.name)
        os.chmod(path, 0o600)
        return path

    def _replace(self, draft, destination, expected):
        self._check_version(destination, expected)
        if destination.exists():
            backups = self.project / ".runtime" / "backups"
            backups.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            suffix = ".env" if destination.name == ".env" else destination.suffix
            backup = (
                backups / f"{destination.stem}_{stamp}_{secrets.token_hex(4)}{suffix}"
            )
            shutil.copyfile(destination, backup)
            os.chmod(backup, 0o600)
        os.replace(draft, destination)

    def validate_settings(self, values):
        if set(values) - ENV_FIELDS.keys():
            raise ValueError("Unknown settings field.")
        if any(
            "\n" in value or "\r" in value or "\0" in value for value in values.values()
        ):
            raise ValueError("Settings must contain one line per field.")
        candidate = {
            **dotenv_values(self.project / ".env", interpolate=False),
            **self._managed_values(values),
        }
        try:
            return Settings.load(self.project, environ=candidate)
        except ZoneInfoNotFoundError:
            raise ValueError(
                "Unknown timezone. Use a name such as Asia/Bangkok."
            ) from None

    def save_settings(self, values, expected):
        path = self.project / ".env"
        self.validate_settings(values)
        with project_idle(self.project):
            self._check_version(path, expected)
            draft = self._draft(".env")
            try:
                draft.write_bytes(path.read_bytes())
                for key, value in self._managed_values(values).items():
                    set_key(str(draft), key, value, quote_mode="always")
                candidate = dotenv_values(draft, interpolate=False)
                try:
                    Settings.load(self.project, environ=candidate)
                except ZoneInfoNotFoundError:
                    raise ValueError(
                        "Unknown timezone. Use a name such as Asia/Bangkok."
                    ) from None
                self._replace(draft, path, expected)
            finally:
                draft.unlink(missing_ok=True)
        return fingerprint(path)

    def _managed_values(self, values):
        raw = dotenv_values(self.project / ".env", interpolate=False)
        updates = dict(values)
        for canonical, alias in ALIASES.items():
            if canonical in values and alias in raw:
                value = values[canonical]
                if canonical == "NGROK_DOMAIN" and value and "://" not in value:
                    value = "https://" + value
                updates[alias] = value
        return updates

    def load_targets(self):
        path = self.project / "targets.json"
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            return [], None
        if len(payload) > 1_000_000:
            raise ValueError("targets.json is too large.")
        targets = list(validate_targets(json.loads(payload.decode("utf-8"))).values())
        return targets, hashlib.sha256(payload).hexdigest()

    def save_targets(self, rows, expected):
        path = self.project / "targets.json"
        with project_idle(self.project):
            self._check_version(path, expected)
            draft = self._draft(".json")
            try:
                draft.write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                load_targets(draft)
                self._replace(draft, path, expected)
            finally:
                draft.unlink(missing_ok=True)
        return fingerprint(path)

    def list_macros(self):
        cfg = Settings.load(self.project, environ={})
        result = []
        for path in sorted(
            cfg.macros_dir.glob("*.json"), key=lambda p: p.name.casefold()
        ):
            try:
                steps = MacroPlayer(
                    cfg.macros_dir, max_seconds=cfg.max_macro_seconds
                ).load(path.name)
                result.append(
                    {
                        "name": path.name,
                        "steps": len(steps),
                        "valid": True,
                        "modified": path.stat().st_mtime,
                    }
                )
            except (OSError, ValueError):
                result.append(
                    {"name": path.name, "steps": "—", "valid": False, "modified": 0}
                )
        return result

    def import_macro(self, source, name):
        source = Path(source)
        if source.stat().st_size > 1_000_000:
            raise ValueError("Macro files must be smaller than 1 MB.")
        data = convert_legacy(json.loads(source.read_text(encoding="utf-8-sig")))
        cfg = Settings.load(self.project, environ={})
        with project_idle(self.project):
            return save_macro(
                cfg.macros_dir,
                macro_name(name),
                data,
                max_seconds=cfg.max_macro_seconds,
            )

    def readiness(self):
        try:
            cfg = Settings.load(self.project, environ={})
        except (ValueError, ZoneInfoNotFoundError):
            return [
                {
                    "name": "Settings",
                    "ok": False,
                    "detail": "Open Settings and correct invalid values.",
                }
            ]
        rows = []

        def add(name, ok, detail):
            rows.append({"name": name, "ok": bool(ok), "detail": detail})

        add(
            "LINE credentials",
            cfg.channel_access_token and cfg.channel_secret,
            (
                "Token and secret configured."
                if cfg.channel_access_token and cfg.channel_secret
                else "Enter the LINE access token and channel secret in Settings."
            ),
        )
        add(
            "Delivery",
            True,
            (
                f"{cfg.delivery_kind.title()} delivery; GROUP_ID takes priority."
                if cfg.delivery_id
                else "Discovery mode: send check-id in LINE to find your group/user ID."
            ),
        )
        executable = os.path.expandvars(cfg.ngrok_exe_path)
        path = Path(executable).expanduser() if executable else None
        if path is not None and not path.is_absolute():
            path = self.project / path
        available = (
            path.is_file()
            if path
            else bool(shutil.which("ngrok") or (self.project / "ngrok.exe").is_file())
        )
        add(
            "ngrok executable",
            available,
            "Executable found." if available else "Browse to ngrok.exe in Settings.",
        )
        add(
            "Login anchor",
            cfg.logged_out_anchor.is_file(),
            (
                "Login-page anchor found."
                if cfg.logged_out_anchor.is_file()
                else "Select your login-page crop in Macros. Required for capture, not check-id."
            ),
        )
        for label, name in [("Login macro", cfg.default_login_macro)] + (
            [("Logout macro", cfg.logout_macro)] if cfg.auto_logout else []
        ):
            try:
                MacroPlayer(cfg.macros_dir, max_seconds=cfg.max_macro_seconds).load(
                    name
                )
                add(label, True, name)
            except (OSError, ValueError):
                add(
                    label,
                    False,
                    f"Record or import {name}. Required for capture, not check-id.",
                )
        return rows

    def import_anchor(self, source):
        from PIL import Image

        with Image.open(source) as source_image:
            image = source_image.convert("RGB")
        if image.width < 2 or image.height < 2:
            raise ValueError("Select a login-page crop with visible detail.")
        with project_idle(self.project):
            folder = self.project / "assets"
            folder.mkdir(exist_ok=True)
            draft = self._draft(".png")
            destination = folder / "login_anchor.png"
            try:
                image.save(draft, "PNG")
                self._replace(draft, destination, fingerprint(destination))
            finally:
                draft.unlink(missing_ok=True)
        return destination
