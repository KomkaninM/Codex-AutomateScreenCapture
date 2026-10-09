"""Project-local environment configuration and synchronized runtime settings."""

from __future__ import annotations

import ipaddress
import math
import os
import re
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dotenv import dotenv_values, set_key

BASE_DIR = Path(__file__).resolve().parent


def macro_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.json", name):
        raise ValueError("Macro must be a simple .json filename in scripts/macros.")
    return name


def public_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or parsed.port not in (None, 443)
    ):
        raise ValueError(
            "Tunnel URL must be an HTTPS origin without credentials, path, or query."
        )
    host = parsed.hostname.lower()
    if host == "localhost" or "." not in host or host.endswith((".local", ".internal")):
        raise ValueError("Tunnel must use a public hostname.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        raise ValueError("Use the public ngrok hostname, not an IP address.")
    return value.rstrip("/")


def boolean(value: str) -> bool:
    if value.strip().lower() in ("true", "1", "yes", "on"):
        return True
    if value.strip().lower() in ("false", "0", "no", "off"):
        return False
    raise ValueError("Boolean must be true/false, 1/0, yes/no, or on/off.")


@dataclass(frozen=True)
class Settings:
    project_dir: Path = BASE_DIR
    port: int = 5000
    channel_access_token: str = ""
    channel_secret: str = ""
    user_id: str = ""
    group_id: str = ""
    public_tunnel_url: str = ""
    ngrok_domain: str = ""
    default_login_macro: str = "login_bms.json"
    logout_macro: str = "logout.json"
    close_menu_macro: str = "close_menu.json"
    auto_logout: bool = False
    reply_unknown: bool = False
    settle_delay: float = 2.0
    detector_interval: float = 10.0
    confidence: float = 0.8
    internal_api_token: str = ""
    timezone: str = "Asia/Bangkok"
    expected_width: int = 1920
    expected_height: int = 1080
    max_macro_seconds: float = 35.0
    image_ttl_seconds: int = 604800
    login_wait_seconds: float = 5.0

    @property
    def macros_dir(self) -> Path:
        return self.project_dir / "scripts" / "macros"

    @property
    def screenshots_dir(self) -> Path:
        return self.project_dir / "screenshots"

    @property
    def logged_in_anchor(self) -> Path:
        return self.project_dir / "assets" / "logout_anchor.png"

    @property
    def logged_out_anchor(self) -> Path:
        return self.project_dir / "assets" / "login_anchor.png"

    @classmethod
    def load(cls, project_dir: Path = BASE_DIR, environ=None) -> Settings:
        env = {
            **dotenv_values(project_dir / ".env"),
            **(os.environ if environ is None else environ),
        }

        def val(name, default="", alias=None):
            return env.get(name) or (env.get(alias) if alias else None) or default

        domain = val("NGROK_DOMAIN")
        url = val(
            "PUBLIC_TUNNEL_URL",
            ("https://" + domain if domain and "://" not in domain else domain),
        )
        cfg = cls(
            project_dir=Path(project_dir).resolve(),
            port=int(val("PORT", "5000")),
            channel_access_token=val(
                "CHANNEL_ACCESS_TOKEN", alias="LINE_CHANNEL_ACCESS_TOKEN"
            ),
            channel_secret=val("LINE_CHANNEL_SECRET"),
            user_id=val("USER_ID"),
            group_id=val("GROUP_ID"),
            public_tunnel_url=public_url(url) if url else "",
            ngrok_domain=domain,
            default_login_macro=macro_name(
                val("LOGIN_MACRO_SCRIPT", "login_bms.json", "DEFAULT_LOGIN_MACRO")
            ),
            logout_macro=macro_name(val("LOGOUT_MACRO_SCRIPT", "logout.json")),
            close_menu_macro=macro_name(val("CLOSE_MENU_MACRO", "close_menu.json")),
            auto_logout=boolean(val("ENABLE_AUTO_LOGOUT", "False")),
            reply_unknown=boolean(val("REPLY_UNKNOWN_COMMANDS", "False")),
            settle_delay=float(val("MACRO_SETTLE_DELAY", "2.0")),
            detector_interval=float(val("DETECTOR_INTERVAL_SEC", "10")),
            confidence=float(val("CONFIDENCE_THRESHOLD", "0.8")),
            internal_api_token=val("INTERNAL_API_TOKEN"),
            timezone=val("TIMEZONE", "Asia/Bangkok"),
            expected_width=int(val("DESKTOP_WIDTH", "1920")),
            expected_height=int(val("DESKTOP_HEIGHT", "1080")),
            max_macro_seconds=float(val("MAX_MACRO_SECONDS", "35")),
            image_ttl_seconds=int(val("IMAGE_TTL_SECONDS", "604800")),
            login_wait_seconds=float(val("LOGIN_WAIT_SECONDS", "5")),
        )
        ZoneInfo(cfg.timezone)
        if (
            not 1 <= cfg.port <= 65535
            or min(cfg.expected_width, cfg.expected_height) < 1
        ):
            raise ValueError("Invalid port or desktop dimensions.")
        for number in (
            cfg.settle_delay,
            cfg.detector_interval,
            cfg.confidence,
            cfg.max_macro_seconds,
            cfg.login_wait_seconds,
        ):
            if not math.isfinite(number) or number < 0:
                raise ValueError(
                    "Timing and confidence settings must be finite and nonnegative."
                )
        if (
            not 0 < cfg.confidence <= 1
            or cfg.max_macro_seconds <= 0
            or cfg.image_ttl_seconds <= 0
        ):
            raise ValueError("Invalid confidence, macro duration, or image TTL.")
        return cfg


class RuntimeConfig:
    def __init__(self, settings: Settings):
        self._settings = settings
        self.lock = threading.RLock()

    def snapshot(self) -> Settings:
        with self.lock:
            return self._settings

    def _persist(self, key: str, value: str, **changes):
        with self.lock:
            path = self._settings.project_dir / ".env"
            if path.is_symlink():
                raise ValueError(".env must not be a symlink.")
            set_key(str(path), key, value)
            self._settings = replace(self._settings, **changes)

    def set_login(self, name: str):
        self._persist("LOGIN_MACRO_SCRIPT", macro_name(name), default_login_macro=name)

    def set_autologout(self, enabled: bool):
        self._persist("ENABLE_AUTO_LOGOUT", str(enabled), auto_logout=enabled)

    def set_tunnel(self, url: str):
        self._persist(
            "PUBLIC_TUNNEL_URL", public_url(url), public_tunnel_url=public_url(url)
        )
