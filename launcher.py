"""Windows one-click bootstrap; installs packages before loading application imports."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def say(message):
    print(message, flush=True)


def ensure_env_file(project):
    """Exclusive creation preserves even an empty existing operator configuration."""
    path = project / ".env"
    try:
        with path.open("xb") as destination:
            destination.write((project / ".env.example").read_bytes())
    except FileExistsError:
        return False
    return True


def install_dependencies(project, python, *, runner=subprocess.run):
    marker = python.parent.parent / ".bms-requirements.sha256"
    fingerprint = hashlib.sha256(
        (project / "requirements.txt").read_bytes()
        + str(python).encode()
        + str(python.stat().st_mtime_ns).encode()
    ).hexdigest()
    cached = (
        marker.exists() and marker.read_text(encoding="ascii").strip() == fingerprint
    )
    if cached:
        result = runner([str(python), "-m", "pip", "check"], cwd=project, check=False)
        if result.returncode == 0:
            say("Dependencies already prepared.")
            return
    # A failed refresh must never retain a marker claiming this environment is ready.
    marker.unlink(missing_ok=True)
    say(
        "Installing dependencies. The first launch needs Internet access and may take a few minutes."
    )
    runner(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip",
            "--disable-pip-version-check",
        ],
        cwd=project,
        check=True,
    )
    runner(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--only-binary=numpy,opencv-python-headless,pillow",
            "-r",
            "requirements.txt",
        ],
        cwd=project,
        check=True,
    )
    runner([str(python), "-m", "pip", "check"], cwd=project, check=True)
    marker.write_text(fingerprint, encoding="ascii")


def validate_interpreter(info):
    if not (3, 11) <= tuple(info["version"]) < (3, 15):
        raise RuntimeError(
            "Use standard Python 3.14 or 3.12; supported versions are 3.11 through 3.14."
        )
    if info["platform"] != "win-amd64" or info["bits"] != 64 or info["free_threaded"]:
        raise RuntimeError(
            "Use standard Windows x64 Python, not 32-bit, ARM64, or free-threaded Python."
        )


def interpreter_info(python):
    script = (
        "import json,sys,sysconfig,struct; "
        'print(json.dumps({"version":list(sys.version_info[:2]),'
        '"platform":sysconfig.get_platform(),"bits":struct.calcsize("P")*8,'
        '"free_threaded":bool(sysconfig.get_config_var("Py_GIL_DISABLED"))}))'
    )
    result = subprocess.run(
        [str(python), "-c", script], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


@contextmanager
def launcher_lock(project):
    folder = project / ".runtime"
    folder.mkdir(exist_ok=True)
    handle = (folder / "launcher.lock").open("a+b")
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(
                "Another launcher is already running. Use its window or close it first."
            ) from None
        yield
    finally:
        handle.close()


def bootstrap(project):
    validate_interpreter(interpreter_info(sys.executable))
    python = project / ".venv-launcher" / "Scripts" / "python.exe"
    if not python.exists():
        say("Creating the launcher virtual environment...")
        subprocess.run(
            [sys.executable, "-m", "venv", str(python.parent.parent)],
            cwd=project,
            check=True,
        )
    try:
        validate_interpreter(interpreter_info(python))
    except (RuntimeError, subprocess.SubprocessError, ValueError):
        raise RuntimeError(
            "The .venv-launcher environment is incompatible or damaged. "
            "Rename that folder, then launch again. Your .env and BMS files are preserved."
        ) from None
    install_dependencies(project, python)
    child = subprocess.Popen(
        [str(python), str(project / "launcher.py"), "--run"], cwd=project
    )
    try:
        return child.wait()
    except KeyboardInterrupt:
        # The child shares this console and also receives Ctrl+C. Keep the
        # launcher lock while it drains its bot and stops its own tunnel.
        return child.wait()


def find_tunnel(data, port, expected_domain=""):
    if not isinstance(data, dict) or not isinstance(data.get("tunnels"), list):
        return None
    for tunnel in data["tunnels"]:
        if not isinstance(tunnel, dict) or not isinstance(tunnel.get("config"), dict):
            continue
        url, addr = tunnel.get("public_url"), tunnel["config"].get("addr")
        if not isinstance(url, str) or not isinstance(addr, str):
            continue
        try:
            remote = urlsplit(url)
            local = urlsplit(addr if "://" in addr else "http://" + addr)
            if (
                remote.scheme == "https"
                and remote.hostname
                and not remote.username
                and not remote.password
                and remote.path in ("", "/")
                and local.scheme == "http"
                and local.hostname in ("localhost", "127.0.0.1", "[::1]", "::1")
                and local.port == port
                and (not expected_domain or remote.hostname == expected_domain)
            ):
                return url.rstrip("/")
        except ValueError:
            continue
    return None


def read_tunnels():
    # This is the official local ngrok inspector, not an external network request.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:4040/api/tunnels", timeout=2) as response:
            return json.loads(response.read(1_000_000))
    except (OSError, ValueError):
        return None


def stop_owned_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def start_tunnel(cfg, project):
    domain = cfg.ngrok_domain or cfg.public_tunnel_url
    if domain:
        domain = urlsplit(domain if "://" in domain else "https://" + domain).hostname
    existing = read_tunnels()
    url = find_tunnel(existing, cfg.port, domain or "")
    if url:
        say(
            "Using your existing ngrok tunnel. The launcher will leave that process running."
        )
        return url, None
    if existing is not None:
        raise RuntimeError(
            "An ngrok inspector is already running on port 4040 for a different tunnel. "
            "Configure it for this bot port/domain, or stop that agent yourself and try again."
        )
    configured_path = cfg.ngrok_exe_path.strip()
    if configured_path:
        executable = Path(os.path.expandvars(configured_path)).expanduser()
        if not executable.is_absolute():
            executable = project / executable
        if not executable.is_file():
            raise RuntimeError(
                "NGROK_EXE_PATH does not point to a file. Enter the full path to ngrok.exe "
                "in .env using single quotes, then launch again."
            )
        binary = str(executable.resolve())
    else:
        binary = shutil.which("ngrok")
        if binary is None and (project / "ngrok.exe").is_file():
            binary = str(project / "ngrok.exe")
    if binary is None:
        raise RuntimeError(
            "Install the official ngrok CLI from https://ngrok.com/download. "
            "Set NGROK_EXE_PATH in .env, add it to PATH, or put ngrok.exe beside "
            "start_bot.bat, then launch again."
        )
    from dotenv import dotenv_values

    environment = os.environ.copy()
    auth = environment.get("NGROK_AUTHTOKEN") or dotenv_values(project / ".env").get(
        "NGROK_AUTHTOKEN"
    )
    if auth:
        environment["NGROK_AUTHTOKEN"] = auth
    args = [binary, "http", str(cfg.port)]
    if domain:
        args.append("--domain=" + domain)
    say("Starting ngrok in a separate console...")
    process = subprocess.Popen(
        args,
        cwd=project,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )
    try:
        stop_at = time.monotonic() + 30
        while time.monotonic() < stop_at:
            if process.poll() is not None:
                raise RuntimeError(
                    "ngrok stopped. Check its console for an authentication or domain error."
                )
            url = find_tunnel(read_tunnels(), cfg.port, domain or "")
            if url:
                return url, process
            time.sleep(0.25)
        raise RuntimeError(
            "ngrok did not expose an HTTPS tunnel within 30 seconds. Check its console."
        )
    except BaseException:
        stop_owned_process(process)
        raise


def prepare_configuration(project):
    from config import Settings

    created = ensure_env_file(project)
    cfg = Settings.load(project)
    if created or not cfg.channel_access_token or not cfg.channel_secret:
        say(
            "First-time setup: enter your LINE token/secret and ngrok settings in .env."
        )
        say(
            "Keep auto-logout disabled while testing. Save Notepad, then return to this window."
        )
        subprocess.Popen(["notepad.exe", str(project / ".env")])
        input("Press Enter after saving .env: ")
        cfg = Settings.load(project)
    if not cfg.channel_access_token or not cfg.channel_secret:
        raise RuntimeError(
            "CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET are still missing in .env."
        )
    if not cfg.group_id:
        say(
            "GROUP_ID is blank: use check-id in your LINE group, add the returned ID to .env, then relaunch."
        )
    required = [
        cfg.macros_dir / cfg.default_login_macro,
        cfg.logged_in_anchor,
        cfg.logged_out_anchor,
    ]
    if cfg.auto_logout:
        required.append(cfg.macros_dir / cfg.logout_macro)
    missing = [p.relative_to(project).as_posix() for p in required if not p.is_file()]
    if missing:
        say("Desktop capture is not configured yet. Add these operator-recorded files:")
        for path in missing:
            say("  " + path)
        say(
            "The bot can still start for LINE setup/check-id. See README.md for macro and image instructions."
        )
    return cfg


def run_application(project):
    say("Running application tests...")
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=project,
        check=True,
    )
    cfg = prepare_configuration(project)
    owned_ngrok = None
    server = None
    try:
        url, owned_ngrok = start_tunnel(cfg, project)
        from config import RuntimeConfig

        RuntimeConfig(cfg).set_tunnel(url)
        say("\nIn LINE Developers, set the webhook URL to: " + url + "/callback")
        say(
            "Enable Use webhook and Webhook redelivery. These settings are done once for a stable domain."
        )
        say(
            "Starting the bot. Keep this window open; Ctrl+C stops it and the ngrok process this launcher started.\n"
        )
        server_environment = os.environ.copy()
        server_environment["NGROK_DOMAIN"] = urlsplit(url).hostname
        server_environment.pop("PUBLIC_TUNNEL_URL", None)
        server = subprocess.Popen(
            [sys.executable, str(project / "server.py")],
            cwd=project,
            env=server_environment,
        )
        return server.wait()
    except KeyboardInterrupt:
        say(
            "\nStopping. Waiting for the bot to finish its current desktop transaction..."
        )
        if server is not None:
            try:
                server.wait(timeout=45 + cfg.max_macro_seconds + cfg.settle_delay + 10)
            except subprocess.TimeoutExpired:
                stop_owned_process(server)
        return 0
    finally:
        stop_owned_process(server)
        stop_owned_process(owned_ngrok)


def main():
    say("=" * 64 + "\nBMS AUTOMATION LINE BOT - ONE-CLICK LAUNCHER\n" + "=" * 64)
    try:
        if sys.platform != "win32":
            raise RuntimeError(
                "Double-click start_bot.bat on your Windows PC. This launcher is Windows-only."
            )
        if "--run" in sys.argv:
            return run_application(ROOT)
        with launcher_lock(ROOT):
            return bootstrap(ROOT)
    except KeyboardInterrupt:
        say("\nLauncher canceled.")
        return 130
    except subprocess.CalledProcessError:
        say(
            "\nA setup/test command failed. Read its error above; the bot was not started."
        )
        return 1
    except (OSError, ValueError, RuntimeError) as exc:
        say("\nSETUP REQUIRED: " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
