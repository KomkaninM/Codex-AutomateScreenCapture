"""Signed Flask ingress, bounded background dispatch, and operator startup dashboard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import signal
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from capture import CaptureEngine
from commands import Bot
from config import RuntimeConfig, Settings, macro_name
from detector import SessionGuard, VisualDetector
from event_ledger import EventLedger
from line_api import LineAPI
from macro_player import MacroPlayer
from scheduler import Scheduler
from workflow import Workflow

log = logging.getLogger(__name__)


class Dispatcher:
    """Bound both running and queued tasks; overload requests can be redelivered."""

    def __init__(self, workers=8, capacity=64):
        self.pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="bms-worker"
        )
        self.slots = threading.BoundedSemaphore(capacity)
        self.lock = threading.Lock()
        self.closed = False

    def submit(self, fn):
        with self.lock:
            if self.closed or not self.slots.acquire(blocking=False):
                raise RuntimeError("Background queue is full or closed.")
            try:
                future = self.pool.submit(fn)
            except BaseException:
                self.slots.release()
                raise
            future.add_done_callback(lambda _: self.slots.release())
            return future

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)


def load_targets(path: Path):
    if not path.exists():
        return {}
    if path.stat().st_size > 1_000_000:
        raise ValueError("targets.json is too large.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("targets.json must be a list.")
    result = {}
    for target in data:
        if (
            not isinstance(target, dict)
            or not isinstance(target.get("id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", target["id"])
        ):
            raise ValueError("Each target needs a safe id.")
        if target["id"].upper() in {key.upper() for key in result}:
            raise ValueError("Target ids must be unique (case-insensitive).")
        macro_name(target.get("macro"))
        if not isinstance(target.get("name"), str) or not target["name"]:
            raise ValueError("Target needs a descriptive name.")
        result[target["id"]] = target
    return result


def build_bot(runtime, dispatcher):
    cfg = runtime.snapshot()
    player = MacroPlayer(
        cfg.macros_dir,
        expected_size=(cfg.expected_width, cfg.expected_height),
        max_seconds=cfg.max_macro_seconds,
    )
    detector = VisualDetector(cfg.logged_out_anchor, confidence=cfg.confidence)
    guard = SessionGuard(
        detector,
        player,
        settle_delay=cfg.settle_delay,
        wait_seconds=cfg.login_wait_seconds,
    )
    workflow = Workflow(
        runtime,
        player,
        guard,
        CaptureEngine(
            cfg.screenshots_dir, expected_size=(cfg.expected_width, cfg.expected_height)
        ),
        load_targets(cfg.project_dir / "targets.json"),
    )
    scheduler = Scheduler(dispatcher.submit)
    return Bot(runtime, workflow, LineAPI(cfg.channel_access_token), scheduler)


def create_app(runtime=None, *, bot=None, dispatcher=None):
    runtime = runtime or RuntimeConfig(Settings.load())
    dispatcher = dispatcher or Dispatcher()
    bot = bot or build_bot(runtime, dispatcher)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000
    app.extensions.update(bms_runtime=runtime, bms_dispatcher=dispatcher, bms_bot=bot)
    ledger = EventLedger(runtime.snapshot().project_dir / ".runtime")
    ingress_lock = threading.Lock()

    @app.get("/health")
    def health():
        return jsonify(status="ok", worker_closed=dispatcher.closed)

    @app.post("/callback")
    def callback():
        cfg = runtime.snapshot()
        if not cfg.channel_secret:
            return jsonify(error="Webhook authentication is not configured."), 503
        body = request.get_data()
        expected = base64.b64encode(
            hmac.new(cfg.channel_secret.encode(), body, hashlib.sha256).digest()
        ).decode()
        if not hmac.compare_digest(
            expected.encode(), request.headers.get("X-Line-Signature", "").encode()
        ):
            return jsonify(error="Invalid signature."), 401
        try:
            payload = json.loads(body)
            events = payload["events"]
            if not isinstance(events, list) or len(events) > 100:
                raise ValueError()
            for event in events:
                if not isinstance(event, dict):
                    raise ValueError()
                if event.get("type") == "message":
                    source, message = event.get("source"), event.get("message")
                    if not isinstance(source, dict) or not isinstance(message, dict):
                        raise ValueError()
                    if message.get("type") == "text":
                        if (
                            not isinstance(message.get("text"), str)
                            or len(message["text"]) > 5000
                        ):
                            raise ValueError()
                        if not isinstance(event.get("replyToken", ""), str):
                            raise ValueError()
                        if not isinstance(source.get("groupId", ""), str):
                            raise ValueError()
                        if not isinstance(event.get("webhookEventId", ""), str):
                            raise ValueError()
        except (ValueError, KeyError, TypeError):
            return jsonify(error="Invalid event payload."), 400
        received_at = time.monotonic()
        with ingress_lock:
            for index, event in enumerate(events):
                if (
                    event.get("type") != "message"
                    or event.get("message", {}).get("type") != "text"
                ):
                    continue
                source = event["source"]
                if source.get("type") != "group" or not source.get("groupId"):
                    continue
                if cfg.group_id:
                    if source["groupId"] != cfg.group_id:
                        continue
                elif event["message"]["text"].strip().lower() != "check-id":
                    continue
                if not event.get("replyToken"):
                    continue
                key = event.get("webhookEventId") or event["replyToken"]
                key = hashlib.sha256(key.encode()).hexdigest()
                try:
                    if not ledger.claim(key):
                        continue
                except sqlite3.Error:
                    return (
                        jsonify(error="Webhook ledger unavailable; retry delivery."),
                        503,
                    )
                stopped_count = None
                schedule_generation = bot.schedule_generation()
                text = event["message"]["text"].strip().lower()
                if text == "stop-capture":
                    # A bounded, non-UI control operation: cancellation bypasses
                    # occupied workers; only its network reply is asynchronous.
                    stopped_count = bot.cancel_schedules()
                try:
                    dispatcher.submit(
                        lambda e=event, t=received_at, g=schedule_generation, n=stopped_count: bot.handle(
                            e, t, schedule_generation=g, stopped_count=n
                        )
                    )
                except RuntimeError:
                    if stopped_count is not None:
                        # Cancellation already succeeded. Retain the durable claim so
                        # redelivery cannot cancel schedules registered after this stop.
                        log.warning(
                            "Schedules canceled; acknowledgement queue was full."
                        )
                        continue
                    try:
                        ledger.release(key)
                    except sqlite3.Error:
                        log.error("Could not release failed webhook queue claim.")
                    # Previously accepted events are deduplicated when this batch is redelivered.
                    return jsonify(error="Background queue full; retry delivery."), 503
        return jsonify(status="accepted"), 200

    @app.post("/internal/update-tunnel")
    def update_tunnel():
        cfg = runtime.snapshot()
        if not cfg.internal_api_token or not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(),
            f"Bearer {cfg.internal_api_token}".encode(),
        ):
            return jsonify(error="Invalid authorization."), 401
        data = request.get_json(silent=True)
        url = (
            data.get("url", data.get("PUBLIC_TUNNEL_URL"))
            if isinstance(data, dict)
            else None
        )
        if not isinstance(url, str):
            return jsonify(error="Provide url as an HTTPS origin."), 400
        try:
            runtime.set_tunnel(url)
        except ValueError:
            return jsonify(error="Invalid public HTTPS tunnel URL."), 400
        except OSError:
            log.error("Could not persist tunnel update.")
            return jsonify(error="Could not persist tunnel configuration."), 500
        return jsonify(status="updated"), 200

    @app.get("/images/<path:filename>")
    def image_file(filename):
        cfg = runtime.snapshot()
        root = cfg.screenshots_dir.resolve()
        path = (root / filename).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            abort(404)
        if not re.fullmatch(
            r"shot_\d{8}_\d{6}_\d{3}_[a-f0-9]{32}_(?:line\.(?:jpg|webp)|preview\.jpg)",
            path.name,
        ):
            abort(404)
        if (
            cfg.image_ttl_seconds > 0
            and time.time() - path.stat().st_mtime > cfg.image_ttl_seconds
        ):
            abort(404)
        response = send_file(path, conditional=True, max_age=3600)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    return app


def startup_banner(cfg, line):
    border = "=" * 70
    print(
        f"{border}\n🤖 BMS AUTOMATION LINE BOT - STARTUP STATUS\n{border}", flush=True
    )
    print(
        "[CONFIG]\n"
        f" • Port:                  {cfg.port}\n"
        f' • ngrok Domain:          {cfg.public_tunnel_url or cfg.ngrok_domain or "not configured"}\n'
        f' • Active Group ID:       {cfg.group_id or "not configured (check-id discovery only)"}\n'
        f" • Default Login Macro:   {cfg.default_login_macro}\n"
        f" • Auto Logout:           {cfg.auto_logout} ({cfg.logout_macro})\n"
        f" • Reply Unknown Cmds:    {cfg.reply_unknown}\n\n[LINE MESSAGING QUOTA]",
        flush=True,
    )
    try:
        quota = line.quota()
        print(
            f" • Monthly Limit:         {quota['limit'] if quota['limit'] is not None else 'unlimited'} messages\n"
            f" • Consumed This Month:   {quota['consumed']} messages\n"
            f" • Remaining Balance:     {quota['remaining'] if quota['remaining'] is not None else 'unlimited'} messages",
            flush=True,
        )
    except Exception as exc:
        print(
            f" • Quota:                 unavailable ({type(exc).__name__}); check LINE credentials/network",
            flush=True,
        )
    try:
        reach = line.group_reach(cfg.group_id)
        print(
            f' • Target Reach (Group):  {reach if reach is not None else "unavailable"} members (upper bound; unblocked reach unavailable)',
            flush=True,
        )
    except Exception:
        print(
            " • Target Reach (Group):  unavailable for this account/group", flush=True
        )
    print(
        f"{border}\n🚀 Listening for webhooks on 0.0.0.0:{cfg.port}...\n{border}",
        flush=True,
    )


@contextmanager
def single_instance(project_dir):
    """Cross-platform OS lock prevents a second bot process sharing the desktop."""
    folder = Path(project_dir) / ".runtime"
    folder.mkdir(exist_ok=True)
    path = folder / "bot.lock"
    if path.is_symlink():
        raise RuntimeError("Runtime lock cannot be a symlink.")
    handle = path.open("a+b")
    try:
        import sys

        if sys.platform == "win32":
            import msvcrt

            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except (BlockingIOError, OSError) as exc:
        raise RuntimeError(
            "Could not acquire bot instance lock; stop other bot processes."
        ) from exc
    finally:
        handle.close()


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = Settings.load()
    if not cfg.channel_secret or not cfg.channel_access_token:
        raise SystemExit(
            "Configure CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET in the project .env."
        )
    with single_instance(cfg.project_dir):
        dispatcher = Dispatcher()
        bot = build_bot(RuntimeConfig(cfg), dispatcher)
        app = create_app(bot.runtime, bot=bot, dispatcher=dispatcher)
        from waitress import create_server

        http = create_server(
            app,
            host="0.0.0.0",
            port=cfg.port,
            threads=4,
            clear_untrusted_proxy_headers=True,
        )

        def stop(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        try:
            startup_banner(cfg, bot.line)
            bot.scheduler.start()
            http.run()
        except KeyboardInterrupt:
            log.info("Stopping scheduler and draining desktop transactions.")
        finally:
            http.close()
            bot.scheduler.close()
            dispatcher.close()


if __name__ == "__main__":
    main()
